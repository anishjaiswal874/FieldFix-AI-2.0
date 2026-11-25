"""Explicit, read-only pilot smoke check. Does not open either held-out split."""

import json
import os
from pathlib import Path
import sys
from time import perf_counter
from unittest.mock import patch

from ml.inference.worker_io import checked_file, ml_root


TRAIN_REPORT_SHA = "688e567e38fc6b7ec4fb8055eef3b1994efc84a8ebc9bcb45b3072ba6e91e54f"
ROOT = Path(__file__).resolve().parents[2]


def cv_examples():
    base = ml_root() / "processed/metu-concrete-cracks/v2"
    split = base / "provisional-10e-v2"
    report = json.loads(checked_file(split / "split_report.json", TRAIN_REPORT_SHA, 64 * 1024))
    manifest = checked_file(split / "train.jsonl", report["manifest_sha256"]["train.jsonl"], 32 * 1024**2)
    first_by_label = {}
    for line in manifest.splitlines():
        row = json.loads(line)
        label = row["label"]
        if row["split"] != "train" or label not in {"crack", "no_crack"}:
            raise ValueError("Unexpected smoke fixture partition")
        first_by_label.setdefault(label, row)
        if len(first_by_label) == 2:
            break
    if len(first_by_label) != 2:
        raise ValueError("Missing training fixture")
    for label, row in first_by_label.items():
        yield label, checked_file(base / row["path"], row["source_sha256"], 5 * 1024**2)


def measured(service, payload, worker):
    started = perf_counter()
    result = worker(service, payload)
    elapsed = round(perf_counter() - started, 3)
    if result is None:
        raise RuntimeError(f"{service} worker was unavailable after {elapsed} seconds")
    return {**result, "elapsed_seconds": elapsed}


def main():
    sys.path.insert(0, str(ROOT / "backend"))
    from app.ai_services import run_worker

    with patch.dict(os.environ, {"FIELDFIX_AI_NLP_ENABLED": "1", "FIELDFIX_AI_CV_ENABLED": "1"}):
        text = b"The bathroom sink is clogged and the faucet is leaking."
        nlp = measured("nlp", text, run_worker)
        cv = {label: measured("cv", jpeg, run_worker) for label, jpeg in cv_examples()}
    print(json.dumps({"scope": "technical smoke only", "nlp_authored_fixture": nlp, "cv_training_fixtures": cv},
                     indent=2))


if __name__ == "__main__":
    main()
