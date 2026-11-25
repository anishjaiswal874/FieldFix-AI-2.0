"""Create an isolated CUDA experiment and guard the one-time METU test."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import gc
from importlib.metadata import distributions
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
import uuid

os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")

import numpy as np
import torch

from . import core, data


HERE = Path(__file__).resolve().parent
RUN_PREFIX = "metu-resnet18-m12-v"
WEIGHT_NAME = "resnet18-f37072fd.pth"


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def local_root():
    path = Path(os.environ["LOCALAPPDATA"]).resolve() / "FieldFixAI" / "ml"
    if path.resolve() != path:
        raise ValueError("Local ML directory is redirected")
    return path


def runs_root(ml):
    path = ml / "runs" / "cv"
    if path.resolve() != path:
        raise ValueError("CV runs directory is redirected")
    return path


def new_run(ml):
    parent = runs_root(ml)
    parent.mkdir(parents=True, exist_ok=True)
    number = 1
    while True:
        path = parent / f"{RUN_PREFIX}{number}"
        try:
            path.mkdir()
            return path
        except FileExistsError:
            number += 1


def ensure_run(ml, path):
    result = path.resolve(strict=True)
    if result.parent != runs_root(ml) or not result.name.startswith(RUN_PREFIX):
        raise ValueError("Run must be a versioned Milestone 12 directory")
    return result


def json_bytes(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def publish(path, write):
    # Windows rename fails if the destination exists. Leave incomplete files for diagnosis.
    if path.exists():
        raise FileExistsError(path)
    pending = path.with_name(f".{path.name}.{uuid.uuid4().hex}.pending")
    with pending.open("xb") as stream:
        write(stream)
        stream.flush()
        os.fsync(stream.fileno())
    pending.rename(path)


def save_json(path, value):
    publish(path, lambda stream: stream.write(json_bytes(value)))


def save_tensors(path, value):
    publish(path, lambda stream: torch.save(value, stream))


def recorded_file(path):
    return {"bytes": path.stat().st_size, "sha256": data.digest(path)}


def source_code():
    return {name: data.digest(HERE / name) for name in ("run.py", "data.py", "core.py", "config.json", "requirements.txt")}


def verify_backup(ml, path):
    receipt = path.resolve(strict=True)
    if receipt.name != "verification.json" or receipt.parent.parent != ml.parent / "backups":
        raise ValueError("The approved backup receipt must be under Local AppData FieldFixAI/backups")
    record = json.loads(receipt.read_bytes())
    archive = receipt.parent / record["archive_name"]
    if (
        record["status"] != "verified"
        or archive.parent != receipt.parent
        or archive.stat().st_size != record["archive_bytes"]
        or data.digest(archive) != record["archive_sha256"]
    ):
        raise ValueError("The pre-training backup no longer matches its verified receipt")
    return {"path": str(archive), "sha256": record["archive_sha256"], "members": record["files"]}


def environment(gpu):
    driver = subprocess.run(
        ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
        capture_output=True, text=True, check=False, timeout=20,
    )
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "gpu_smoke": gpu,
        "nvidia_driver": driver.stdout.strip() if driver.returncode == 0 else None,
        "packages": dict(sorted((package.metadata["Name"], package.version) for package in distributions())),
    }


def capacity_try(train, config, workers, device, weights, batch_size):
    core.seed_everything(config["seed"])
    scratch = core.from_cached_weights(weights, config["weights_sha256"], device)
    torch.cuda.reset_peak_memory_stats(device)
    return core.batch_probe(scratch, train, config, workers, device, batch_size)


def choose_batch(train, config, workers, device, weights, force_small):
    attempts = []
    sizes = [config["oom_batch_size"]] if force_small else [config["batch_size"], config["oom_batch_size"]]
    for size in sizes:
        try:
            resource = capacity_try(train, config, workers, device, weights, size)
        except torch.cuda.OutOfMemoryError as failure:
            attempts.append({"batch_size": size, "status": "cuda_out_of_memory", "message": str(failure)})
        else:
            attempts.append({"batch_size": size, "status": "passed", **resource})
            gc.collect()
            torch.cuda.empty_cache()
            return size, attempts
        gc.collect()
        torch.cuda.empty_cache()
    raise RuntimeError(f"Both approved CUDA batch-size probes failed: {attempts}")


def frozen_batchnorm(network):
    return {
        name: value.detach().cpu().clone()
        for name, value in network.named_buffers()
        if name.endswith(("running_mean", "running_var", "num_batches_tracked"))
    }


def train(ml, backup_receipt, force_small):
    config = json.loads((HERE / "config.json").read_bytes())
    backup = verify_backup(ml, backup_receipt)
    device, gpu = core.cuda_smoke()
    print("GPU_SMOKE " + json.dumps(gpu), flush=True)
    run = new_run(ml)
    print(f"RUN_DIR={run}", flush=True)
    (run / "epochs").mkdir()
    (run / "checkpoints").mkdir()
    try:
        weights = Path.home() / ".cache" / "torch" / "hub" / "checkpoints" / WEIGHT_NAME
        training, train_hash = data.load_split(ml, config, "train")
        validation, validation_hash = data.load_split(ml, config, "validation")
        save_json(run / "config.json", config)
        save_json(run / "environment.json", environment(gpu))
        save_json(run / "source.json", {
            "backup": backup, "metu_root": str(data.dataset_root(ml)),
            "split_report_sha256": config["split_report_sha256"],
            "train_manifest_sha256": train_hash, "validation_manifest_sha256": validation_hash,
            "cached_weights_path": str(weights), "cached_weights_sha256": config["weights_sha256"],
            "train_support": len(training), "validation_support": len(validation),
            "source_code_sha256": source_code(),
        })
        with ThreadPoolExecutor(max_workers=config["jpeg_workers"]) as workers:
            batch_size, attempts = choose_batch(training, config, workers, device, weights, force_small)
            save_json(run / "batch-probe.json", {"batch_size": batch_size, "attempts": attempts})
            print(f"CUDA fine-tuning memory probe passed at batch size {batch_size}.", flush=True)
            core.seed_everything(config["seed"])
            network = core.from_cached_weights(weights, config["weights_sha256"], device)
            original_bn = frozen_batchnorm(network)
            started = time.perf_counter()
            start_utc = now()
            history = []
            rank = (-1.0, float("-inf"))
            best = None
            stale = 0
            stage = None
            for epoch in range(1, config["head_epochs"] + config["fine_tune_epochs"] + 1):
                selected_stage = "head" if epoch <= config["head_epochs"] else "fine_tune"
                if selected_stage != stage:
                    stage = selected_stage
                    optimizer = core.make_optimizer(network, config, stage)
                torch.cuda.reset_peak_memory_stats(device)
                training_metrics = core.train_epoch(network, optimizer, training, batch_size, config, workers, device, epoch, stage)
                truth, scores, validation_run = core.score_rows(network, validation, batch_size, config, workers, device)
                validation_metrics = {
                    **validation_run, **core.binary_metrics(truth, scores, config["validation_checkpoint_threshold"], config["label_order"]),
                }
                for name, current in frozen_batchnorm(network).items():
                    if not torch.equal(current, original_bn[name]):
                        raise RuntimeError(f"BatchNorm buffer unexpectedly changed: {name}")
                resource = {**core.gpu_memory(device), "peak_process_working_set_bytes": core.host_peak_bytes()}
                checkpoint = run / "checkpoints" / f"epoch-{epoch:02d}.pt"
                save_tensors(checkpoint, {
                    "epoch": epoch, "stage": stage, "model_state": network.state_dict(),
                    "optimizer_state": optimizer.state_dict(), "seed": config["seed"],
                    "cpu_rng_state": torch.get_rng_state(), "cuda_rng_state": torch.cuda.get_rng_state(device),
                    "label_order": config["label_order"], "batch_size": batch_size,
                })
                log = {
                    "epoch": epoch, "stage": stage, "learning_rates": [unit["lr"] for unit in optimizer.param_groups],
                    "training": training_metrics, "validation": validation_metrics, "resources": resource,
                    "checkpoint": {"path": checkpoint.relative_to(run).as_posix(), **recorded_file(checkpoint)},
                    "batchnorm_running_buffers_equal_to_initial": True,
                }
                save_json(run / "epochs" / f"epoch-{epoch:02d}.json", log)
                history.append(log)
                print(
                    f"EPOCH {epoch:02d} {stage} train loss {training_metrics['loss']:.6f}; "
                    f"validation loss {validation_metrics['loss']:.6f}, accuracy {validation_metrics['accuracy']:.4%}, "
                    f"macro-F1 {validation_metrics['macro_f1']:.6f}", flush=True,
                )
                candidate = validation_metrics["macro_f1"], -validation_metrics["loss"]
                if candidate > rank:
                    rank, best, best_truth, best_scores = candidate, log, truth, scores
                    stale = 0
                elif stage == "fine_tune":
                    stale += 1
                    if stale >= config["fine_tune_patience"]:
                        break

        elapsed = time.perf_counter() - started
        chosen, grid = core.threshold_search(best_truth, best_scores, config)
        save_json(run / "validation-threshold-search.json", {
            "method": "highest validation macro-F1, then nearest 0.50, then lower threshold",
            "selected": chosen, "grid": grid, "epoch": best["epoch"],
        })
        model_file = run / "resnet18-inference.pt"
        best_data = torch.load(run / best["checkpoint"]["path"], map_location="cpu", weights_only=True)
        save_tensors(model_file, {"model_state": best_data["model_state"], "label_order": config["label_order"]})
        save_json(run / "training-summary.json", {
            "started_utc": start_utc, "completed_utc": now(), "training_seconds": elapsed,
            "meaning": "after memory probe through the last saved epoch",
            "epochs_completed": len(history), "early_stopped": len(history) < config["head_epochs"] + config["fine_tune_epochs"],
            "batch_size": batch_size, "best_epoch": best["epoch"], "epoch_metrics": history,
            "peak_vram_allocated_bytes": max(row["resources"]["peak_vram_allocated_bytes"] for row in history),
            "peak_vram_reserved_bytes": max(row["resources"]["peak_vram_reserved_bytes"] for row in history),
            "peak_process_working_set_bytes": max((row["resources"]["peak_process_working_set_bytes"] or 0 for row in history), default=0),
        })
        save_json(run / "freeze.json", {
            "frozen_utc": now(), "best_epoch": best["epoch"], "best_checkpoint": best["checkpoint"],
            "selection": "validation macro-F1 at 0.50, then lower validation loss",
            "threshold": chosen["threshold"], "threshold_validation": chosen,
            "inference_model": {"name": model_file.name, **recorded_file(model_file)},
            "config_sha256": data.digest(run / "config.json"), "code_sha256": source_code(),
            "batch_size": batch_size, "evaluation_scope": config["evaluation_scope"],
            "parent_photo_independence": config["parent_photo_independence"],
        })
        print(f"FROZEN best epoch {best['epoch']}, crack threshold {chosen['threshold']:.2f}, train seconds {elapsed:.2f}", flush=True)
    except (Exception, KeyboardInterrupt) as error:
        save_json(run / "failure.json", {"timestamp_utc": now(), "type": type(error).__name__, "message": str(error)})
        raise


def load_frozen(run, device):
    freeze = json.loads((run / "freeze.json").read_bytes())
    config = json.loads((run / "config.json").read_bytes())
    model = run / freeze["inference_model"]["name"]
    if (
        freeze["code_sha256"] != source_code()
        or freeze["config_sha256"] != data.digest(run / "config.json")
        or freeze["inference_model"]["sha256"] != data.digest(model)
        or freeze["best_checkpoint"]["sha256"] != data.digest(run / freeze["best_checkpoint"]["path"])
    ):
        raise ValueError("Frozen code, preprocessing, or checkpoint hash changed")
    saved = torch.load(model, map_location="cpu", weights_only=True)
    if saved["label_order"] != config["label_order"]:
        raise ValueError("Frozen classifier label order mismatch")
    network = core.architecture()
    network.load_state_dict(saved["model_state"], strict=True)
    return config, freeze, network.to(device).eval()


def evaluate(ml, run):
    result = run / "provisional-test.json"
    claim = run / "test-claim.json"
    freeze_hash = data.digest(run / "freeze.json")
    if result.exists():
        saved = json.loads(result.read_bytes())
        if saved["freeze_sha256"] != freeze_hash:
            raise ValueError("Stored provisional test does not belong to this freeze")
        print("Reusing saved metrics. No test rescoring.", flush=True)
        return saved
    if claim.exists():
        raise ValueError("Provisional test scoring has already started. Preserved the receipt without rescoring.")
    device, gpu = core.cuda_smoke()
    config, freeze, network = load_frozen(run, device)
    test, manifest_hash = data.load_split(ml, config, "test")
    save_json(claim, {"started_utc": now(), "freeze_sha256": freeze_hash, "test_manifest_sha256": manifest_hash})
    torch.cuda.reset_peak_memory_stats(device)
    with ThreadPoolExecutor(max_workers=config["jpeg_workers"]) as workers:
        truth, scores, timing = core.score_rows(network, test, freeze["batch_size"], config, workers, device, measure=True)
    metrics = core.binary_metrics(truth, scores, freeze["threshold"], config["label_order"])
    saved = {
        "completed_utc": now(), "freeze_sha256": freeze_hash, "test_manifest_sha256": manifest_hash,
        "evaluation_scope": freeze["evaluation_scope"], "parent_photo_independence": freeze["parent_photo_independence"],
        "best_epoch": freeze["best_epoch"], "inference_model": freeze["inference_model"],
        "metrics": metrics, "scoring": timing, "resources": core.gpu_memory(device), "runtime": gpu,
        "predictions": [
            {"path": row["path"], "source_sha256": row["source_sha256"], "label": row["label"],
             "crack_score": float(scores[i]), "predicted": config["label_order"][int(scores[i] >= freeze["threshold"])]}
            for i, row in enumerate(test)
        ],
    }
    save_json(result, saved)
    return saved


def inference_smoke(ml, run):
    if (run / "inference-smoke.json").exists():
        return json.loads((run / "inference-smoke.json").read_bytes())
    device, _ = core.cuda_smoke()
    config, freeze, network = load_frozen(run, device)
    validation, _ = data.load_split(ml, config, "validation")
    examples = [next(row for row in validation if row["label"] == label) for label in config["label_order"]]
    with ThreadPoolExecutor(max_workers=2) as workers:
        _, scores, _ = core.score_rows(network, examples, len(examples), config, workers, device)
    report = {
        "scope": "two genuine validation examples; qualitative inference check",
        "threshold": freeze["threshold"],
        "examples": [
            {"path": row["path"], "label": row["label"], "crack_score": float(scores[i]),
             "predicted": config["label_order"][int(scores[i] >= freeze["threshold"])]}
            for i, row in enumerate(examples)
        ],
    }
    save_json(run / "inference-smoke.json", report)
    return report


def inventory(run):
    if (run / "artifact-manifest.json").exists():
        raise ValueError("Artifact manifest already exists")
    files = {
        path.relative_to(run).as_posix(): recorded_file(path)
        for path in sorted(run.rglob("*")) if path.is_file()
    }
    save_json(run / "artifact-manifest.json", {"created_utc": now(), "runs": run.name, "files": files})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("smoke-cuda")
    fit = commands.add_parser("train")
    fit.add_argument("--backup", type=Path, required=True)
    fit.add_argument("--batch16", action="store_true", help="Use approved smaller batch on a new run after CUDA OOM")
    for name in ("evaluate", "smoke-inference", "inventory"):
        command = commands.add_parser(name)
        command.add_argument("--run", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "smoke-cuda":
            _, report = core.cuda_smoke()
            print(json.dumps(report, indent=2))
            return
        ml = local_root()
        if args.command == "train":
            train(ml, args.backup, args.batch16)
            return
        run = ensure_run(ml, args.run)
        if args.command == "evaluate":
            result = evaluate(ml, run)
            print(json.dumps({key: value for key, value in result.items() if key != "predictions"}, indent=2))
        elif args.command == "smoke-inference":
            print(json.dumps(inference_smoke(ml, run), indent=2))
        else:
            inventory(run)
    except (OSError, ValueError, RuntimeError, KeyError, KeyboardInterrupt) as error:
        sys.exit(f"Milestone 12 stopped: {type(error).__name__}: {error}")


if __name__ == "__main__":
    main()
