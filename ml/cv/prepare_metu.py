"""Audit the verified METU archive and optionally publish a parent-grouped split."""

import argparse
from collections import Counter, defaultdict
from contextlib import contextmanager
import hashlib
from io import BytesIO
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

from PIL import Image, __version__ as PILLOW_VERSION

from ml.cv.acquire_metu import (
    ARCHIVE_BYTES, ARCHIVE_NAME, ARCHIVE_SHA256, DOI, DOWNLOAD_URL, LICENSE,
    dataset_lock, dataset_path, verify_archive, write_json,
)
from ml.cv.metu_groups import RATIOS, SEED, mark_similarity, split_rows


FOLDERS = {"positive": "crack", "negative": "no_crack"}
EXPECTED = {"crack": 20_000, "no_crack": 20_000}
MAX_PATCH_BYTES = 2 * 1024**2
RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL"} | {
    f"{device}{i}" for device in ("COM", "LPT") for i in range(10)
}


@contextmanager
def tar_stream(source):
    # Convert RAR to a structured tar stream. bsdtar never writes dataset files.
    # -P preserves original names so Python can reject unsafe paths.
    with tempfile.TemporaryFile() as errors:
        with subprocess.Popen(
            ["tar.exe", "-cPf", "-", "--format=pax", f"@{source.as_posix()}"],
            stdout=subprocess.PIPE, stderr=errors,
        ) as process:
            assert process.stdout is not None
            try:
                with tarfile.open(fileobj=process.stdout, mode="r|") as archive:
                    yield archive
                if process.stdout.read(64 * 1024).strip(b"\0"):
                    raise ValueError("Unexpected data after the generated tar stream")
                if process.stdout.read(1):
                    raise ValueError("Unexpected trailing length in the generated tar stream")
                status = process.wait()
                errors.seek(0)
                diagnostic = ""
                for line in errors:
                    message = line.decode(errors="replace").strip()
                    # Archive owner names are ignored. Windows tar can warn about a null gname.
                    if message and not message.endswith("Can't translate gname '(null)' to current locale"):
                        diagnostic = message
                        break
                if status or diagnostic:
                    raise ValueError(f"Windows tar could not read the complete RAR (exit {status}): {diagnostic}")
            except BaseException:
                if process.poll() is None:
                    process.kill()
                raise


def parts_for(name):
    parts = name.replace("\\", "/").split("/")
    for part in parts:
        if (
            not re.fullmatch(r"[A-Za-z0-9._ -]{1,160}", part)
            or part.endswith((".", " "))
            or part.split(".")[0].upper() in RESERVED_NAMES
        ):
            raise ValueError(f"Unsafe archive path: {name!r}")
    return parts


def scan_archive(archive, expected):
    """Build an exact size and entry inventory before writing images."""
    layout = []
    files = {}
    ancillary = []
    counts = Counter()
    wrappers = set()
    seen = set()
    path_spellings = {}
    path_types = {}
    for entry in archive:
        parts = parts_for(entry.name)
        name = "/".join(parts)
        if name.casefold() in seen:
            raise ValueError(f"Repeated or case-colliding archive path: {name}")
        seen.add(name.casefold())
        for depth in range(1, len(parts) + 1):
            prefix = "/".join(parts[:depth])
            if path_spellings.setdefault(prefix.casefold(), prefix) != prefix:
                raise ValueError(f"Case-colliding archive directory: {prefix}")
            is_directory = depth < len(parts) or entry.isdir()
            if path_types.setdefault(prefix, is_directory) != is_directory:
                raise ValueError(f"Archive file conflicts with a directory: {prefix}")
        layout.append((entry.name, entry.type, entry.size))
        if entry.isdir() and entry.size == 0:
            continue
        if entry.type not in {tarfile.REGTYPE, tarfile.AREGTYPE} or entry.sparse is not None:
            raise ValueError(f"Archive contains a link or special member: {name}")
        suffix = Path(parts[-1]).suffix.lower()
        if suffix not in {".jpg", ".jpeg"}:
            if (
                0 <= entry.size <= 1024**2
                and (suffix in {".txt", ".csv", ".json"} or parts[-1].lower() in {"desktop.ini", "thumbs.db", ".ds_store"})
            ):
                ancillary.append({"archive_member": name, "bytes": entry.size})
                continue
            raise ValueError(f"Unexpected archive member: {name}")
        if len(parts) not in {2, 3} or parts[-2].lower() not in FOLDERS:
            raise ValueError(f"Image outside the Positive and Negative class folders: {name}")
        if not 0 < entry.size <= MAX_PATCH_BYTES:
            raise ValueError(f"Unexpected image size in archive: {name} ({entry.size} bytes)")
        wrappers.add(tuple(parts[:-2]))
        label = FOLDERS[parts[-2].lower()]
        counts[label] += 1
        if counts[label] > expected[label]:
            raise ValueError(f"Too many {label} entries in the official archive")
        files[name] = {"archive_member": name, "label": label, "bytes": entry.size}
    if counts != Counter(expected) or len(wrappers) != 1:
        raise ValueError(f"Unexpected archive layout or class counts: {dict(counts)}")
    return layout, files, ancillary


def decode_patch(data):
    result = {"source_sha256": hashlib.sha256(data).hexdigest()}
    try:
        with Image.open(BytesIO(data), formats=["JPEG"]) as image:
            if image.size != (227, 227) or image.mode != "RGB":
                return {**result, "status": "excluded", "reason": "unexpected_dimensions_or_mode"}
            image.load()
            result["pixel_sha256"] = hashlib.sha256(image.tobytes()).hexdigest()
            luminance = image.convert("L").resize((9, 8), Image.Resampling.LANCZOS).tobytes()
            dhash = 0
            for y in range(8):
                for x in range(8):
                    i = y * 9 + x
                    dhash = (dhash << 1) | (luminance[i] > luminance[i + 1])
            result.update(status="kept", dhash=f"{dhash:016x}", has_exif=bool(image.info.get("exif")))
    except (OSError, ValueError, Image.DecompressionBombError):
        result.update(status="excluded", reason="unreadable_jpeg")
    return result


def extract_images(archive, layout, files, target):
    rows = []
    pixel_groups = defaultdict(list)
    for label in EXPECTED:
        (target / "images" / label).mkdir(parents=True)
    entries = iter(archive)
    for name, kind, size in layout:
        entry = next(entries, None)
        if entry is None or (entry.name, entry.type, entry.size) != (name, kind, size):
            raise ValueError("Archive changed between listing and extraction")
        canonical_name = "/".join(parts_for(entry.name))
        info = files.get(canonical_name)
        if not info:
            continue
        image_data = archive.extractfile(entry).read()
        if len(image_data) != size:
            raise ValueError(f"Incomplete archive member: {canonical_name}")
        row = {**info, **decode_patch(image_data), "parent_group": None, "split": None}
        rows.append(row)
        if row["status"] == "kept":
            group = pixel_groups[row["pixel_sha256"]]
            if not group:
                output = Path("images") / row["label"] / Path(canonical_name).name
                (target / output).write_bytes(image_data)
                row["path"] = output.as_posix()
            group.append(row)
    if next(entries, None) is not None:
        raise ValueError("Archive acquired extra entries after the listing pass")

    for group in pixel_groups.values():
        first, *copies = group
        if any(row["label"] != first["label"] for row in copies):
            (target / first.pop("path")).unlink()
            for row in group:
                row.update(status="excluded", reason="identical_pixels_conflicting_labels")
        else:
            sources = {first["source_sha256"]: first["archive_member"]}
            for row in copies:
                path = sources.get(row["source_sha256"])
                reason = "duplicate_file" if path is not None else "duplicate_pixels"
                row.update(status="excluded", reason=reason, duplicate_of=path or first["archive_member"])
                sources.setdefault(row["source_sha256"], row["archive_member"])
    return rows


def write_manifest(path, rows):
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for row in sorted(rows, key=lambda row: row["archive_member"]):
            stream.write(json.dumps(row, sort_keys=True) + "\n")
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_prepared(output, verify_images=False):
    report = json.loads((output / "quality.json").read_bytes())
    manifest = (output / "inventory.jsonl").read_bytes()
    if (
        report["source"]["sha256"] != ARCHIVE_SHA256
        or report["inventory_sha256"] != hashlib.sha256(manifest).hexdigest()
    ):
        raise ValueError("Prepared output does not match its source or saved inventory hash")
    rows = [json.loads(line) for line in manifest.splitlines()]
    if verify_images:
        resolved_output = output.resolve()
        for row in rows:
            if row["status"] != "kept":
                continue
            image = output / row["path"]
            if not image.resolve().is_relative_to(resolved_output):
                raise ValueError(f"Prepared image path escapes the dataset: {image}")
            with image.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != row["source_sha256"]:
                    raise ValueError(f"Prepared image failed its saved SHA-256: {image}")
    return rows, report


def prepare(raw, output):
    archive_path = raw / ARCHIVE_NAME
    verify_archive(archive_path)
    if output.exists():
        read_prepared(output, verify_images=True)
        print(f"Verified and reused {output}")
        return
    with tar_stream(archive_path) as stream:
        layout, files, ancillary = scan_archive(stream, EXPECTED)
    image_bytes = sum(info["bytes"] for info in files.values())
    # Allow up to a 64 KiB allocation unit per image plus space for reports.
    required = image_bytes + len(files) * 65_536 + 32 * 1024**2
    free = shutil.disk_usage(output.anchor).free
    if free < required:
        raise RuntimeError(f"Extraction requires {required:,} bytes free with headroom, found {free:,}")
    print(f"Verified {len(files):,} archive image entries and {image_bytes:,} unpacked image bytes")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".metu-prepare-", dir=output.parent) as work:
        stage = Path(work) / "dataset"
        with tar_stream(archive_path) as stream:
            rows = extract_images(stream, layout, files, stage)
        relations = mark_similarity(rows)
        kept = Counter(row["label"] for row in rows if row["status"] == "kept")
        excluded = Counter(row["reason"] for row in rows if row["status"] == "excluded")
        source_copies = Counter(row["source_sha256"] for row in rows)
        pixel_copies = Counter(row["pixel_sha256"] for row in rows if "pixel_sha256" in row)
        by_label = {
            label: dict(Counter(row["reason"] for row in rows if row["status"] == "excluded" and row["label"] == label))
            for label in EXPECTED
        }
        report = {
            "source": {"doi": DOI, "license": LICENSE, "url": DOWNLOAD_URL, "bytes": ARCHIVE_BYTES, "sha256": ARCHIVE_SHA256},
            "archive_class_counts": EXPECTED,
            "unpacked_image_bytes": image_bytes,
            "extraction_headroom_bytes": required,
            "free_bytes_before_extraction": free,
            "kept_class_counts": {label: kept[label] for label in EXPECTED},
            "exclusion_counts": dict(excluded),
            "exclusions_by_label": by_label,
            "byte_duplicate_groups": sum(count > 1 for count in source_copies.values()),
            "decoded_pixel_duplicate_groups": sum(count > 1 for count in pixel_copies.values()),
            "similarity": relations,
            "numeric_stems": sum(Path(row["archive_member"]).stem.isdecimal() for row in rows),
            "patches_with_exif": sum(row.get("has_exif", False) for row in rows),
            "archive_sidecars": ancillary,
            "pillow_version": PILLOW_VERSION,
            "extractor": subprocess.check_output(["tar.exe", "--version"], text=True).strip(),
            "group_status": "parent_provenance_required",
            "split_status": "awaiting_reviewed_parent_map",
        }
        report["inventory_sha256"] = write_manifest(stage / "inventory.jsonl", rows)
        write_manifest(stage / "exclusions.jsonl", [row for row in rows if row["status"] == "excluded"])
        write_json(stage / "quality.json", report)
        log = (
            f"Verified publisher SHA-256 {ARCHIVE_SHA256}\n"
            f"Listed {len(files):,} expected images ({image_bytes:,} unpacked bytes)\n"
            f"Kept {dict(kept)}; exclusions {dict(excluded)}\n"
            f"Similarity screen {relations}\nAwaiting reviewed parent-photo mapping before splitting\n"
        )
        (stage / "processing.log").write_text(log, encoding="utf-8")
        stage.rename(output)
    print(f"Published quality report and inventory at {output}")
    print(log, end="")


def publish_split(output, parent_file):
    if not parent_file.is_file():
        raise ValueError(f"Reviewed parent mapping is required at {parent_file}")
    if parent_file.resolve() != parent_file.absolute():
        raise ValueError("Parent map must be a regular file in the requested ML folder")
    raw_map = parent_file.read_bytes()
    parent_map = json.loads(raw_map)
    if (
        parent_map.get("archive_sha256") != ARCHIVE_SHA256
        or not isinstance(parent_map.get("provenance"), str)
        or not parent_map["provenance"].strip()
    ):
        raise ValueError("Parent map must pin the source SHA-256 and describe the reviewed provenance")
    map_sha256 = hashlib.sha256(raw_map).hexdigest()
    rows, quality = read_prepared(output, verify_images=True)
    split = output / "parent-grouped-v1"
    if split.exists():
        report = json.loads((split / "split_report.json").read_bytes())
        digest = hashlib.sha256((split / "split_manifest.jsonl").read_bytes()).hexdigest()
        if (
            report["archive_sha256"] != ARCHIVE_SHA256
            or report["inventory_sha256"] != quality["inventory_sha256"]
            or report["parent_map_sha256"] != map_sha256
            or report["split_manifest_sha256"] != digest
        ):
            raise ValueError("The existing split differs from the evidence or manifest. Preserved it.")
        print(f"Verified and reused {split}")
        return
    assigned, counts, parents, groups = split_rows(rows, parent_map["members"])
    report = {
        "archive_sha256": ARCHIVE_SHA256,
        "inventory_sha256": quality["inventory_sha256"],
        "parent_map_sha256": map_sha256,
        "parent_map_provenance": parent_map["provenance"],
        "parents": parents,
        "groups_after_linking": groups,
        "target_ratios": RATIOS,
        "partition_counts": counts,
        "seed": SEED,
    }
    with tempfile.TemporaryDirectory(prefix=".metu-split-", dir=output) as work:
        stage = Path(work) / "split"
        stage.mkdir()
        report["split_manifest_sha256"] = write_manifest(stage / "split_manifest.jsonl", assigned)
        write_json(stage / "split_report.json", report)
        stage.rename(split)
    print(f"Published parent-grouped split with class counts {counts} at {split}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "split"])
    args = parser.parse_args()
    try:
        raw = dataset_path("raw")
        output = dataset_path("processed")
        with dataset_lock(raw):
            if args.command == "prepare":
                prepare(raw, output)
            else:
                publish_split(output, dataset_path("metadata") / "parent-groups.json")
    except (OSError, ValueError, RuntimeError, KeyError, tarfile.TarError, subprocess.SubprocessError) as error:
        sys.exit(f"METU preparation stopped: {error}")


if __name__ == "__main__":
    main()
