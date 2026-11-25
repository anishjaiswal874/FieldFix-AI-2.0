# Milestone 10C Part 1

Use **Plumbing, HVAC, and Electrical** as the provisional NLP taxonomy for unplanned work orders. Include ordinary lighting in Electrical. Route Structural, standalone Lighting, General Maintenance, and other out-of-scope requests to manual review. Acquire **METU v2 first** for a concrete-crack patch-classification baseline, subject to separate download approval and verification of the parent-photo grouping.

## FMUCD input and the 504 exclusions

The audit matched the Milestone 10B JSONL manifest against the 13 cached CSV slices by window, physical starting line, record hash, and normalized-description hash. Each cached range passed its length and SHA-256 check against its source metadata. The 38-column header contains the publisher code fields `SystemCode`, `SubsystemCode`, and `DescriptiveCode`. The last is the component code field. [FMUCD v1 release](https://data.mendeley.com/datasets/cb8d2nsjss/1) · [Authors' data dictionary](https://pmc.ncbi.nlm.nih.gov/articles/PMC11465139/#tbl1)

| Milestone 10B decision in processing order | Rows |
|---|---:|
| Extra copies of an identical 38-field row | 464 |
| Missing work-order description after deduplication | 1 |
| Nonempty description with no alphabetic characters after deduplication | 2 |
| Missing work-order ID after deduplication | 0 |
| Missing system code after deduplication | 37 |
| Normalized descriptions removed for crossing splits | 0 |
| **Total excluded** | **504** |
| **Retained on the original split** | **3,889** |

The reasons are mutually exclusive. Of the 58 rows with a blank system code in the raw audit, 21 were already counted as extra exact copies. Two CSV records had a quoted multiline field and were parsed as complete rows. The 4,393-row denominator starts after incomplete byte-range edges have been discarded. The original validation university is U1 and the original test university is U4.

The [publisher-code inventory](fmucd-code-distribution.md) lists all observed codes and descriptions across U1, U4, U5, U6, U10, U11, and U12. It covers 18 nonblank systems, 53 nonblank subsystems, 181 nonblank components, and a blank-code row at each level. Its totals include planned and duplicate rows. These byte windows give a pilot view of seven universities.

## Genuine descriptions reviewed

Examples below paraphrase genuine **training-partition** descriptions and omit personal and location identifiers. The window and 1-based physical line locate each original in the existing Local AppData cache. U4 contributed aggregate counts only.

| Proposed FieldFix area | Examples and publisher evidence | Label decision |
|---|---|---|
| Plumbing | Clogged sink (`D2010SNK`, window-06 line 224) and leaking toilet (`D2010TWB`, window-09 line 396). A roof leak reportedly linked to a drain uses `D2040ROD` (window-05 line 164), which illustrates the need to review drainage versus enclosure. | Use `D20` with approved fixture, water-distribution, sanitary-waste and rainwater-drainage subsystems. |
| Electrical | A tripped circuit that needs resetting (`D5010CBE`, window-06 line 144). Another order under the same breaker code reports multiple lights out and suggests the breaker (window-12 line 182). | Use `D50` with `D5010` Electrical Service & Distribution or `D5020` Lighting and Branch Wiring. Communications/security and general/other electrical codes require review. |
| HVAC | A heater making a whistling sound (`D3050UNH`, window-05 line 211) and a no-heat report filed under a thermostat (`D3060THE`, window-05 line 224). | Use `D30` with approved heating, cooling, distribution, unit, controls and balancing subsystems. |
| Structural | Patching and painting a hole in a wall (`C3010CIW`, window-06 line 82) and cracked windows (`B2020WOW`, window-06 line 247) are finishes and enclosure examples. `B10` Superstructure had zero sampled rows. | Manual review. The observed wall and window descriptions provide no verified load-bearing defect labels. |
| Lighting | A failed light after replacing its lamp (`D5020CFL`, window-12 line 180) and an intermittent light switch (`D5020WDS`, window-06 line 20). | Include ordinary lighting in Electrical. The source groups fixtures and branch wiring together, and symptoms can overlap the breaker class. |
| General Maintenance | Support for a city inspection (`D9090PPM`, window-11 line 140) and a twice-yearly classroom procedure under the same component (window-07 line 174). The ten retained training rows marked UPM under `D90` all share one normalized description. U1 and U4 have zero retained `D90` UPM rows. | Manual review. `D9090PPM` identifies preventive maintenance, and these rows offer very limited corrective-maintenance coverage. |

## Proposed mapping on the frozen partitions

Count only unplanned (`UPM`) rows that match a system and subsystem in the [draft mapping](fieldfix-nlp-label-map.proposal.json). Generic or special-purpose plumbing and HVAC subsystems are outside the draft allowlist. Work orders or normalized descriptions associated with multiple publisher system codes go to review. The explicit allowlist produced 918 candidates, and the conflict rule sent one validation HVAC row to review. The counts below are the resulting 917 provisional source-label candidates. They remain subject to human label review.

| Draft label or disposition | Train | Validation | Test | Total |
|---|---:|---:|---:|---:|
| Plumbing | 303 | 25 | 30 | 358 |
| HVAC | 320 | 24 | 15 | 359 |
| Electrical, including ordinary lighting | 141 | 37 | 22 | 200 |
| **Three-class candidates** | **764** | **86** | **67** | **917** |
| Other or conflicted unplanned work for manual review | 535 | 108 | 72 | 715 |
| Planned-maintenance rows outside the request baseline | 1,769 | 386 | 102 | 2,257 |
| **Original Milestone 10B partition** | **3,068** | **580** | **241** | **3,889** |

Structural, standalone Lighting, and General Maintenance have zero assigned rows in every partition under this policy. Electrical includes 184 `D5020` rows and 16 `D5010` rows. The test partition has one `D5010` work order. Broad electrical performance would need stronger coverage.

### Evaluation implications

- **University confounding.** All three proposed labels appear at all seven sampled universities. U5 alone supplies 159 of 320 training HVAC rows. The proposed evaluation uses one validation university and one test university, with 67 projected test rows and just 15 HVAC rows. That supports a pilot comparison. A university-independent performance claim needs broader held-out coverage.
- **Duplicate leakage.** Across the frozen partitions, I found zero overlapping full-row hashes, normalized-description hashes, or work-order ID strings. A simple signature that normalizes digit runs and punctuation for descriptions of four or more words also found zero cross-fold matches. Paraphrases and loosely related templates remain possible. The 764 proposed training rows contain 627 distinct normalized descriptions and 627 distinct university-and-order keys. For future development, keep repeated orders and description groups together and fit preprocessing on development data only.
- **Planned-maintenance shortcuts.** The training split contains 239 repetitions of an annual HVAC terminal/heating-coil template, 179 of a monthly emergency-light inspection, and 118 of one scheduled pump check. These resemble asset task schedules more than reactive complaints. Keep `PPM` outside the corrective baseline. Use the publisher codes, university, work-order ID, and maintenance-mode flag for auditing or sampling only. Train on the description after reviewing templates and location cues.
- **Holdout.** The recorded SHA-256 in the draft pins the original split. Preserve U4 and its original membership. Its counts here describe a read-only projection. Keep test text out of label development, preprocessing design, threshold selection, and model selection. Evaluate the agreed test subset only after those choices are frozen.

### Human decisions before release

1. Confirm FieldFix's Plumbing scope for rainwater drains and Electrical scope for ordinary lights and switches. Review development examples for text–code mismatch, cross-trade orders, and institutional shorthand. Leave unresolved cases with manual reviewers.
2. Approve the three stable model labels and later bind them to active `categories.id` values. The read-only application category query returned no rows. The draft leaves every ID and the model version empty. An approved release should record both model and mapping versions. [Existing FieldFix mapping design](../../backend/README.md#future-nlp-category-mapping)
3. Define when automation should abstain, using reviewed development examples outside the taxonomy as well as in-scope examples. Out-of-scope requests, unresolved label mappings, and inactive categories should produce no category suggestion and remain in the existing manual workflow.

## CV acquisition decision

**METU v2 is the practical first acquisition.** The previously verified Mendeley endpoint returned `206 Partial Content` for a one-byte request. It provides one **241,363,336-byte (230.2 MiB) RAR**, with published SHA-256 `08d7dc505a4f5a0330cee2fa2a1ae4b5b4f98bcffd0549b774caf7959bb1f02f`. The release is CC BY 4.0 and describes 40,000 RGB 227 × 227 images, split evenly between cracked and uncracked, from 458 parent photos of campus-building concrete. It is suitable for a patch-classification baseline. [METU v2 release](https://data.mendeley.com/datasets/5y9wdsg2zt/2) · [Publisher file metadata](https://data.mendeley.com/public-api/datasets/5y9wdsg2zt/files?folder_id=root&version=2)

After approval, save `Concrete Crack Images for Classification.rar` under `%LOCALAPPDATA%\FieldFixAI\ml\raw\metu-concrete-cracks\v2`. Resume a partial file only with a matching validator and exact `206` range. Then list and test the archive with installed Windows `tar.exe` (libarchive 3.8.9), whose compatibility with this RAR is still unverified. Validate member types and paths before streaming the two image classes to staging. Reject links, traversal, and case-insensitive path collisions. Check the expected decoded dimensions, counts, and per-file hashes before promoting to `processed\metu-concrete-cracks\v2\images`, with `crack` and `no_crack` class folders. Exact unpacked bytes are unknown until the archive can be listed. This volume had about **300.4 GiB** free during the audit. Recheck free space before transfer and again before extraction against remaining download bytes, unpacked image bytes and staging/manifest overhead. Verify a parent-photo or independently documented group identifier before splitting. If that provenance cannot be recovered, limit conclusions to acquisition and patch inventory until a leakage-safe evaluation set is available.

**SDNET2018 Walls has an operational blocker.** The archive is absent locally. Utah State offers one ZIP listed as **515,903 kB** (roughly 504 MiB assuming binary-style units) and its official archive endpoint returned Cloudflare `403 Forbidden` to the previous header request. Exact bytes and byte-range support remain unverified. Its official workflow requires the full ZIP and extraction of only `W/CW` and `W/UW`, expected to contain 3,851 cracked and 14,287 uncracked 256 × 256 patches from 72 parent wall photographs. The Walls subset becomes a better first choice if wall-specific evaluation is essential and a verified copy is supplied. [Utah State release](https://digitalcommons.usu.edu/all_datasets/48/) · [Authors' dataset paper](https://pmc.ncbi.nlm.nih.gov/articles/PMC6247444/)

This milestone ended at the audit and proposal. All data access used the existing FMUCD cache and prior CV access results.
