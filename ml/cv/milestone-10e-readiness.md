# Milestone 10E | METU leakage audit and evaluation readiness

**Use `provisional-10e-v2` for an exploratory ResNet18 baseline in Milestone 12.** The split isolates every spatial match retained by the audit. Its evaluation scope is `provisional_leakage_limited`. A claim about unseen photos, surfaces, structures, or sites requires a separate held-out evaluation with documented source groups.

## Source integrity and parent-photo evidence

The local RAR matched the publisher's full SHA-256. I matched the original inventory to the original exclusions and rechecked retained class counts and hash uniqueness. The 1,598 excluded aliases point to the same class, byte hash, and decoded-pixel hash as their canonical image. They represent 1,519 exact duplicate groups. There were zero exact cross-label conflicts. The starting pool was 19,684 cracked and 18,718 non-cracked patches. [METU v2 release](https://data.mendeley.com/datasets/5y9wdsg2zt/2) · [Published file metadata](https://data.mendeley.com/public-api/datasets/5y9wdsg2zt/files?folder_id=root&version=2)

The publisher describes 458 parent photos. The classification archive contains no mapping sidecar, and the earlier JPEG metadata scan found JFIF fields without parent identifiers. Both labels reuse all 20,000 numeric base IDs, and some known exact copies are 18,667 IDs apart. The `_1` filename suffix covers a range of cracked patches without a published parent meaning. The authors' paper describes random selection from the base patches and separate testing on new sites, with no per-patch parent map listed. The separately catalogued segmentation release contains the original high-resolution photos and masks, with no standalone mapping file in the listing examined in 10D. Parent identity therefore remains unresolved. The companion archive was outside this audit. [Authors' paper, Section 3](https://www.iaarc.org/publications/fulltext/ISARC2018-Paper154.pdf#page=3) · [Companion segmentation release](https://data.mendeley.com/datasets/jwsn7tfbrp/1)

## Repeat detection and visual review

1. Enumerate pairs of retained patches at 64-bit dHash Hamming distance up to 12, including both within-label and cross-label pairs. Use NumPy popcount in upper-triangle blocks. The hash is a candidate filter.
2. Compare 96 × 96 grayscale patches streamed from the checksum-verified RAR. Estimate a small translation from a 32 × 32 high-pass representation and refine on the 96-pixel grid up to seven pixels per axis. Measure normalized cross-correlation on an 80 × 80 interior for grayscale and high-pass detail. Also compare 16 local tiles. Only count a tile when both high-pass standard deviations reach 2 on the 8-bit intensity scale. Require agreement across several quadrants so a flat gradient or a single common texture detail provides insufficient evidence.
3. Connect pairs that meet the standard policy. Quarantine both standard components when an edge passes only the loose policy and bridges those components. An already connected loose-only pair stays with its component. Quarantine any component touched by a spatially supported label conflict. The actual spatial comparisons found zero such conflicts.

I visually spot-checked 12 genuine pairs from the accepted, borderline, and rejected bands. `Negative/00537.jpg` and `Negative/13333.jpg` share several aggregate details after a small translation, with dHash distance 8 and detail correlation 0.963. The borderline pair `Positive/01686.jpg` and `Positive/07275.jpg` shows a matching crack fragment under a shift, supporting the decision to quarantine that band. Conversely, `Negative/15921.jpg` and `Negative/16035.jpg` have the same dHash but show distinct detail with correlation 0.049. The examples illustrate both genuine overlap and hash collisions. They are a diagnostic spot check.

### Threshold sensitivity

| dHash radius | Hash candidates | Cross-label hash candidates | Largest hash-only component | Standard spatial matches |
|---:|---:|---:|---:|---:|
| 0 | 2,604 | 0 | 4 | 2,585 |
| 1 | 4,066 | 0 | 4 | 4,017 |
| 2 | 4,754 | 0 | 7 | 4,663 |
| 4 | 5,131 | 0 | 14 | 4,981 |
| 6 | 5,297 | 0 | 19 | 5,049 |
| 8 | 5,547 | 3 | 27 | 5,125 |
| 10 | 6,267 | 25 | 52 | 5,290 |
| 12 | 9,637 | 373 | 441 | 5,489 |

At radius 12 the hash-only graph had 258 mixed-label components and 388 non-clique components. The standard spatial graph had zero mixed-label components and one non-clique component. It contained 28,150 singletons, 4,764 pairs, 240 triplets, and one four-image group. Of its 5,489 edges, 4,830 were within Cracked and 659 within Non-cracked. A total of 474 edges used a nonzero alignment shift.

| Spatial policy at radius 12 | Minimum grayscale / detail / tile correlation | Tiles / quadrants required | Edges | Cross-label edges | Largest component |
|---|---|---|---:|---:|---:|
| Loose | 0.90 / 0.80 / 0.75 | 6 / 3 | 5,622 | 0 | 4 |
| Standard | 0.96 / 0.92 / 0.85 | 8 / 3 | 5,489 | 0 | 4 |
| Strict | 0.98 / 0.96 / 0.92 | 8 / 4 | 4,947 | 0 | 4 |

For the pixel comparator, 48 seeded in-memory controls per transformation exercised a 2-by-3 pixel shift, a brightness increase, and JPEG quality-70 re-encoding on 96-pixel images. Each policy matched 48/48 shifts and 48/48 brightness changes. JPEG matches were 41/48 loose, 21/48 standard, and 0/48 strict. All policies rejected 48/48 random cross-class controls. The recompression misses and accepted pairs at the radius-12 boundary show why this screen cannot guarantee coverage of all visual repeats. Different crops from the same photo can share no pixels. Images from the same surface can share texture or lighting even when the comparator sees no overlap.

## Frozen provisional manifests

The final version stratifies by publisher label and overlap component size. Group ranks use a SHA-256 ordering pinned to seed `metu-v2-fieldfix-10e-v2`, and each stratum is apportioned toward 80/10/10. This retains whole groups while keeping multi-image components represented in all three folds. The earlier v1 draft is preserved for traceability. It met the class targets but concentrated every multi-image component in training, so use v2.

| Partition | Cracked | Non-cracked | Total | Overlap groups | Multi-image groups |
|---|---:|---:|---:|---:|---:|
| Train | 15,727 | 14,784 | 30,511 | 26,313 | 4,004 |
| Validation | 1,965 | 1,848 | 3,813 | 3,289 | 500 |
| Provisional test | 1,966 | 1,848 | 3,814 | 3,289 | 501 |
| Quarantine | 26 | 238 | 264 | 264 | 0 |
| **Total** | **19,684** | **18,718** | **38,402** | **33,155** | **5,005** |

Of the 133 loose-only edges, 132 bridged different standard components and sent 264 patches to quarantine. All 5,489 standard edges and the one loose-only edge with both endpoints assigned remain inside one partition. The independent manifest check accounted for all 38,402 retained records and left the 1,598 original exact aliases excluded. It found zero duplicate byte or pixel hashes across output records, zero mixed-label groups, and zero accepted or borderline edges crossing folds. There are 1,472 rejected hash-only candidates crossing folds. Those pairs failed spatial confirmation, and their presence leaves the documented residual leakage risk.

The output root is `%LOCALAPPDATA%\FieldFixAI\ml\processed\metu-concrete-cracks\v2`.

- `leakage-audit-10e-v1\audit_report.json` and `pair_evidence.jsonl` hold threshold policies, distributions, and scores.
- `provisional-10e-v2\train.jsonl`, `validation.jsonl`, `test.jsonl`, and `quarantine.jsonl` hold memberships, labels, hashes, and overlap groups. Parent group fields are null. `split_report.json` pins every manifest hash.
- The original archive, inventory, exclusions, and grouping audit remain the source of record.

Reproduce from the project root using the existing Python, NumPy 2.4.4, Pillow 12.2.0, and Windows tar. Recomputing from the local archive produced byte-identical pair evidence and audit report. A second split run also produced byte-identical copies of all four manifests and the split report. The original inventory, exclusions, grouping audit, quality report, and processing log matched their pre-audit SHA-256 values.

```powershell
python -B -m ml.cv.audit_metu audit --reproduce
python -B -m ml.cv.audit_metu split
```

## Milestone 12 recommendation

1. When training is authorized, start a seeded ResNet18 transfer-learning baseline from `provisional-10e-v2\train.jsonl`. Fit the classifier using the training manifest and apply augmentation only during training. Choose the checkpoint and decision threshold on the v2 validation manifest. Keep the v2 test manifest sealed until those choices are frozen. Exclude the quarantine from development and evaluation.
2. Label the METU test result **provisional, leakage-limited patch evaluation**. Report the confusion matrix, crack precision, crack recall and precision–recall AUC. Use the evaluation only as a baseline for the implementation and relative model comparisons. A parent-independent headline score remains pending.
3. For the final project, preferably collect fresh originals from multiple independent buildings or assets. Record site, structure or asset, parent-photo ID, and crop coordinates before extraction. Have reviewers label the patches, adjudicate disagreements, and freeze the external set by site or asset before tuning. Keep every crop from one parent and asset together, run exact-repeat and spatial-overlap checks against METU, and evaluate once at the frozen threshold. Report uncertainty with source-group resampling when the number of independent groups supports it. An authorized cross-dataset alternative is SDNET2018 Walls, provided parent grouping is verified and the transfer scope is described as wall imagery. [Utah State SDNET2018 record](https://digitalcommons.usu.edu/all_datasets/48/)

This milestone produced evaluation artifacts only. It involved no dataset downloads, new ML dependencies, or model training.
