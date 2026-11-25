"""Run local inference on genuine development examples or one supplied description."""

import argparse
import json
import sys

from ml.nlp import fmucd, models
from ml.nlp.train_m11 import MODEL_VERSION, digest, json_bytes, read_trusted_model, run_path, save_json, verify_frozen


# Fixed examples from the 10C audit. The scope labels come from the reviewed cases,
# never from predictions. The out-of-scope rows were excluded before fitting.
EXAMPLES = (
    ("sink_clogged", "window-06", 224, "plumbing"),
    ("heater_noise", "window-05", 211, "hvac"),
    ("tripped_circuit", "window-06", 144, "electrical"),
    ("failed_ordinary_light", "window-12", 180, "electrical"),
    ("wall_patching", "window-06", 82, "outside_scope"),
    ("cracked_windows", "window-06", 247, "outside_scope"),
    ("ceiling_tile_fell", "window-09", 415, "outside_scope"),
    ("city_inspection_support", "window-11", 140, "outside_scope"),
)


def inference(model, text, threshold):
    result = models.scores(model, [text])
    accept = bool(models.accepted(result, threshold)[0])
    return {
        "suggested_label": str(result["forced"][0]) if accept else None,
        "action": "suggest_label" if accept else "manual_review",
        "reason": (
            "passed_margin"
            if accept else "below_margin" if result["has_features"][0] else "no_vocabulary_features"
        ),
        "decision_margin": float(result["margin"][0]),
        "margin_threshold": threshold,
        "model_version": MODEL_VERSION,
    }


def write_or_verify(path, content):
    if path.exists() and path.read_bytes() != json_bytes(content):
        raise ValueError(f"Reproduction differs from the existing {path.name}")
    if not path.exists():
        save_json(path, content)


def smoke(run, freeze, model):
    source = json.loads((run / "source.json").read_bytes())
    original, approved, _, _ = fmucd.acquire_rows(("train",), frozen_source=source)
    trained, _ = fmucd.supervised_rows(original, approved)
    in_scope = {row["record_sha256"]: row["label"] for row in trained["train"]}
    raw = {(row["window"], row["line_in_window"]): row for row in original["train"]}
    threshold = freeze["review_rule"]["threshold"]
    cases = []
    for tag, window, line, scope in EXAMPLES:
        row = raw[(window, line)]
        if in_scope.get(row["record_sha256"], "outside_scope") != scope:
            raise ValueError(f"Unexpected source scope of smoke case {tag}")
        cases.append({
            "case": tag,
            "reviewed_scope": scope,
            "publisher_record": fmucd.provenance([row])[0],
            **inference(model, row["text"], threshold),
        })
    report = {
        "purpose": "qualitative inference smoke test, separate from held-out evaluation",
        "examples_source": "genuine records from training universities; out-of-scope examples were excluded from training",
        "cases": cases,
        "technical_empty_input_check": inference(model, "", threshold),
    }
    if report["technical_empty_input_check"]["action"] != "manual_review":
        raise ValueError("Empty input must route to manual review")
    return report


def finish_manifest(run, freeze):
    test = json.loads((run / "test_evaluation.json").read_bytes())
    claim = json.loads((run / "test_access.json").read_bytes())
    checksum = digest(run / "freeze.json")
    if test["freeze_sha256"] != checksum or claim["freeze_sha256"] != checksum:
        raise ValueError("Test artifacts do not belong to the current freeze")
    files = {
        path.name: {"bytes": path.stat().st_size, "sha256": digest(path)}
        for path in sorted(run.iterdir()) if path.is_file() and path.name != "run_manifest.json"
    }
    write_or_verify(run / "run_manifest.json", {
        "run_id": freeze["run_id"],
        "model_version": MODEL_VERSION,
        "selected_model": freeze["selected_model"],
        "label_order": freeze["label_order"],
        "software": freeze["software"],
        "seed": freeze["seed"],
        "artifacts": files,
        "test_evaluation": "one scoring pass after freeze",
        "configuration": freeze["configuration"],
    })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text", help="Score one description without saving it")
    args = parser.parse_args()
    try:
        run = run_path()
        freeze = verify_frozen(run)
        model = read_trusted_model(run, freeze)
        if args.text is not None:
            result = inference(model, args.text, freeze["review_rule"]["threshold"])
        else:
            result = smoke(run, freeze, model)
            if not (run / "test_evaluation.json").exists():
                raise ValueError("Complete the frozen held-out evaluation before saving the final run manifest")
            write_or_verify(run / "smoke_results.json", result)
            finish_manifest(run, freeze)
        print(json.dumps(result, indent=2))
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        sys.exit(f"Milestone 11 smoke test stopped: {error}")


if __name__ == "__main__":
    main()
