"""Pilot feature controls, NLP scope checks, and bounded local subprocesses."""

import json
import os
from pathlib import Path
import re
import subprocess
from threading import BoundedSemaphore, Thread

from app.priority_rules import report_text, uncertain
from app.request_uploads import MAX_PHOTO_BYTES


ROOT = Path(__file__).resolve().parents[2]
LABELS = {"plumbing": "Plumbing", "hvac": "HVAC", "electrical": "Electrical"}
NLP_MODEL = "fmucd-pilot-m11-v1"
CV_MODEL = "metu-resnet18-m12-v1"
BINDING_VERSION = "m13-bindings-v1"
# Passing the pilot checks does not authorize operational category suggestions.
NLP_ROLLOUT_APPROVED = False
MAX_RESPONSE = 1024
FEATURES = ("nlp", "nlp_demo", "cv", "priority")
PROFILES = {
    "nlp": ("nlp-m11", 4000, 16),
    "cv": ("cv-m12-v1", MAX_PHOTO_BYTES, 28),
}
SLOTS = {name: BoundedSemaphore(1) for name in PROFILES}
SYSTEMS = {
    "plumbing": r"\b(?:sink|faucet|tap|toilet|drain|shower|water pipe)s?\b",
    "hvac": r"\b(?:hvac|air conditioning|air conditioner|thermostat|radiator|furnace|supply vent)s?\b",
    "electrical": r"\b(?:outlet|socket|circuit breaker|electrical panel|light fixture|ceiling light)s?\b",
}
FAULTS = {
    "plumbing": r"\b(?:clogged|blocked|overflowing|leak(?:s|ing)?|not draining|not flushing)\b",
    "hvac": r"\b(?:no (?:heat|cooling|airflow)|not (?:cooling|heating|working)|blowing (?:cold|hot) air|rattling)\b",
    "electrical": r"\b(?:sparking|tripp(?:ed|ing)|flicker(?:s|ing)?|burned out|no power|not working)\b",
}
OTHER_SCOPE = re.compile(
    r"\b(?:concrete|structural|ceiling tiles?|broken glass|windows?|roof(?:ing)?|walls?|"
    r"paint(?:ing)?|patching|inspection|door lock|elevator|washing machine|pest|landscaping|mould|mold)\b"
)
NORMAL_OPERATION = re.compile(r"\b(?:(?:works?|working|operates?|operating) (?:fine|normally)|all clear)\b")
CLAUSE = re.compile(r"[.!;]+|\b(?:and|but|while)\b")


def enabled(feature: str) -> bool:
    return os.environ.get(f"FIELDFIX_AI_{feature.upper()}_ENABLED") == "1"


def availability() -> dict[str, str]:
    local = os.environ.get("LOCALAPPDATA", "")
    ml = Path(local) / "FieldFixAI/ml"
    if not enabled("cv"):
        cv = "disabled"
    elif not (Path(local).is_absolute()
              and (ml / "envs/cv-m12-v1/Scripts/python.exe").is_file()
              and all((ml / "runs/cv/metu-resnet18-m12-v1" / name).is_file()
                      for name in ("freeze.json", "config.json", "resnet18-inference.pt"))):
        cv = "runtime_missing"
    else:
        cv = "enabled"
    return {"nlp": "validation_required" if not NLP_ROLLOUT_APPROVED else
            "enabled" if enabled("nlp") else "disabled",
            "nlp_demo": "research_only" if enabled("nlp_demo") else "disabled",
            "cv": cv, "priority": "enabled" if enabled("priority") else "disabled"}


def eligible_system(description: str) -> str | None:
    text = report_text(description)
    if not text or uncertain(text) or OTHER_SCOPE.search(text) or NORMAL_OPERATION.search(text):
        return None
    systems = [key for key, pattern in SYSTEMS.items() if re.search(pattern, text)]
    if len(systems) == 1:
        system = systems[0]
        if any(re.search(SYSTEMS[system], clause) and re.search(FAULTS[system], clause) for clause in CLAUSE.split(text)):
            return system
    return None


def binding_for(label: str) -> tuple[int, str] | None:
    if label not in LABELS:
        return None
    raw = os.environ.get("FIELDFIX_AI_NLP_BINDINGS", "")
    if not raw or len(raw) > 2048:
        return None
    try:
        bindings = json.loads(raw)
        if bindings["version"] != BINDING_VERSION or bindings["model"] != NLP_MODEL:
            return None
        entry = bindings["categories"][label]
        category_id, name = entry["id"], entry["name"]
    except (ValueError, KeyError, TypeError):
        return None
    if type(category_id) is int and 1 <= category_id <= 2**63 - 1 and name == LABELS[label]:
        return category_id, name
    return None


def worker_environment(local_app_data: str) -> dict[str, str]:
    # Pass Windows runtime paths only. App secrets and arbitrary PYTHON* settings stay in FastAPI.
    env = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "TEMP", "TMP", "PATH") if key in os.environ}
    # TorchVision imports code that calls getpass.getuser() even for eager inference.
    env["USERNAME"] = os.environ.get("USERNAME") or "fieldfix-worker"
    return {**env, "LOCALAPPDATA": local_app_data, "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1"}


def stop_process(process: subprocess.Popen, env: dict[str, str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt" and "SystemRoot" in env:
        # Windows venv launchers can own a child interpreter. Stop the entire launched tree.
        taskkill = Path(env["SystemRoot"]) / "System32/taskkill.exe"
        try:
            subprocess.run([str(taskkill), "/PID", str(process.pid), "/T", "/F"],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           env=env, timeout=2, check=False, creationflags=subprocess.CREATE_NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired):
            pass
    if process.poll() is None:
        process.kill()


def exchange(command: list[str], payload: bytes, timeout: float, env: dict[str, str],
             output_limit: int = MAX_RESPONSE) -> bytes | None:
    """Drain bounded stdout while writing stdin so a stopped reader cannot deadlock the request."""
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with subprocess.Popen(command, cwd=ROOT, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, creationflags=flags, shell=False) as process:
        response = bytearray()
        failed = []

        def send():
            try:
                with process.stdin:
                    process.stdin.write(payload)
            except OSError:
                failed.append(True)

        def receive():
            try:
                with process.stdout:
                    response.extend(process.stdout.read(output_limit + 1))
                    if len(response) > output_limit:
                        stop_process(process, env)
            except OSError:
                failed.append(True)

        threads = [Thread(target=send), Thread(target=receive)]
        for thread in threads:
            thread.start()
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            stop_process(process, env)
            process.wait()
            code = 1
        for thread in threads:
            thread.join()
        if code or failed or len(response) > output_limit:
            return None
        return bytes(response)


def run_worker(feature: str, payload: bytes) -> dict | None:
    extended = {"nlp_inspect": ("nlp_demo", "nlp", "--inspect", 600000, 48000),
                "nlp_corpus": ("nlp_demo", "nlp", "--corpus", 1000000, 48000),
                "cv_preview": ("cv", "cv", "--preview", MAX_PHOTO_BYTES, 550000)}.get(feature)
    if not enabled(extended[0] if extended else feature):
        return None
    # Both NLP entry points share the same frozen worker, limits and slot.
    if feature == "nlp_demo":
        feature = "nlp"
    if extended:
        _, feature, option, _, _ = extended
    env_name, limit, deadline = PROFILES[feature]
    if extended:
        limit = extended[3]
    if not 0 < len(payload) <= limit or not SLOTS[feature].acquire(blocking=False):
        return None
    try:
        local_app_data = os.environ.get("LOCALAPPDATA")
        if not local_app_data or not Path(local_app_data).is_absolute():
            return None
        python = Path(local_app_data) / "FieldFixAI/ml/envs" / env_name / "Scripts/python.exe"
        worker = ROOT / "ml/inference" / f"{feature}_worker.py"
        if not python.is_file() or not worker.is_file():
            return None
        command = [str(python), "-I", "-B", str(worker)]
        env = worker_environment(local_app_data)
        output = (exchange([*command, option], payload, deadline, env, extended[4]) if extended
                  else exchange(command, payload, deadline, env))
        result = json.loads(output) if output else None
        if not isinstance(result, dict):
            return None
        if extended:
            return result
        if feature == "nlp" and result.keys() == {"label"}:
            label = result["label"]
            return result if label is None or isinstance(label, str) and label in LABELS else None
        if feature == "cv" and result.keys() == {"crack_detected"} and type(result["crack_detected"]) is bool:
            return result
    except (OSError, ValueError, RecursionError, subprocess.SubprocessError):
        return None
    finally:
        SLOTS[feature].release()
    return None
