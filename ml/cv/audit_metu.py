"""Audit METU visual repeats locally and prepare a leakage-limited partition."""

import argparse
from collections import Counter, defaultdict
import hashlib
from io import BytesIO
import json
from pathlib import Path
import random
import sys
import tempfile

import numpy as np
from PIL import Image, __version__ as PILLOW_VERSION

from ml.cv.acquire_metu import ARCHIVE_NAME, ARCHIVE_SHA256, dataset_lock, dataset_path, verify_archive, write_json
from ml.cv.metu_groups import RATIOS, Groups
from ml.cv.metu_overlap import HASH_RADII, POLICIES, TILE_MIN_STD, compare, features, hash_candidates
from ml.cv.prepare_metu import read_prepared, tar_stream, write_manifest


AUDIT_VERSION = "leakage-audit-10e-v1"
SPLIT_VERSION = "provisional-10e-v2"
SPLIT_SEED = "metu-v2-fieldfix-10e-v2"
CONTROL_SEED = 51010


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verified_inventory(output):
    rows, quality = read_prepared(output)
    excluded = [row for row in rows if row["status"] == "excluded"]
    members = {row["archive_member"]: row for row in rows}
    kept = [row for row in rows if row["status"] == "kept"]
    saved_exclusions = (output / "exclusions.jsonl").read_bytes().splitlines()
    if len(members) != len(rows) or excluded != list(map(json.loads, saved_exclusions)):
        raise ValueError("The source inventory and original exclusions disagree")
    if dict(Counter(row["label"] for row in kept)) != quality["kept_class_counts"]:
        raise ValueError("The retained inventory does not match the original class counts")
    if len({row["source_sha256"] for row in kept}) != len(kept) or len({row["pixel_sha256"] for row in kept}) != len(kept):
        raise ValueError("The retained inventory still contains exact duplicates")
    for row in excluded:
        canonical = members[row["duplicate_of"]]
        if (
            row["reason"] != "duplicate_file"
            or canonical["status"] != "kept"
            or any(row[key] != canonical[key] for key in ("label", "source_sha256", "pixel_sha256"))
        ):
            raise ValueError(f"Unresolved alias or label conflict: {row['archive_member']}")
    if any(row["split"] is not None or row["parent_group"] is not None for row in rows):
        raise ValueError("Expected the original inventory with unassigned groups and splits")
    return kept, excluded, quality


def load_features(archive, rows, needed):
    by_name = {rows[i]["archive_member"]: i for i in needed}
    result = {}
    with tar_stream(archive) as source:
        for entry in source:
            name = entry.name.replace("\\", "/")
            if name not in by_name:
                continue
            i = by_name[name]
            data = source.extractfile(entry).read()
            if hashlib.sha256(data).hexdigest() != rows[i]["source_sha256"]:
                raise ValueError(f"Archive patch does not match the original inventory: {name}")
            with Image.open(BytesIO(data)) as image:
                result[i] = features(image)
    if result.keys() != needed:
        raise ValueError("Some audit images were missing from the verified archive")
    return result


def connected(rows, edges):
    graph = Groups(range(len(rows)))
    for first, second in edges:
        graph.join(first, second)
    components = defaultdict(list)
    for i in range(len(rows)):
        components[graph.find(i)].append(i)
    return graph, components


def graph_summary(rows, edges):
    graph, components = connected(rows, edges)
    direct_edges = Counter(graph.find(first) for first, _ in edges)
    sizes = Counter(map(len, components.values()))
    return {
        "edges": len(edges),
        "cross_label_edges": sum(rows[first]["label"] != rows[second]["label"] for first, second in edges),
        "component_sizes": dict(sorted(sizes.items())),
        "largest_component": max(sizes),
        "non_clique_components": sum(
            direct_edges[group] < len(ids) * (len(ids) - 1) // 2 for group, ids in components.items()
        ),
        "mixed_label_components": sum(len({rows[i]["label"] for i in ids}) > 1 for ids in components.values()),
    }


def control_checks(selected, images, different_pairs):
    counts = defaultdict(Counter)
    for i in selected:
        original = images[i]
        pixels = original[0]
        image = Image.fromarray(pixels)
        with BytesIO() as stream:
            image.save(stream, "JPEG", quality=70)
            stream.seek(0)
            with Image.open(stream) as recoded:
                comparisons = {"jpeg_quality_70": compare(original, features(recoded))}
        shift = np.pad(pixels, 3, mode="reflect")[1:97, 6:102]
        comparisons["shift_crop_2y_3x"] = compare(original, features(Image.fromarray(shift)))
        lifted = np.minimum(pixels.astype(np.int16) + 12, 255).astype(np.uint8)
        comparisons["brightness_plus_12"] = compare(original, features(Image.fromarray(lifted)))
        for name, result in comparisons.items():
            counts[name]["tested"] += 1
            counts[name].update(result["accepted_policies"])
    for first, second in different_pairs:
        result = compare(images[first], images[second])
        counts["random_cross_class_pairs"]["tested"] += 1
        counts["random_cross_class_pairs"].update(result["accepted_policies"])
    return {name: {key: values[key] for key in ("tested", *POLICIES)} for name, values in sorted(counts.items())}


def audit(archive, output, *, reproduce=False):
    target = output / AUDIT_VERSION
    if target.exists() and not reproduce:
        return verify_audit(output)
    verify_archive(archive)
    rows, excluded, quality = verified_inventory(output)
    candidates = list(hash_candidates([int(row["dhash"], 16) for row in rows]))
    rng = random.Random(CONTROL_SEED)
    by_label = {label: [i for i, row in enumerate(rows) if row["label"] == label] for label in ("crack", "no_crack")}
    selected = [i for members in by_label.values() for i in rng.sample(members, 24)]
    random_cross = list(zip(rng.sample(by_label["crack"], 48), rng.sample(by_label["no_crack"], 48)))
    needed = {i for a, b, _ in candidates for i in (a, b)} | set(selected) | {i for pair in random_cross for i in pair}
    print(f"Found {len(candidates):,} hash candidate pairs. Reading {len(needed):,} source patches from the local RAR.", flush=True)
    images = load_features(archive, rows, needed)
    with tempfile.TemporaryDirectory(prefix=".metu-leakage-", dir=output) as temp:
        stage = Path(temp) / "audit"
        stage.mkdir()
        path = stage / "pair_evidence.jsonl"
        accepted = {policy: [] for policy in POLICIES}
        with path.open("w", encoding="utf-8", newline="\n") as stream:
            for n, (a, b, distance) in enumerate(candidates, 1):
                result = compare(images[a], images[b])
                for policy in result["accepted_policies"]:
                    accepted[policy].append((a, b, distance))
                evidence = {
                    "first": rows[a]["archive_member"],
                    "second": rows[b]["archive_member"],
                    "hash_distance": distance,
                    "same_publisher_label": rows[a]["label"] == rows[b]["label"],
                    **result,
                }
                stream.write(json.dumps(evidence, sort_keys=True) + "\n")
                if n % 2_500 == 0:
                    print(f"Compared {n:,}/{len(candidates):,} visual candidates.", flush=True)
        report = {
            "version": AUDIT_VERSION,
            "archive_sha256": ARCHIVE_SHA256,
            "inventory_sha256": quality["inventory_sha256"],
            "exclusions_sha256": sha256(output / "exclusions.jsonl"),
            "grouping_audit_sha256": sha256(output / "grouping_audit.json"),
            "pair_evidence_sha256": sha256(path),
            "kept_by_class": quality["kept_class_counts"],
            "exact_same_label_aliases": len(excluded),
            "exact_cross_label_conflicts": 0,
            "thresholds_gray_ncc_detail_ncc_tile_ncc_tiles_quadrants": POLICIES,
            "tile_minimum_highpass_std": TILE_MIN_STD,
            "hash_radius_sweep": {
                radius: {
                    "hash_only": graph_summary(rows, [(a, b) for a, b, d in candidates if d <= radius]),
                    "standard_overlap": graph_summary(rows, [(a, b) for a, b, d in accepted["standard"] if d <= radius]),
                } for radius in HASH_RADII
            },
            "overlap_policy_sweep_at_max_radius": {
                policy: {
                    **graph_summary(rows, [(a, b) for a, b, _ in edges]),
                    "largest_hash_distance": max((distance for _, _, distance in edges), default=None),
                } for policy, edges in accepted.items()
            },
            "comparator_controls": control_checks(selected, images, random_cross),
            "control_seed": CONTROL_SEED,
            "versions": {"numpy": np.__version__, "pillow": PILLOW_VERSION},
            "parent_photo_verification": "unresolved",
        }
        write_json(stage / "audit_report.json", report)
        if target.exists():
            verify_audit(output)
            if any(sha256(target / path.name) != sha256(path) for path in stage.iterdir()):
                raise ValueError("Recomputed overlap evidence differs from the saved audit. Preserved the saved version.")
            print("Reproduction verified. Pair evidence and the audit report are byte-identical.")
        else:
            stage.rename(target)
            print(f"Published {target}")
    return report


def verify_audit(output):
    target = output / AUDIT_VERSION
    report = json.loads((target / "audit_report.json").read_bytes())
    for path, key in (
        (output / "inventory.jsonl", "inventory_sha256"),
        (output / "exclusions.jsonl", "exclusions_sha256"),
        (output / "grouping_audit.json", "grouping_audit_sha256"),
        (target / "pair_evidence.jsonl", "pair_evidence_sha256"),
    ):
        if sha256(path) != report[key]:
            raise ValueError(f"Saved audit evidence does not match {path}")
    if report["version"] != AUDIT_VERSION or report["archive_sha256"] != ARCHIVE_SHA256:
        raise ValueError("Saved audit has unexpected version or source")
    return report


def stratified_groups(components, rows):
    """Balance both class labels and group cardinalities without splitting groups."""
    strata = defaultdict(list)
    for group, members in components.items():
        labels = {rows[i]["label"] for i in members}
        if len(labels) != 1:
            raise ValueError("Mixed labels inside an eligible overlap group")
        strata[(next(iter(labels)), len(members))].append(group)

    assignments = {}
    fold_counts = {fold: Counter() for fold in RATIOS}
    for (label, size), groups in sorted(strata.items()):
        def rank(value):
            return hashlib.sha256(f"{SPLIT_SEED}/{label}/{size}/{value}".encode()).hexdigest()

        groups.sort(key=lambda group: rank(rows[group]["archive_member"]))
        quotas = {fold: divmod(len(groups) * round(ratio * 10), 10) for fold, ratio in RATIOS.items()}
        remainders = len(groups) - sum(whole for whole, _ in quotas.values())
        extra = set(sorted(RATIOS, key=lambda fold: (-quotas[fold][1], rank(fold)))[:remainders])
        start = 0
        for fold in RATIOS:
            count = quotas[fold][0] + int(fold in extra)
            for group in groups[start : start + count]:
                assignments[group] = fold
            fold_counts[fold][label] += count * size
            start += count
    if any(set(counts) != {"crack", "no_crack"} or min(counts.values()) < 1 for counts in fold_counts.values()):
        raise ValueError("The allocation cannot represent both classes in each partition")
    return assignments, {fold: dict(counts) for fold, counts in fold_counts.items()}


def partition(rows, evidence):
    indexes = {row["archive_member"]: i for i, row in enumerate(rows)}
    standard, loose = [], []
    for pair in evidence:
        first, second = indexes[pair["first"]], indexes[pair["second"]]
        policies = pair["accepted_policies"]
        if "standard" in policies:
            standard.append((first, second))
        if "loose" in policies:
            loose.append((first, second))
    graph, components = connected(rows, standard)
    quarantine = defaultdict(set)
    for first, second in loose:
        first_group, second_group = graph.find(first), graph.find(second)
        if first_group != second_group:
            for group in (first_group, second_group):
                quarantine[group].add("borderline_overlap_between_components")
        if rows[first]["label"] != rows[second]["label"]:
            for group in (first_group, second_group):
                quarantine[group].add("possible_cross_label_overlap")

    eligible = {group: members for group, members in components.items() if group not in quarantine}
    assignments, counts = stratified_groups(eligible, rows)
    parts = {fold: [] for fold in RATIOS}
    held = []
    for group, members in components.items():
        for i in members:
            source = rows[i]
            row = {key: source[key] for key in ("archive_member", "path", "label", "source_sha256", "pixel_sha256")}
            row.update(
                overlap_group=rows[group]["archive_member"],
                overlap_group_size=len(members),
                parent_group=None,
                split=assignments.get(group),
                evaluation_scope="provisional_leakage_limited",
            )
            if group in quarantine:
                held.append({**row, "reasons": sorted(quarantine[group])})
            else:
                parts[row["split"]].append(row)
    destination = {row["archive_member"]: fold for fold, members in parts.items() for row in members}
    if len(destination) + len(held) != len(rows) or len(destination) != sum(map(len, parts.values())):
        raise ValueError("The partition lost or repeated some retained records")
    checked = {}
    for name, pairs in (("standard", standard), ("loose", loose)):
        both_kept = [(destination[rows[a]["archive_member"]], destination[rows[b]["archive_member"]])
                     for a, b in pairs if rows[a]["archive_member"] in destination and rows[b]["archive_member"] in destination]
        if any(first != second for first, second in both_kept):
            raise ValueError(f"An observed {name} overlap crosses two partitions")
        checked[name] = len(both_kept)
    return parts, held, counts, destination, checked


def publish_provisional(output):
    report = verify_audit(output)
    rows, excluded, quality = verified_inventory(output)
    source = output / AUDIT_VERSION / "pair_evidence.jsonl"
    with source.open(encoding="utf-8") as stream:
        parts, held, counts, destination, checked = partition(rows, map(json.loads, stream))
    target = output / SPLIT_VERSION
    with tempfile.TemporaryDirectory(prefix=".metu-provisional-", dir=output) as temp:
        stage = Path(temp) / "split"
        stage.mkdir()
        manifests = {f"{fold}.jsonl": write_manifest(stage / f"{fold}.jsonl", members) for fold, members in parts.items()}
        manifests["quarantine.jsonl"] = write_manifest(stage / "quarantine.jsonl", held)
        alias_counts = Counter(destination.get(row["duplicate_of"], "quarantine") for row in excluded)
        split_report = {
            "version": SPLIT_VERSION,
            "evaluation_scope": "provisional_leakage_limited",
            "parent_photo_independence": "unverified",
            "archive_sha256": ARCHIVE_SHA256,
            "inventory_sha256": quality["inventory_sha256"],
            "exclusions_sha256": report["exclusions_sha256"],
            "pair_evidence_sha256": report["pair_evidence_sha256"],
            "seed": SPLIT_SEED,
            "target_ratios": RATIOS,
            "allocation": "stratified by publisher class and overlap component size",
            "partition_counts": counts,
            "quarantined_by_class": dict(Counter(row["label"] for row in held)),
            "quarantine_groups": len({row["overlap_group"] for row in held}),
            "groups_per_partition": {fold: len({row["overlap_group"] for row in members}) for fold, members in parts.items()},
            "component_sizes_by_partition": {
                fold: dict(sorted(Counter({row["overlap_group"]: row["overlap_group_size"] for row in members}.values()).items()))
                for fold, members in parts.items()
            },
            "unassigned_exact_aliases_by_canonical_destination": dict(alias_counts),
            "overlap_edges_with_both_endpoints_in_partitions": checked,
            "observed_standard_or_loose_edges_crossing_partitions": 0,
            "exact_duplicates_or_mixed_overlap_group_labels_in_partitions": 0,
            "manifest_sha256": manifests,
        }
        write_json(stage / "split_report.json", split_report)
        if target.exists():
            if {p.name for p in target.iterdir()} != {p.name for p in stage.iterdir()} or any(
                sha256(target / path.name) != sha256(path) for path in stage.iterdir()
            ):
                raise ValueError("Reproduced files differ from the frozen provisional split. Preserved the published version.")
            print("Reproduction verified. All four manifests and the split report are byte-identical.", flush=True)
        else:
            stage.rename(target)
            print(f"Published {target}", flush=True)
        return split_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["audit", "split"])
    parser.add_argument("--reproduce", action="store_true", help="Recompute the audit and compare it with the saved evidence")
    args = parser.parse_args()
    if args.reproduce and args.command != "audit":
        parser.error("--reproduce applies to audit. The split command always checks reproduction if a version exists.")
    try:
        raw = dataset_path("raw")
        output = dataset_path("processed")
        with dataset_lock(raw):
            if args.command == "audit":
                report = audit(raw / ARCHIVE_NAME, output, reproduce=args.reproduce)
                print(json.dumps(report["overlap_policy_sweep_at_max_radius"], indent=2))
            else:
                print(json.dumps(publish_provisional(output), indent=2))
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        sys.exit(f"METU leakage audit stopped: {error}")


if __name__ == "__main__":
    main()
