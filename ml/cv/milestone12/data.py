"""Read-only METU loading and deterministic 224-pixel batch preparation."""

from collections import Counter
import hashlib
from io import BytesIO
import json
from pathlib import Path, PurePosixPath
import random

import numpy as np
from PIL import Image


EXPECTED = {
    "train": {"crack": 15727, "no_crack": 14784},
    "validation": {"crack": 1965, "no_crack": 1848},
    "test": {"crack": 1966, "no_crack": 1848},
}


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def dataset_root(ml):
    return ml / "processed" / "metu-concrete-cracks" / "v2"


def load_split(ml, config, fold):
    if fold not in EXPECTED:
        raise ValueError(f"Partition is outside the Milestone 12 loader: {fold}")
    base = dataset_root(ml)
    if base.resolve() != base or (base / "images").resolve() != base / "images":
        raise ValueError("Dataset root or image directory is redirected")
    split = base / config["split"]
    if split.resolve() != split:
        raise ValueError("Split manifest directory is redirected")
    report_bytes = (split / "split_report.json").read_bytes()
    if hashlib.sha256(report_bytes).hexdigest() != config["split_report_sha256"]:
        raise ValueError("Frozen split report hash mismatch")
    report = json.loads(report_bytes)
    if report["version"] != config["split"] or report["parent_photo_independence"] != "unverified":
        raise ValueError("Frozen split identity or scope differs from the approved one")
    name = f"{fold}.jsonl"
    content = (split / name).read_bytes()
    actual_hash = hashlib.sha256(content).hexdigest()
    if actual_hash != report["manifest_sha256"][name]:
        raise ValueError(f"Frozen manifest hash mismatch: {name}")
    rows = [json.loads(line) for line in content.splitlines()]
    if Counter(row["label"] for row in rows) != EXPECTED[fold]:
        raise ValueError(f"Unexpected {fold} class counts")
    seen = set()
    for row in rows:
        relative = PurePosixPath(row["path"])
        if (
            row["split"] != fold
            or len(relative.parts) != 3
            or relative.parts[:2] != ("images", row["label"])
            or relative.suffix.lower() != ".jpg"
            or relative.name != PurePosixPath(row["archive_member"]).name
            or "\\" in row["path"]
            or ":" in row["path"]
            or row["path"] in seen
        ):
            raise ValueError(f"Unexpected image entry in {fold}: {row['path']}")
        seen.add(row["path"])
        row["file"] = base.joinpath(*relative.parts)
        row["target"] = config["label_order"].index(row["label"])
    for label in config["label_order"]:
        directory = base / "images" / label
        if directory.resolve() != directory:
            raise ValueError(f"Image class directory is redirected: {directory}")
    return rows, actual_hash


def decode(row, size, flip):
    path = row["file"]
    if path.is_symlink():
        raise ValueError(f"Redirected image: {row['path']}")
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != row["source_sha256"]:
        raise ValueError(f"Frozen JPEG changed: {row['path']}")
    with Image.open(BytesIO(payload)) as original:
        if original.format != "JPEG" or original.mode != "RGB" or original.size != (227, 227):
            raise ValueError(f"Unexpected source image format: {row['path']}")
        shaped = original.resize((size, size), Image.Resampling.BILINEAR)
        if flip:
            shaped = shaped.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        return np.array(shaped, dtype=np.uint8)


def batches(rows, batch_size, config, workers, *, epoch=None):
    # Seed the epoch ordering and flips before concurrent JPEG decoding.
    rng = random.Random(config["seed"] + (epoch or 0))
    order = list(range(len(rows)))
    if epoch is not None:
        rng.shuffle(order)
    size = config["image_size"]
    flip_probability = config["train_horizontal_flip_probability"]

    def submit(start):
        chosen = [rows[i] for i in order[start : start + batch_size]]
        futures = [
            workers.submit(decode, row, size, epoch is not None and rng.random() < flip_probability)
            for row in chosen
        ]
        return chosen, futures

    batch, pending = submit(0)
    for start in range(0, len(order), batch_size):
        images = np.stack([future.result() for future in pending])
        targets = np.array([row["target"] for row in batch], dtype=np.int64)
        if start + batch_size < len(order):
            batch, pending = submit(start + batch_size)
        yield images, targets
