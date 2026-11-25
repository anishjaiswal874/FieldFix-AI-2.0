"""Acquire fixed, small byte ranges from the publisher's FMUCD v1 CSV."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


SOURCE = (
    "https://data.mendeley.com/public-files/datasets/cb8d2nsjss/files/"
    "4c6c74f2-8bac-423c-a0c9-143130e1eea2/file_downloaded"
)
SOURCE_BYTES = 1_439_574_384
ETAG = '"d1b5b032e7cd50b3da2e7cc2c53a9749-69"'
WINDOW_BYTES = 128 * 1024
STRATA = 12


def pilot_root():
    return Path(os.environ["LOCALAPPDATA"]) / "FieldFixAI" / "ml"


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(data)
        except BaseException:
            handle.close()
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def windows(header_only=False):
    yield "header", 0
    if not header_only:
        for index in range(STRATA):
            midpoint = SOURCE_BYTES * (2 * index + 1) // (2 * STRATA)
            yield f"window-{index + 1:02d}", midpoint - WINDOW_BYTES // 2


def check_curl_version():
    result = subprocess.run(
        ["curl.exe", "--disable", "--version"], capture_output=True, text=True, check=True
    )
    major, minor, *_ = result.stdout.split()[1].split(".")
    if (int(major), int(minor)) < (8, 4):
        raise RuntimeError("curl 8.4 or later is required for the response size cap")


def fetch(start):
    end = start + WINDOW_BYTES - 1
    expected_range = f"bytes {start}-{end}/{SOURCE_BYTES}"
    # curl 8.4+ also enforces --max-filesize when a response omits Content-Length.
    fields = (
        "%{stderr}%{http_code}\t%header{content-range}\t%header{content-length}"
        "\t%header{etag}\t%header{content-encoding}"
    )
    result = subprocess.run(
        [
            "curl.exe", "--disable", "--silent", "--show-error", "--fail", "--location",
            "--proto", "=https", "--proto-redir", "=https", "--max-redirs", "3",
            "--max-time", "30", "--max-filesize", str(WINDOW_BYTES),
            "--range", f"{start}-{end}", "--header", "Accept-Encoding: identity",
            "--write-out", fields, SOURCE,
        ],
        capture_output=True,
        check=False,
    )
    headers = result.stderr.decode("utf-8", errors="replace")
    if result.returncode:
        raise RuntimeError(f"Range {start}-{end} failed: {headers.strip()}")
    status, content_range, length, etag, encoding = headers.split("\t")
    if (
        status != "206"
        or content_range != expected_range
        or length != str(WINDOW_BYTES)
        or etag != ETAG
        or encoding not in {"", "identity"}
        or len(result.stdout) != WINDOW_BYTES
    ):
        raise ValueError(f"Rejected response for {expected_range} (HTTP {status})")
    return result.stdout


def load_window(folder, name, start):
    path = folder / f"{name}.bin"
    metadata_path = folder / f"{name}.json"
    if path.stat().st_size != WINDOW_BYTES:
        raise ValueError(f"Cached range has the wrong size: {path}")
    data = path.read_bytes()
    metadata = json.loads(metadata_path.read_bytes())
    expected = window_metadata(name, start, data)
    if metadata != expected:
        raise ValueError(f"Cached range failed validation: {path}")
    return data


def window_metadata(name, start, data):
    return {
        "name": name,
        "source": SOURCE,
        "source_size": SOURCE_BYTES,
        "etag": ETAG,
        "start": start,
        "end": start + WINDOW_BYTES - 1,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--header-only", action="store_true")
    args = parser.parse_args()
    root = pilot_root()
    free = shutil.disk_usage(root.anchor).free
    if free < 8 * 1024 * 1024:
        raise RuntimeError("Pilot requires 8 MiB of free storage on the target volume")
    folder = root / "raw" / "fmucd" / "v1" / "pilot"
    downloaded = 0

    for name, start in windows(args.header_only):
        path = folder / f"{name}.bin"
        metadata_path = folder / f"{name}.json"
        if path.exists() and metadata_path.exists():
            load_window(folder, name, start)
            print(f"{name}: verified cached range")
            continue
        if downloaded == 0:
            check_curl_version()
        data = fetch(start)
        downloaded += len(data)
        atomic_write(path, data)
        metadata = window_metadata(name, start, data)
        atomic_write(metadata_path, (json.dumps(metadata, indent=2) + "\n").encode())
        print(f"{name}: saved verified range {start}-{metadata['end']}")

    print(f"Downloaded CSV bytes this run: {downloaded}; volume free before run: {free}")


if __name__ == "__main__":
    main()
