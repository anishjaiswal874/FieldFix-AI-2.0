"""Find likely duplicate clusters and split disjoint, documented parent groups."""

from collections import Counter, defaultdict
import hashlib


RATIOS = {"train": 0.8, "validation": 0.1, "test": 0.1}
SEED = "metu-v2-fieldfix-10d"
PUBLISHED_PARENT_PHOTOS = 458


class Groups:
    def __init__(self, ids):
        self.parent = {value: value for value in ids}

    def find(self, value):
        while self.parent[value] != value:
            self.parent[value] = self.parent[self.parent[value]]
            value = self.parent[value]
        return value

    def join(self, first, second):
        first, second = sorted((self.find(first), self.find(second)))
        self.parent[second] = first


def mark_similarity(rows):
    """Link equal or one-bit-neighbor dHashes as review candidates."""
    by_hash = defaultdict(list)
    for row in rows:
        if "dhash" in row:
            by_hash[int(row["dhash"], 16)].append(row)
    groups = Groups(by_hash)
    for digest in by_hash:
        for bit in range(64):
            neighbor = digest ^ (1 << bit)
            if neighbor < digest and neighbor in by_hash:
                groups.join(digest, neighbor)
    linked = defaultdict(list)
    for digest, records in by_hash.items():
        linked[groups.find(digest)].extend(records)
    mixed = 0
    related = 0
    for group, records in linked.items():
        if len(records) < 2:
            continue
        mixed += len({row["label"] for row in records}) > 1
        related += 1
        for row in records:
            row["similarity_group"] = f"dhash-{group:016x}"
    return {"candidate_groups": related, "mixed_label_candidate_groups": mixed}


def allocation(by_group):
    """Reserve class coverage, then assign large groups toward the targets."""
    classes = ("crack", "no_crack")
    group_counts = {group: Counter(row["label"] for row in rows) for group, rows in by_group.items()}
    support = Counter(label for labels in group_counts.values() for label in labels)
    if any(support[label] < len(RATIOS) for label in classes):
        raise ValueError("The documented groups cannot populate three two-class partitions")
    totals = Counter()
    for labels in group_counts.values():
        totals.update(labels)
    counts = {fold: Counter() for fold in RATIOS}
    assignments = {}
    order = sorted(
        by_group,
        key=lambda group: (-len(by_group[group]), hashlib.sha256(f"{SEED}/{group}".encode()).hexdigest()),
    )

    def assign(group, fold):
        assignments[group] = fold
        counts[fold].update(group_counts[group])

    # Small seed groups leave the larger groups free to balance the totals.
    seed_order = ("validation", "test", "train")
    mixed = [group for group in reversed(order) if len(group_counts[group]) == len(classes)]
    for fold, group in zip(seed_order, mixed):
        assign(group, fold)
    for label in classes:
        pure = [
            group for group in order
            if len(group_counts[group]) == 1 and label in group_counts[group]
        ]
        for fold in seed_order:
            if not counts[fold][label]:
                assign(pure.pop(), fold)

    for group in order:
        if group in assignments:
            continue
        labels = group_counts[group]

        def score(fold):
            delta = 0.0
            for label in classes:
                before = counts[fold][label] / totals[label] - RATIOS[fold]
                after = before + labels[label] / totals[label]
                delta += after**2 - before**2
            return delta

        assign(group, min(RATIOS, key=score))
    return assignments, {fold: dict(counts[fold]) for fold in RATIOS}


def split_rows(rows, parents):
    """Keep reviewed parents and duplicate/similarity clusters within one fold."""
    valid = [row for row in rows if "pixel_sha256" in row]
    known = {row["archive_member"] for row in rows}
    if not isinstance(parents, dict) or not set(parents) <= known:
        raise ValueError("Parent map refers to members outside the prepared inventory")
    for row in valid:
        value = parents.get(row["archive_member"])
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            raise ValueError(f"Missing or invalid reviewed parent ID for {row['archive_member']}")
    parent_ids = {parents[row["archive_member"]] for row in valid}
    if len(parent_ids) > PUBLISHED_PARENT_PHOTOS:
        raise ValueError("Parent IDs exceed the release's 458 original photographs; check the grouping evidence")
    groups = Groups(parent_ids)
    seen = {}
    for row in valid:
        parent = parents[row["archive_member"]]
        for field in ("pixel_sha256", "similarity_group"):
            if row.get(field):
                other = seen.setdefault((field, row[field]), parent)
                groups.join(parent, other)
    linked = defaultdict(list)
    for row in rows:
        if row["status"] == "kept":
            parent = parents[row["archive_member"]]
            linked[groups.find(parent)].append(row)
    assignments, counts = allocation(linked)
    result = [
        {
            **row,
            "parent_group": parents[row["archive_member"]],
            "split_group": group,
            "split": assignments[group],
        }
        for group, records in linked.items()
        for row in records
    ]
    return result, counts, len(parent_ids), len(linked)
