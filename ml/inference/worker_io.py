"""Small protocol and artifact checks shared by the two local worker scripts."""

from hashlib import sha256
import json
import os
from pathlib import Path
import sys
from threading import Timer


def watchdog(seconds: int) -> Timer:
    # Also bound the worker if its supervising process exits unexpectedly.
    timer = Timer(seconds, os._exit, args=(1,))
    timer.daemon = True
    timer.start()
    return timer


def input_bytes(maximum: int) -> bytes:
    data = sys.stdin.buffer.read(maximum + 1)
    if not 0 < len(data) <= maximum:
        raise ValueError("Invalid worker payload")
    return data


def checked_file(path: Path, expected: str, maximum: int) -> bytes:
    with path.open("rb") as stream:
        data = stream.read(maximum + 1)
    if len(data) > maximum or sha256(data).hexdigest() != expected:
        raise ValueError("Unexpected frozen artifact")
    return data


def ml_root() -> Path:
    return Path(os.environ["LOCALAPPDATA"]) / "FieldFixAI/ml"


def emit(result: dict) -> None:
    sys.stdout.write(json.dumps(result))
    sys.stdout.flush()
