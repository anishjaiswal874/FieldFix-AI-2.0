"""Read the pinned FMUCD pilot and apply the 10C label policy without resplitting."""

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

from ml.pilot.acquire_fmucd import load_window, pilot_root, windows
from ml.pilot.validate_fmucd import fingerprint, normalize_description, read_source


AUDIT_HASH = "855dfc16d4d678eb15c84b2f26223bea40b8e2c00c1e835913f0755a75e674df"
SPLIT_HASH = "0d8c751f618a7eaa8b30b74ef349db8f68fb1de716a40704cd03d8495c3474d7"
MAPPING_HASH = "bee1e386c71c96324024f2813f4bdb9b73bfdb7fd0191ba1f8f4d53498d51e27"
SOURCE_DATASET = "10.17632/cb8d2nsjss.1"
MAPPING = Path(__file__).resolve().parents[1] / "pilot" / "fieldfix-nlp-label-map.proposal.json"
UNIVERSITIES = {"train": ["5", "6", "10", "11", "12"], "validation": ["1"], "test": ["4"]}
APPROVED_COUNTS = {
    "train": {"plumbing": 303, "hvac": 320, "electrical": 141},
    "validation": {"plumbing": 25, "hvac": 24, "electrical": 37},
    "test": {"plumbing": 30, "hvac": 15, "electrical": 22},
}
LABEL_ORDER = ["electrical", "hvac", "plumbing"]


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def read_pinned(path, expected):
    data = path.read_bytes()
    if sha256(data) != expected:
        raise ValueError(f"Pinned file changed: {path}")
    return data


def context():
    root = pilot_root()
    raw = root / "raw" / "fmucd" / "v1" / "pilot"
    processed = root / "processed" / "fmucd" / "v1" / "pilot"
    audit = json.loads(read_pinned(processed / "audit.json", AUDIT_HASH))
    manifest = list(map(json.loads, read_pinned(processed / "split_manifest.jsonl", SPLIT_HASH).splitlines()))
    approved = json.loads(read_pinned(MAPPING, MAPPING_HASH))
    if approved["source"] != {"dataset_doi": SOURCE_DATASET, "split_manifest_sha256": SPLIT_HASH}:
        raise ValueError("Mapping points to the wrong dataset or frozen split")
    if audit["split_universities"] != UNIVERSITIES or audit["records"] != 4393 or len(manifest) != 3889:
        raise ValueError("Pilot university or row totals differ from the audited data")
    seen = {}
    for item in manifest:
        fold = item["split"]
        if item["university"] not in UNIVERSITIES[fold]:
            raise ValueError("University is outside its frozen partition")
        for field in ("record_sha256", "description_sha256", "university"):
            if seen.setdefault((field, item[field]), fold) != fold:
                raise ValueError(f"Cross-partition leak detected in {field}")

    # Pin the range metadata without opening any test-window text at training time.
    metadata = {}
    for name, start in windows():
        info = json.loads((raw / f"{name}.json").read_bytes())
        if info["name"] != name or info["start"] != start:
            raise ValueError(f"Unexpected source range: {name}")
        metadata[name] = info
    source = {
        "doi": SOURCE_DATASET,
        "publisher_page": "https://data.mendeley.com/datasets/cb8d2nsjss/1",
        "audit_sha256": AUDIT_HASH,
        "frozen_split_sha256": SPLIT_HASH,
        "reviewed_mapping_sha256": MAPPING_HASH,
        "windows": metadata,
        "window_manifest_sha256": sha256(json.dumps(metadata, sort_keys=True).encode()),
        "university_partitions": UNIVERSITIES,
        "original_counts": {"sampled": 4393, "frozen": len(manifest), "excluded_before_freeze": 504},
        "no_cross_partition_exact_or_normalized_description_matches": True,
    }
    return raw, audit, manifest, approved, source


def acquire_rows(requested, *, frozen_source=None):
    raw, audit, manifest, approved, source = context()
    if frozen_source is not None and source != frozen_source:
        raise ValueError("Source range metadata changed since model freeze")
    pointers = {
        (item["window"], item["line_in_window"]): item for item in manifest if item["split"] in requested
    }
    opened_windows = set()

    def read_window(name, start):
        payload = load_window(raw, name, start)
        if sha256(payload) != source["windows"][name]["sha256"]:
            raise ValueError(f"Window bytes changed during the read: {name}")
        opened_windows.add(name)
        return payload

    header, _ = read_source(read_window("header", 0), "header")
    original = {fold: [] for fold in requested}
    found = set()
    needed = {name for name, _ in pointers}
    for name, start in windows():
        if name not in needed:
            continue
        _, entries = read_source(read_window(name, start), name, header)
        if len(entries) != audit["coverage"][name]["records"]:
            raise ValueError(f"Unexpected record count in {name}")
        for line, values in entries:
            key = name, line
            if key not in pointers:
                continue
            saved = pointers[key]
            row = dict(zip(header, values))
            digest = fingerprint(json.dumps(values, ensure_ascii=False, separators=(",", ":")))
            canonical = fingerprint(normalize_description(row["WODescription"]))
            if (
                digest != saved["record_sha256"]
                or canonical != saved["description_sha256"]
                or row["SystemCode"].strip() != saved["source_system_code"]
                or row["UniversityID"] != saved["university"]
                or row["PPM/UPM"].strip() != saved["maintenance_type"]
            ):
                raise ValueError(f"Frozen row does not match the publisher record at {key}")
            found.add(key)
            original[saved["split"]].append({
                **saved,
                "source_subsystem_code": row["SubsystemCode"].strip(),
                "source_component_code": row["DescriptiveCode"].strip(),
                "work_order_key_sha256": fingerprint(f'{row["UniversityID"]}/{row["WOID"].strip()}'),
                "text": row["WODescription"],
            })
    if found != pointers.keys():
        raise ValueError("Some frozen records were missing from their original ranges")
    return original, approved, source, sorted(opened_windows)


def supervised_rows(original, approved):
    policy = approved["source_row_policy"]
    rules = {
        (item["source_system_code"], subsystem): item["label"]
        for item in approved["labels"]
        for subsystem in item["allowed_source_subsystem_codes"]
    }
    eligible, disposition = {}, {}
    for fold, rows in original.items():
        by_order, by_description = defaultdict(set), defaultdict(set)
        for row in rows:
            by_order[row["work_order_key_sha256"]].add(row["source_system_code"])
            by_description[row["description_sha256"]].add(row["source_system_code"])
        selected = []
        removed = Counter()
        for row in rows:
            label = rules.get((row["source_system_code"], row["source_subsystem_code"]))
            if row["maintenance_type"] != policy["maintenance_type"]:
                removed["non_upm"] += 1
            elif label is None:
                removed["out_of_scope_system_or_subsystem"] += 1
            elif (
                policy["quarantine_work_order_with_multiple_system_codes"] and len(by_order[row["work_order_key_sha256"]]) > 1
                or policy["quarantine_normalized_description_with_multiple_system_codes"] and len(by_description[row["description_sha256"]]) > 1
            ):
                removed["conflicting_source_system"] += 1
            else:
                selected.append({**row, "label": label})
        counts = dict(Counter(row["label"] for row in selected))
        if counts != APPROVED_COUNTS[fold]:
            raise ValueError(f"Labeled counts do not match the 10C audit for {fold}: {counts}")
        eligible[fold] = selected
        disposition[fold] = {
            "frozen_rows": len(rows),
            "eligible_rows": len(selected),
            "by_label": counts,
            "distinct_normalized_descriptions": len({row["description_sha256"] for row in selected}),
            "exclusions": dict(removed),
        }
    return eligible, disposition


def provenance(rows):
    return [{key: value for key, value in row.items() if key != "text"} for row in rows]
