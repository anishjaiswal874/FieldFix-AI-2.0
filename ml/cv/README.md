# Milestone 10D | METU v2 acquisition and preparation

The publisher's release is **Concrete Crack Images for Classification v2**, DOI `10.17632/5y9wdsg2zt.2`, by Çağlar Fırat Özgenel. The official `Concrete Crack Images for Classification.rar` is **241,363,336 bytes (230.2 MiB)**, licensed **CC BY 4.0**. Its published SHA-256 is `08d7dc505a4f5a0330cee2fa2a1ae4b5b4f98bcffd0549b774caf7959bb1f02f`. The release describes 20,000 cracked and 20,000 uncracked 227 × 227 RGB patches derived from 458 parent photographs. [Mendeley release](https://data.mendeley.com/datasets/5y9wdsg2zt/2) · [File metadata](https://data.mendeley.com/public-api/datasets/5y9wdsg2zt/files?folder_id=root&version=2) · [Official RAR](https://data.mendeley.com/public-files/datasets/5y9wdsg2zt/files/8a70d8a5-bce9-4291-bab9-b48cfb3e87c3/file_downloaded)

## Verified preflight

- The publisher returned `206 Partial Content` and a strong ETag for capped range checks. The RAR4 header indicates a non-solid, single-volume archive with unencrypted headers.
- Windows `tar.exe` uses libarchive **3.8.9**. A truncated prefix could not be listed as a complete archive. A complete one-member RAR framed from the first official entry passed listing and conversion, and the installed **Pillow 12.2.0** decoded its original `Positive/00001.jpg` bytes as a 227 × 227 RGB JPEG. Full-archive compatibility still needs the authorized run. [Libarchive format support](https://www.libarchive.org/)
- The project disk had **322,321,453,056 bytes free (about 300 GiB)** on the scripted preflight. The planning budget is **4,536,330,632 bytes**, covering the archive and a 4 GiB working allowance. Exact unpacked image bytes will come from the verified archive inventory. Preparation checks that total again with per-file allocation and report headroom before writing images.
- Preflight transferred **72,674 METU archive bytes** across bounded probes, plus small metadata and an upstream test fixture. It kept no METU archive or extracted dataset. Temporary fixtures exercised interrupted download recovery, HTTP response rejection, traversal and link rejection, image and duplicate checks, and reproducible grouped splitting. Actual dataset counts and exclusions are pending.

## Run from the project root

The preflight command reads the publisher metadata and discards a capped 1 KiB header probe. It requires the existing Windows curl 8.4 or later, tar, Python and Pillow.

```powershell
python -B -m ml.cv.acquire_metu preflight
```

Run these two commands only after explicit approval for the complete METU v2 download.

```powershell
python -B -m ml.cv.acquire_metu download --approved-download
python -B -m ml.cv.prepare_metu prepare
```

The downloader keeps the archive and its journal in `%LOCALAPPDATA%\FieldFixAI\ml\raw\metu-concrete-cracks\v2`. It commits bounded 16 MiB ranges only when the response offset, size, and ETag match, records a SHA-256 per committed range, and verifies the committed bytes before resuming. A transfer error stops the run. Rerun the approved command to continue from the verified offset. If the source validator changes or a saved chunk fails its hash, the command preserves the partial file and reports the problem. The complete archive is promoted only after the published SHA-256 matches. A valid completed archive is reused on later runs.

Preparation verifies the complete archive again and keeps it intact. It inventories member types, paths, class counts and sizes before extraction, then decodes and hashes the images in staging. The pipeline rejects unsafe paths, links and case collisions. It excludes unreadable or off-spec JPEGs, collapses exact-byte or exact-pixel duplicates within a class, and excludes exact-pixel groups with conflicting labels. Equal and one-bit-different grayscale difference hashes flag plausible relationships for review. The published directory is `%LOCALAPPDATA%\FieldFixAI\ml\processed\metu-concrete-cracks\v2`.

| Output | Contents |
|---|---|
| `images\crack` and `images\no_crack` | One validated original JPEG per retained exact-pixel group |
| `inventory.jsonl` | Archive paths, source and decoded-pixel hashes, labels, exclusions, possible relationships, and unresolved group and split fields |
| `exclusions.jsonl` | Excluded members and reasons |
| `quality.json` and `processing.log` | Published archive identity, observed class counts, unpacked bytes, duplicates, conflicts, filename and EXIF evidence, and auxiliary archive entries |

## Parent photos and split

The public description supplies the parent count. The checked filename is sequential, and the information inspected so far provides no verified patch-to-parent map. After preparation, inspect the filename patterns, metadata counts, and any archive sidecars. The author's separate segmentation release documents the 458 source photographs and may help establish provenance if more evidence is needed. No companion dataset was downloaded. [Author's segmentation release](https://data.mendeley.com/datasets/jwsn7tfbrp/1) · [Authors' crack-detection paper](https://www.iaarc.org/publications/2018_proceedings_of_the_35th_isarc/performance_comparison_of_pretrained_convolutional_neural_networks_on_crack_detection_in_buildings.html)

Supply a reviewed `parent-groups.json` under `%LOCALAPPDATA%\FieldFixAI\ml\metadata\metu-concrete-cracks\v2` if reliable parent IDs are recovered. Its top-level fields are `archive_sha256` with the published hash, `provenance` describing the reviewed evidence, and `members` mapping each decoded member's inventory path to its parent ID. Include same-class duplicates and conflicting aliases so their parent groups can be linked. Source image numbers and difference-hash clusters alone are insufficient evidence.

```powershell
python -B -m ml.cv.prepare_metu split
```

The split command links reviewed parent IDs through exact-pixel matches and the conservative visual screen, then assigns entire groups toward a seeded 80/10/10 class balance. It requires both classes in each partition and publishes `parent-grouped-v1\split_manifest.jsonl` and `split_report.json` with the realized counts and source hashes. Group-level assignment may change the realized proportions. A reviewed parent-disjoint split supports parent-photo separation. A claim of separation by building or site needs grouping evidence at that level. Until a reviewed mapping is available, the usable result is the validated inventory.

The [Milestone 10E readiness report](milestone-10e-readiness.md) documents the separate overlap audit and the provisional, leakage-limited manifests for an exploratory baseline.
