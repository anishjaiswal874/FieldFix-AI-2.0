"""Screen one confirmed concrete JPEG using the frozen ResNet18."""

from importlib.metadata import version
from base64 import b64encode
from io import BytesIO
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ml.inference.worker_io import checked_file, emit, input_bytes, ml_root, watchdog


FREEZE_SHA = "1e5630da070858786e40de555323022542803f8cb211af697a92808ad234f439"
MODEL_SHA = "f98d6d635a260cbdfbdc9f23b2ac718fd074be93db2e1666023e75cceb94f027"
MAX_BYTES = 15 * 1024 * 1024
MAX_PIXELS = 16_000_000


def screen(jpeg: bytes, preview: bool = False):
    run = ml_root() / "runs/cv/metu-resnet18-m12-v1"
    freeze = json.loads(checked_file(run / "freeze.json", FREEZE_SHA, 8 * 1024))
    config = json.loads(checked_file(run / "config.json", freeze["config_sha256"], 8 * 1024))
    artifact = freeze["inference_model"]
    if artifact["sha256"] != MODEL_SHA or artifact["name"] != "resnet18-inference.pt" or freeze["threshold"] != 0.86:
        raise ValueError("Unexpected CV freeze")
    model_bytes = checked_file(run / artifact["name"], MODEL_SHA, artifact["bytes"])
    verified = {"torch": "2.12.1+cu126", "torchvision": "0.27.1+cu126", "Pillow": "12.2.0", "numpy": "2.4.4"}
    if any(version(package) != expected for package, expected in verified.items()):
        raise ValueError("Incompatible frozen CV environment")

    import numpy as np
    from PIL import Image
    import torch
    from ml.cv.milestone12 import core

    if not torch.cuda.is_available() or torch.version.cuda != "12.6":
        raise RuntimeError("The pilot requires the existing CUDA runtime")
    device = torch.device("cuda:0")
    with Image.open(BytesIO(jpeg), formats=["JPEG"]) as source:
        if source.width * source.height > MAX_PIXELS or getattr(source, "is_animated", False):
            raise ValueError("Invalid image eligibility")
        with source.convert("RGB") as rgb:
            quality = image_quality(rgb) if preview else None
            with rgb.resize((config["image_size"], config["image_size"]), Image.Resampling.BILINEAR) as resized:
                images = np.array(resized, dtype=np.uint8)[None, ...]
    saved = torch.load(BytesIO(model_bytes), map_location="cpu", weights_only=True)
    if saved["label_order"] != config["label_order"] or config["label_order"] != ["no_crack", "crack"]:
        raise ValueError("CV labels differ from the frozen order")
    model = core.architecture()
    model.load_state_dict(saved["model_state"], strict=True)
    model.to(device).eval()
    inputs = core.tensorize(images, device, core.normalization(config, device))
    if preview:
        return {**explain_screen(model, inputs, images[0], freeze["threshold"]), "quality": quality}
    with torch.inference_mode():
        return crack_score(model(inputs)) >= freeze["threshold"]


def crack_score(logits):
    import torch
    if not torch.isfinite(logits).all().item():
        raise ValueError("Invalid classifier output")
    return logits.softmax(dim=1)[0, 1].item()


def image_quality(rgb):
    import numpy as np
    from PIL import Image

    gray = rgb.convert("L")
    gray.thumbnail((512, 512), Image.Resampling.BILINEAR)
    values = np.asarray(gray, dtype=np.float64)
    laplacian = (values[:-2, 1:-1] + values[2:, 1:-1] + values[1:-1, :-2] +
                 values[1:-1, 2:] - 4 * values[1:-1, 1:-1])
    sharpness = float(laplacian.var()) if laplacian.size else 0.0
    exposure = float(values.mean())
    return {
        "width": rgb.width, "height": rgb.height, "sharpness": round(sharpness, 2),
        "brightness": round(exposure, 2), "blur": "pass" if sharpness >= 45 else "warning",
        "exposure": "pass" if 40 <= exposure <= 220 else "warning",
        "resolution": "pass" if min(rgb.size) >= 224 else "warning",
    }


def explain_screen(model, inputs, resized, threshold):
    import numpy as np
    from PIL import Image
    import torch
    from torch.nn import functional as F

    def jpeg(image):
        stream = BytesIO()
        image.save(stream, format="JPEG", quality=82)
        return b64encode(stream.getvalue()).decode("ascii")

    activations = []
    hook = model.layer4[-1].conv2.register_forward_hook(lambda _layer, _args, output: activations.append(output))
    try:
        model.requires_grad_(False)
        logits = model(inputs.requires_grad_(True))
        score = crack_score(logits)
        gradient, = torch.autograd.grad(logits[0, 1], activations[0])
    finally:
        hook.remove()
    heat = (activations[0].detach() * gradient.mean(dim=(2, 3), keepdim=True)).sum(dim=1, keepdim=True).relu()
    overlay = None
    if torch.isfinite(heat).all().item() and heat.max().item() > 0:
        heat = F.interpolate(heat / heat.max(), size=resized.shape[:2], mode="bilinear", align_corners=False)
        intensity = heat[0, 0].cpu().numpy()
        tint = np.stack((np.full_like(intensity, 255), 180 * (1 - intensity), np.zeros_like(intensity)), axis=-1)
        alpha = intensity[..., None] * 0.65
        canvas = BytesIO()
        Image.fromarray((resized * (1 - alpha) + tint * alpha).astype(np.uint8)).save(canvas, format="PNG")
        overlay = b64encode(canvas.getvalue()).decode("ascii")
    rgb = Image.fromarray(resized)
    normalized = inputs.detach()[0].permute(1, 2, 0).cpu().numpy()
    # Map standardized RGB values in [-3, 3] to visible display values.
    display = ((np.clip(normalized, -3, 3) + 3) * (255 / 6)).astype(np.uint8)
    return {"crack_detected": score >= threshold, "score": score, "threshold": threshold,
            "resized": jpeg(rgb), "grayscale": jpeg(rgb.convert("L")), "normalized": jpeg(Image.fromarray(display)),
            "grad_cam": overlay}


def main() -> int:
    timer = watchdog(24)
    try:
        preview = sys.argv[1:] == ["--preview"]
        result = screen(input_bytes(MAX_BYTES), preview=preview)
        emit(result if preview else {"crack_detected": result})
        return 0
    except Exception:
        return 1
    finally:
        timer.cancel()


if __name__ == "__main__":
    raise SystemExit(main())
