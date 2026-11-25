"""Verify the METU v2 source or acquire its complete archive after approval."""

import argparse
from contextlib import contextmanager
import hashlib
import json
import msvcrt
import os
from pathlib import Path
import shutil
import subprocess
import sys

from ml.pilot.acquire_fmucd import atomic_write, check_curl_version, pilot_root


DATASET = "metu-concrete-cracks"
VERSION = "v2"
DOI = "10.17632/5y9wdsg2zt.2"
LICENSE = "CC BY 4.0"
ARCHIVE_NAME = "Concrete Crack Images for Classification.rar"
ARCHIVE_BYTES = 241_363_336
ARCHIVE_SHA256 = "08d7dc505a4f5a0330cee2fa2a1ae4b5b4f98bcffd0549b774caf7959bb1f02f"
FILE_ID = "8a70d8a5-bce9-4291-bab9-b48cfb3e87c3"
METADATA_URL = "https://data.mendeley.com/public-api/datasets/5y9wdsg2zt/files?folder_id=root&version=2"
DOWNLOAD_URL = f"https://data.mendeley.com/public-files/datasets/5y9wdsg2zt/files/{FILE_ID}/file_downloaded"
CHUNK_BYTES = 16 * 1024**2
PROBE_BYTES = 1024
PREPARATION_HEADROOM = 4 * 1024**3


def dataset_path(stage):
    root = pilot_root()
    path = root / stage / DATASET / VERSION
    expected = Path(os.environ["LOCALAPPDATA"]).resolve() / "FieldFixAI" / "ml" / stage / DATASET / VERSION
    if path.resolve() != expected:
        raise ValueError(f"Dataset path crosses a link or junction: {path}")
    return path


@contextmanager
def dataset_lock(raw):
    raw.mkdir(parents=True, exist_ok=True)
    lock = raw / ".dataset.lock"
    if lock.resolve() != lock.absolute():
        raise ValueError(f"Refusing redirected lock path: {lock}")
    with lock.open("a+b") as handle:
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise RuntimeError("Another METU acquisition or preparation is running") from error
        yield


def write_json(path, value):
    atomic_write(path, (json.dumps(value, ensure_ascii=True, indent=2) + "\n").encode("utf-8"))


def verify_archive(path):
    if path.resolve() != path.absolute() or not path.is_file():
        raise ValueError(f"Expected a regular archive at {path}")
    if path.stat().st_size != ARCHIVE_BYTES:
        raise ValueError("Archive size differs from the official METU v2 size")
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != ARCHIVE_SHA256:
        raise ValueError("Archive SHA-256 does not match the publisher")


def curl(url, *args):
    result = subprocess.run(
        [
            "curl.exe", "--disable", "--silent", "--show-error", "--fail", "--location",
            "--proto", "=https", "--proto-redir", "=https", "--max-redirs", "3",
            "--connect-timeout", "15", "--max-time", "120", *args, url,
        ],
        capture_output=True,
        check=False,
    )
    headers = result.stderr.decode("utf-8", errors="replace")
    if result.returncode:
        raise RuntimeError(f"Publisher request stopped (curl {result.returncode}): {headers.strip()}")
    return result.stdout, headers


def verify_publisher_metadata():
    payload, _ = curl(
        METADATA_URL, "--max-filesize", "65536",
        "--header", "Accept: application/vnd.mendeley-public-dataset.1+json",
    )
    listing = [entry for entry in json.loads(payload) if entry.get("id") == FILE_ID]
    if len(listing) == 1:
        details = listing[0]["content_details"]
        observed = (listing[0]["filename"], details["size"], details["sha256_hash"], details["download_url"])
        if observed == (ARCHIVE_NAME, ARCHIVE_BYTES, ARCHIVE_SHA256, DOWNLOAD_URL):
            return
    raise ValueError("Publisher file metadata differs from the pinned METU v2 release")


def fetch_range(start, count, validator=None):
    end = start + count - 1
    data, headers = curl(
        DOWNLOAD_URL, "--max-filesize", str(count), "--range", f"{start}-{end}",
        "--header", "Accept-Encoding: identity", "--write-out",
        "%{stderr}%{http_code}\t%header{content-range}\t%header{content-length}"
        "\t%header{etag}\t%header{content-encoding}",
    )
    status, range_header, length, etag, encoding = headers.split("\t")
    if (
        status != "206"
        or range_header != f"bytes {start}-{end}/{ARCHIVE_BYTES}"
        or length != str(count)
        or len(data) != count
        or encoding not in {"", "identity"}
        or not (len(etag) > 2 and etag.startswith('"') and etag.endswith('"'))
        or (validator is not None and etag != validator)
    ):
        raise ValueError(f"Rejected HTTP {status} for bytes {start}-{end} (range or validator changed)")
    return data, etag


def preflight():
    raw = dataset_path("raw")
    dataset_path("processed")
    check_curl_version()
    verify_publisher_metadata()
    data, etag = fetch_range(0, PROBE_BYTES)
    if not data.startswith(b"Rar!\x1a\x07\x00"):
        raise ValueError("The publisher response does not start with the verified RAR4 signature")
    print(f"{DOI} | {LICENSE} | {ARCHIVE_NAME}")
    print(f"Publisher size: {ARCHIVE_BYTES:,} bytes | SHA-256: {ARCHIVE_SHA256}")
    print(DOWNLOAD_URL)
    print(f"206 range probe: {len(data):,} bytes discarded | strong ETag {etag}")
    free = shutil.disk_usage(raw.anchor).free
    print(f"ML root: {pilot_root()} | {free:,} bytes free")
    print(
        f"Planning budget: {ARCHIVE_BYTES + PREPARATION_HEADROOM:,} bytes "
        "(archive plus 4 GiB working allowance; exact extraction budget follows inventory)"
    )
    print(subprocess.check_output(["tar.exe", "--version"], text=True).strip())
    from PIL import __version__ as pillow_version

    print(f"Pillow {pillow_version}")
    return etag


def committed_size(partial, state):
    """Check journaled chunks and remove only an interrupted, uncommitted tail."""
    offset = 0
    with partial.open("r+b") as stream:
        for chunk in state["chunks"]:
            count = min(CHUNK_BYTES, ARCHIVE_BYTES - offset)
            if count <= 0 or chunk["bytes"] != count:
                raise ValueError("Partial download journal contains an invalid chunk size")
            data = stream.read(count)
            if len(data) != count or hashlib.sha256(data).hexdigest() != chunk["sha256"]:
                raise ValueError(f"Partial download failed its saved SHA-256 at byte {offset}")
            offset += count
        if stream.read(1):
            stream.truncate(offset)
            stream.flush()
            os.fsync(stream.fileno())
            print(f"Discarded uncommitted bytes after offset {offset:,}")
    return offset


def acquire():
    raw = dataset_path("raw")
    with dataset_lock(raw):
        archive = raw / ARCHIVE_NAME
        if archive.exists():
            verify_archive(archive)
            print(f"Verified and reused {archive}")
            return
        for path in (archive, raw / (ARCHIVE_NAME + ".part"), raw / "download-state.json"):
            if path.resolve() != path.absolute():
                raise ValueError(f"Refusing redirected download path: {path}")
        partial = raw / (ARCHIVE_NAME + ".part")
        journal = raw / "download-state.json"
        etag = preflight()
        identity = {"url": DOWNLOAD_URL, "size": ARCHIVE_BYTES, "sha256": ARCHIVE_SHA256, "etag": etag}
        if journal.exists():
            state = json.loads(journal.read_bytes())
            if state["source"] != identity:
                raise ValueError("Source validator changed. The partial download was preserved.")
            if state["chunks"] and not partial.exists():
                raise ValueError("Download journal exists but its data file is missing")
        else:
            if partial.exists() and partial.stat().st_size:
                raise ValueError("Existing partial download has no trusted journal")
            state = {"source": identity, "chunks": []}
            write_json(journal, state)
        partial.touch(exist_ok=True)
        offset = committed_size(partial, state)
        print(f"Verified resume offset: {offset:,} bytes", flush=True)
        if shutil.disk_usage(raw).free < ARCHIVE_BYTES - offset + 1024**2:
            raise RuntimeError("Insufficient free space for the remaining archive bytes and journal")
        with partial.open("ab") as stream:
            while offset < ARCHIVE_BYTES:
                count = min(CHUNK_BYTES, ARCHIVE_BYTES - offset)
                data, _ = fetch_range(offset, count, validator=etag)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
                state["chunks"].append({"bytes": count, "sha256": hashlib.sha256(data).hexdigest()})
                write_json(journal, state)
                offset += count
                print(f"Committed {offset:,}/{ARCHIVE_BYTES:,} bytes", flush=True)
        verify_archive(partial)
        partial.rename(archive)
        print(f"Verified complete archive: {archive}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("preflight", help="Read metadata and discard a capped 1 KiB probe")
    download = commands.add_parser("download", help="Acquire the complete archive")
    download.add_argument("--approved-download", action="store_true", required=True)
    args = parser.parse_args()
    try:
        if args.command == "preflight":
            preflight()
        else:
            acquire()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        sys.exit(f"METU acquisition stopped: {error}")


if __name__ == "__main__":
    main()
