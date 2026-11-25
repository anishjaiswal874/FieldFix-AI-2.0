"""Screen hash neighbors for aligned, spatially distributed image detail."""

import numpy as np
from PIL import Image, ImageFilter


SIDE = 96
HASH_RADII = (0, 1, 2, 4, 6, 8, 10, 12)
# Grayscale NCC, high-pass NCC, per-tile NCC, minimum supported tiles/quadrants.
POLICIES = {
    "loose": (0.90, 0.80, 0.75, 6, 3),
    "standard": (0.96, 0.92, 0.85, 8, 3),
    "strict": (0.98, 0.96, 0.92, 8, 4),
}
TILE_MIN_STD = 2.0


def hash_candidates(digests):
    """Complete all-pairs Hamming screen in bounded-memory upper-triangle blocks."""
    hashes = np.asarray(digests, dtype=np.uint64)
    for start in range(0, len(hashes), 192):
        block = np.bitwise_count(hashes[start : start + 192, None] ^ hashes[None, start:])
        left, right = np.nonzero(block <= HASH_RADII[-1])
        upper = left < right
        left, right = left[upper], right[upper]
        distances = block[left, right]
        yield from zip((start + left).tolist(), (start + right).tolist(), distances.tolist())


def features(image):
    gray = image.convert("L").resize((SIDE, SIDE), Image.Resampling.LANCZOS)
    pixels = np.array(gray, dtype=np.uint8)
    detail = pixels.astype(np.int16) - np.asarray(gray.filter(ImageFilter.GaussianBlur(2)), dtype=np.int16)
    coarse = detail.reshape(32, 3, 32, 3).mean(axis=(1, 3), dtype=np.float32)
    return pixels, detail, coarse


def center(values):
    values = np.asarray(values, dtype=np.float32)
    return values - values.mean()


def correlation(a, b):
    a, b = center(a).ravel(), center(b).ravel()
    denom = np.sqrt(np.dot(a, a) * np.dot(b, b))
    return float(np.clip(np.dot(a, b) / denom, -1, 1)) if denom else 0.0


def align(first, second):
    # Five by five proposals at 32 px, then refine around the best on the 96 px grid.
    template = first[2][3:29, 3:29]
    best = max(
        ((dy, dx) for dy in range(-2, 3) for dx in range(-2, 3)),
        key=lambda p: correlation(template, second[2][3+p[0] : 29+p[0], 3+p[1] : 29+p[1]]),
    )
    offsets = {(0, 0)}
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            offsets.add((3 * best[0] + dy, 3 * best[1] + dx))
    reference = first[1][8:88, 8:88]
    return max(
        sorted(offsets, key=lambda p: (abs(p[0]) + abs(p[1]), p)),
        key=lambda p: correlation(reference, second[1][8+p[0] : 88+p[0], 8+p[1] : 88+p[1]]),
    )


def tiles(detail):
    return detail.reshape(4, 20, 4, 20).transpose(0, 2, 1, 3).reshape(16, 400)


def compare(first, second):
    dy, dx = align(first, second)
    interior = np.s_[8:88, 8:88]
    matched = np.s_[8+dy : 88+dy, 8+dx : 88+dx]
    gray = correlation(first[0][interior], second[0][matched])
    detail = correlation(first[1][interior], second[1][matched])
    a, b = tiles(first[1][interior]), tiles(second[1][matched])
    a_std, b_std = a.std(axis=1), b.std(axis=1)
    informative = (a_std >= TILE_MIN_STD) & (b_std >= TILE_MIN_STD)
    a, b = a - a.mean(axis=1, keepdims=True), b - b.mean(axis=1, keepdims=True)
    local = np.divide(
        (a * b).mean(axis=1), a_std * b_std,
        out=np.zeros(16), where=informative,
    )
    evidence = {}
    accepted = []
    for name, (min_gray, min_detail, min_tile, min_tiles, min_quadrants) in POLICIES.items():
        support = np.flatnonzero(informative & (local >= min_tile))
        quadrants = {(int(i) // 8, (int(i) % 4) // 2) for i in support}
        evidence[name] = [len(support), len(quadrants)]
        if gray >= min_gray and detail >= min_detail and len(support) >= min_tiles and len(quadrants) >= min_quadrants:
            accepted.append(name)
    return {
        "shift_yx": [dy, dx],
        "grayscale_ncc": round(gray, 6),
        "detail_ncc": round(detail, 6),
        "informative_tiles": int(informative.sum()),
        "support_tiles_and_quadrants": evidence,
        "accepted_policies": accepted,
    }
