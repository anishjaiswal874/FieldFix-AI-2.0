"""Validate the cached FMUCD ranges and write a reproducible pilot split manifest."""

from collections import Counter, defaultdict
import csv
import hashlib
import json
import re
from statistics import median
import unicodedata

if __package__:
    from .acquire_fmucd import atomic_write, load_window, pilot_root, windows
else:
    from acquire_fmucd import atomic_write, load_window, pilot_root, windows


MISSING = {"", "nan", "null", "n/a", "#n/a", "#ref!"}
RECORD_START = re.compile(rb"(?:[1-9]|1[0-2]),(?:Canada|USA),")
SOURCE_FIELDS = {
    "UniversityID", "Country", "WOID", "WODescription", "WOPriority", "PPM/UPM",
    "SystemCode", "SystemDescription", "SubsystemCode", "SubsystemDescription",
    "DescriptiveCode", "ComponentDescription",
}
SPLIT_SEED = "fieldfix-10b-v1"


def missing(value):
    return value.strip().casefold() in MISSING


def normalize_description(value):
    return " ".join(unicodedata.normalize("NFC", value).casefold().split())


def fingerprint(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def read_source(data, name, header=None):
    # Discard incomplete edge lines. A quoted CSV record can span physical lines,
    # so synchronize on a dataset-specific row prefix, then use the CSV parser.
    lines = data.split(b"\n")[:-1]
    if name == "header":
        first_line = 1
        decoded = lines[0].decode("utf-8-sig")
        header = next(csv.reader([decoded], strict=True))
        if len(header) != 38 or len(set(header)) != 38 or not SOURCE_FIELDS <= set(header):
            raise ValueError("FMUCD header does not match the expected schema")
    else:
        first_line = next(
            (i for i, line in enumerate(lines[1:], 1) if RECORD_START.match(line)),
            len(lines),
        )
    body = lines[first_line:]
    reader = csv.reader((line.decode("utf-8") + "\n" for line in body), strict=True)
    rows = []
    while True:
        start = reader.line_num
        try:
            values = next(reader)
        except StopIteration:
            break
        except csv.Error as error:
            # The slice can end inside a quoted field with embedded newlines.
            if (
                str(error) == "unexpected end of data"
                and reader.line_num == len(body)
                and not any(RECORD_START.match(line) for line in body[start + 1 :])
            ):
                break
            raise
        if len(values) != len(header) or not RECORD_START.match(body[start]):
            raise ValueError(f"{name}: invalid CSV record at window line {first_line + start + 1}")
        rows.append((first_line + start + 1, values))
    if len(rows) < 3:
        raise ValueError(f"{name}: too few consecutive records to validate the window")
    return header, rows


def split_rows(candidates):
    # Hold out whole universities. Use a stable hash so input order and Python's
    # per-process hash seed cannot change the assignment.
    universities = sorted(
        {row["university"] for row in candidates},
        key=lambda university: (fingerprint(f"{SPLIT_SEED}/{university}"), university),
    )
    if len(universities) < 3:
        raise ValueError("Three universities are needed for the pilot split")
    groups = {
        "train": sorted(universities[2:], key=int),
        "validation": [universities[1]],
        "test": [universities[0]],
    }
    assignment = {
        university: split for split, members in groups.items() for university in members
    }
    descriptions = defaultdict(set)
    for row in candidates:
        descriptions[row["description_sha256"]].add(assignment[row["university"]])
    shared = {digest for digest, splits in descriptions.items() if len(splits) > 1}
    result = [
        {**row, "split": assignment[row["university"]]}
        for row in candidates
        if row["description_sha256"] not in shared
    ]
    if set(groups) != {row["split"] for row in result}:
        raise ValueError("A pilot partition is empty after cross-split deduplication")
    return groups, result, len(shared)


def main():
    root = pilot_root()
    source = root / "raw" / "fmucd" / "v1" / "pilot"
    destination = root / "processed" / "fmucd" / "v1" / "pilot"
    header = None
    coverage = {}
    missing_fields = Counter()
    system_labels = Counter()
    unplanned_system_labels = Counter()
    subsystem_labels = Counter()
    component_labels = Counter()
    maintenance_types = Counter()
    descriptions = Counter()
    description_labels = defaultdict(set)
    work_orders = Counter()
    work_order_labels = defaultdict(set)
    full_records = set()
    candidates = []
    short_descriptions = 0
    descriptions_without_letters = 0

    for name, start in windows():
        data = load_window(source, name, start)
        header, rows = read_source(data, name, header)
        universities = set()
        for line, values in rows:
            row = dict(zip(header, values))
            university = row["UniversityID"]
            universities.add(university)
            missing_fields.update(key for key, value in row.items() if missing(value))
            kind = row["PPM/UPM"].strip()
            if missing(kind):
                kind = "missing"
            maintenance_types[kind] += 1
            system = row["SystemCode"].strip()
            label = (system, row["SystemDescription"].strip())
            system_labels[label] += 1
            if kind == "UPM":
                unplanned_system_labels[label] += 1
            subsystem_labels[(row["SubsystemCode"].strip(), row["SubsystemDescription"].strip())] += 1
            component_labels[(row["DescriptiveCode"].strip(), row["ComponentDescription"].strip())] += 1
            description = normalize_description(row["WODescription"])
            has_description = not missing(description)
            has_letters = has_description and any(character.isalpha() for character in description)
            if has_description:
                descriptions[description] += 1
                short_descriptions += len(description.split()) <= 2
                descriptions_without_letters += not has_letters
                if not missing(system):
                    description_labels[description].add(system)

            order_id = row["WOID"].strip()
            if not missing(order_id):
                order = (university, order_id)
                work_orders[order] += 1
                if not missing(system):
                    work_order_labels[order].add(system)

            record_sha = fingerprint(json.dumps(values, ensure_ascii=False, separators=(",", ":")))
            if record_sha in full_records:
                continue
            full_records.add(record_sha)
            if has_letters and not missing(order_id) and not missing(system):
                candidates.append({
                    "window": name,
                    "line_in_window": line,
                    "record_sha256": record_sha,
                    "description_sha256": fingerprint(description),
                    "university": university,
                    "source_system_code": system,
                    "maintenance_type": kind,
                })
        coverage[name] = {"records": len(rows), "universities": sorted(universities, key=int)}

    count = sum(section["records"] for section in coverage.values())
    groups, split, shared_descriptions = split_rows(candidates)
    split_system_counts = defaultdict(Counter)
    for row in split:
        split_system_counts[row["split"]][row["source_system_code"]] += 1
    report = {
        "fields": header,
        "records": count,
        "coverage": coverage,
        "missing_by_field": {name: missing_fields[name] for name in header},
        "maintenance_types": dict(maintenance_types),
        "system_codes_and_labels": system_labels.most_common(),
        "unplanned_system_codes_and_labels": unplanned_system_labels.most_common(),
        "top_subsystem_codes_and_labels": subsystem_labels.most_common(15),
        "top_component_codes_and_labels": component_labels.most_common(15),
        "extra_exact_duplicate_rows": count - len(full_records),
        "repeated_university_work_order_keys": sum(n > 1 for n in work_orders.values()),
        "work_order_keys_with_multiple_system_codes": sum(len(codes) > 1 for codes in work_order_labels.values()),
        "repeated_normalized_descriptions": sum(n > 1 for n in descriptions.values()),
        "descriptions_with_multiple_system_codes": sum(len(codes) > 1 for codes in description_labels.values()),
        "descriptions_at_most_two_words": short_descriptions,
        "descriptions_without_letters": descriptions_without_letters,
        "median_description_words": median(
            len(text.split()) for text, occurrences in descriptions.items() for _ in range(occurrences)
        ),
        "rows_with_usable_description_order_and_code_after_deduplication": len(candidates),
        "split_seed": SPLIT_SEED,
        "split_universities": groups,
        "split_counts": dict(Counter(row["split"] for row in split)),
        "split_source_code_counts": dict(split_system_counts),
        "shared_descriptions_excluded": shared_descriptions,
        "rows_excluded_for_shared_descriptions": len(candidates) - len(split),
    }
    atomic_write(destination / "audit.json", (json.dumps(report, indent=2) + "\n").encode())
    manifest = "".join(json.dumps(record, sort_keys=True) + "\n" for record in split)
    atomic_write(destination / "split_manifest.jsonl", manifest.encode())
    print(f"Validated {count} records in {len(coverage)} ranges")
    print(f"Pilot split: {report['split_counts']}")
    print(f"Reports: {destination}")


if __name__ == "__main__":
    main()
