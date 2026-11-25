"""Fit the Milestone 11 models, freeze selection, then evaluate the test once."""

import argparse
from datetime import datetime, timezone
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
import sys
import tempfile

import joblib
import numpy as np

from ml.nlp import fmucd, models
from ml.pilot.acquire_fmucd import atomic_write, pilot_root


RUN_ID = "milestone-11-v1"
MODEL_VERSION = "fmucd-pilot-m11-v1"
PACKAGES = ("numpy", "scipy", "scikit-learn", "joblib", "threadpoolctl")
HERE = Path(__file__).resolve().parent


def run_path():
    path = pilot_root() / "runs" / "nlp" / RUN_ID
    expected = Path(os.environ["LOCALAPPDATA"]).resolve() / "FieldFixAI" / "ml" / "runs" / "nlp" / RUN_ID
    if path.resolve() != expected:
        raise ValueError(f"NLP run path crosses a link or junction: {path}")
    return path


def json_bytes(data):
    return (json.dumps(data, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


def save_json(path, data):
    atomic_write(path, json_bytes(data))


def digest(path):
    return fmucd.sha256(path.read_bytes())


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def code_hashes():
    files = (HERE / "fmucd.py", HERE / "models.py", HERE / "train_m11.py", HERE.parent / "pilot" / "validate_fmucd.py")
    return {path.name: digest(path) for path in files}


def software():
    return {"python": platform.python_version(), **{name: version(name) for name in PACKAGES}}


def verify_versions():
    installed = software()
    for pin in (HERE / "requirements-m11.txt").read_text().splitlines():
        name, required = pin.split("==")
        if installed[name] != required:
            raise ValueError(f"Milestone 11 requires {name}=={required}, found {installed[name]}")
    return installed


def texts(rows):
    return [row["text"] for row in rows]


def labels(rows):
    return [row["label"] for row in rows]


def verify_frozen(run):
    state = json.loads((run / "freeze.json").read_bytes())
    if state["run_id"] != RUN_ID or state["software"] != verify_versions():
        raise ValueError("The run identity or package versions differ from the frozen state")
    for name, checksum in state["file_hashes"].items():
        if Path(name).name != name:
            raise ValueError("Invalid frozen artifact name")
        fmucd.read_pinned(run / name, checksum)
    if state["code_sha256"] != code_hashes():
        raise ValueError("Training or preprocessing code changed after the freeze")
    return state


def read_trusted_model(run, freeze):
    name = freeze["selected_model"] + ".joblib"
    fmucd.read_pinned(run / name, freeze["file_hashes"][name])
    # Only the trusted artifacts produced by this local run are deserialized.
    return joblib.load(run / name)


def repeat_check(name, model, train, validation, baseline):
    repeated, elapsed = models.fit_model(name, train)
    if repeated["tfidf"].vocabulary_ != model["tfidf"].vocabulary_:
        raise ValueError(f"{name} vocabulary was different on repeat fitting")
    np.testing.assert_array_equal(repeated["tfidf"].idf_, model["tfidf"].idf_)
    again = models.scores(repeated, texts(validation))
    np.testing.assert_array_equal(again["forced"], baseline["forced"])
    np.testing.assert_allclose(again["decision"], baseline["decision"], rtol=0, atol=1e-12)
    return {
        "fresh_train_only_refit_seconds": elapsed,
        "vocabulary_and_idf_equal": True,
        "validation_predictions_equal": True,
        "max_absolute_validation_score_delta": float(np.max(np.abs(again["decision"] - baseline["decision"]))),
    }


def train(run):
    if run.exists():
        print(f"Reusing frozen development run {run}")
        return verify_frozen(run)
    installed = verify_versions()
    original, approved, source, opened = fmucd.acquire_rows(("train", "validation"))
    if {"window-02", "window-03"}.intersection(opened):
        raise ValueError("A test text window was opened before freeze")
    eligible, disposition = fmucd.supervised_rows(original, approved)
    training, validation = eligible["train"], eligible["validation"]
    print(f"Train {len(training)}, validation {len(validation)}. Test windows unopened.", flush=True)

    run.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".nlp-m11-", dir=run.parent) as temp:
        stage = Path(temp) / "run"
        stage.mkdir()
        comparison = {}
        for name in models.MODEL_NAMES:
            model, elapsed = models.fit_model(name, training)
            before = models.scores(model, texts(validation))
            train_scores = models.scores(model, texts(training))
            rule = models.choose_review_rule(train_scores, labels(validation), before)
            file = stage / f"{name}.joblib"
            joblib.dump(model, file, compress=3, protocol=5)
            reloaded = joblib.load(file)
            restored = models.scores(reloaded, texts(validation))
            np.testing.assert_array_equal(restored["decision"], before["decision"])
            np.testing.assert_array_equal(restored["forced"], model.predict(texts(validation)))
            comparison[name] = {
                "validation": models.evaluate_predictions(labels(validation), before, rule["threshold"]),
                "validation_review_search": rule,
                "fit_seconds": elapsed,
                "compressed_joblib_bytes": file.stat().st_size,
                "vocabulary_size": len(model["tfidf"].vocabulary_),
                "iterations": np.asarray(model["classifier"].n_iter_).tolist(),
                "repeatability": {
                    **repeat_check(name, model, training, validation, before),
                    "joblib_reload_scores_and_predictions_equal": True,
                },
            }
            result = comparison[name]["validation"]["forced"]
            print(f"{name}: validation accuracy {result['accuracy']:.4f}; macro-F1 {result['macro_f1']:.4f}", flush=True)
        winner = max(
            models.MODEL_NAMES,
            key=lambda name: (
                comparison[name]["validation"]["forced"]["macro_f1"],
                comparison[name]["validation"]["forced"]["accuracy"],
                name == "logistic_regression",
            ),
        )
        rule = comparison[winner]["validation_review_search"]
        label_map = {
            **approved,
            "source_mapping_version": approved["mapping_version"],
            "mapping_version": "1.0.0-m11-labels.1",
            "status": "approved_training_labels_database_ids_unbound",
            "model_version": MODEL_VERSION,
        }
        save_json(stage / "label_map.json", label_map)
        save_json(stage / "label_order.json", fmucd.LABEL_ORDER)
        save_json(stage / "source.json", source)
        save_json(stage / "development_evaluation.json", {
            "models": comparison, "data_disposition": disposition,
            "source_windows_opened": opened, "test_scores_computed": False,
        })
        provenance = fmucd.provenance(training + validation)
        (stage / "development_rows.jsonl").write_bytes(b"".join(
            (json.dumps(row, sort_keys=True) + "\n").encode() for row in provenance
        ))
        (stage / "requirements-m11.txt").write_bytes((HERE / "requirements-m11.txt").read_bytes())
        freeze = {
            "run_id": RUN_ID,
            "model_version": MODEL_VERSION,
            "frozen_utc": now(),
            "selected_model": winner,
            "selection": "highest validation forced macro-F1, then accuracy, then Logistic Regression",
            "review_rule": rule,
            "label_order": fmucd.LABEL_ORDER,
            "seed": models.SEED,
            "software": installed,
            "configuration": {
                "normalization": "Unicode NFC, casefold, and collapsed whitespace; retain words including negation",
                "token_pattern": models.TOKEN_PATTERN,
                "vectorizer": {"ngram_range": [1, 2], "min_df": 2, "sublinear_tf": True, "norm": "l2", "stop_words": None},
                "logistic_regression": {"solver": "lbfgs", "C": 1.0, "max_iter": 1000, "class_weight": "balanced"},
                "linear_svc": {"C": 1.0, "dual": "auto", "max_iter": 5000, "class_weight": "balanced"},
                "fit_thread_limit": 1,
            },
            "code_sha256": code_hashes(),
            "file_hashes": {path.name: digest(path) for path in sorted(stage.iterdir())},
        }
        save_json(stage / "freeze.json", freeze)
        stage.rename(run)
    print(f"Frozen {winner} and decision margin {rule['threshold']:.6f} in {run}", flush=True)
    return freeze


def evaluate(run):
    freeze = verify_frozen(run)
    checksum = digest(run / "freeze.json")
    output = run / "test_evaluation.json"
    claim = run / "test_access.json"
    if output.exists():
        saved = json.loads(output.read_bytes())
        started = json.loads(claim.read_bytes())
        if saved["freeze_sha256"] != checksum or started["freeze_sha256"] != checksum:
            raise ValueError("Stored test result does not belong to this freeze")
        print("Reusing saved test metrics without scoring the test again.", flush=True)
        return saved
    if claim.exists():
        raise ValueError("A held-out evaluation was already started. Preserved the claim and did not rescore.")

    source = json.loads((run / "source.json").read_bytes())
    original, approved, _, opened = fmucd.acquire_rows(("test",), frozen_source=source)
    eligible, disposition = fmucd.supervised_rows(original, approved)
    rows = eligible["test"]
    model = read_trusted_model(run, freeze)
    with claim.open("xb") as stream:
        stream.write(json_bytes({"freeze_sha256": checksum, "started_utc": now(), "policy": "score once"}))
        stream.flush()
        os.fsync(stream.fileno())
    result = models.scores(model, texts(rows))
    threshold = freeze["review_rule"]["threshold"]
    mask = models.accepted(result, threshold)
    evaluations = models.evaluate_predictions(labels(rows), result, threshold)
    review_rows = [
        {
            **row,
            "forced_label": str(result["forced"][i]),
            "decision_margin": float(result["margin"][i]),
            "has_features": bool(result["has_features"][i]),
            "accepted": bool(mask[i]),
        }
        for i, row in enumerate(fmucd.provenance(rows))
    ]
    saved = {
        "freeze_sha256": checksum,
        "selected_model": freeze["selected_model"],
        "university": fmucd.UNIVERSITIES["test"],
        "source_windows_opened_after_freeze": opened,
        "data_disposition": disposition["test"],
        "evaluations": evaluations,
        "rows": review_rows,
        "scored_once_utc": now(),
    }
    save_json(output, saved)
    print(f"Saved one held-out evaluation in {output}", flush=True)
    return saved


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["train", "evaluate"])
    args = parser.parse_args()
    try:
        location = run_path()
        data = train(location) if args.command == "train" else evaluate(location)["evaluations"]
        if args.command == "evaluate":
            print(json.dumps(data, indent=2))
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        sys.exit(f"Milestone 11 stopped: {error}")


if __name__ == "__main__":
    main()
