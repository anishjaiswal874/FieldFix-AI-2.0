# FieldFix AI Milestone 10B

This pilot inspects published university work orders for a future maintenance classifier. It writes source-code distributions and a split manifest. The manifest carries source codes and fingerprints for auditing. FieldFix category mapping and model training require later milestones.

## Reproduce the FMUCD pilot

Run these commands from the project root in PowerShell with Python 3.12 and Windows curl 8.4 or later.

```powershell
python -B ml/pilot/acquire_fmucd.py
python -B ml/pilot/validate_fmucd.py
```

Acquisition takes the first 128 KiB for the header and one 128 KiB window centered in each of twelve equal byte strata. Each response is capped at 128 KiB and must match the expected HTTP 206 status, byte range, file size, and ETag. The ETag identifies the publisher's object version. Local SHA-256 manifests verify the bytes that were retained. On a rerun, verified windows are reused. The full CSV is 1,439,574,384 bytes, and these commands have a maximum payload of 1,703,936 bytes per complete uncached run. `--header-only` limits acquisition to the first window. [FMUCD v1 release and CC BY 4.0 license](https://data.mendeley.com/datasets/cb8d2nsjss/1)

The scripts write only under `%LOCALAPPDATA%\FieldFixAI\ml`. The cache is in `raw\fmucd\v1\pilot`. The validator reads the actual CSV header, discards incomplete range edges, and uses the publisher's university and country fields to synchronize each interior window. Python's CSV reader handles quoted and multiline records. The validator normalizes Unicode, case, and whitespace, then audits blank and placeholder values, exact row duplicates, and repeated order and description keys. Trace each split row back to its cached window and 1-based physical starting line. The processed files contain counts, hashes, and source codes. Free-text descriptions stay in the raw cache. [FMUCD data dictionary](https://pmc.ncbi.nlm.nih.gov/articles/PMC11465139/#tbl1)

## Findings from the pilot

- The thirteen windows contained **4,393 complete records** across **seven of twelve universities**. The publisher file is grouped by university in these slices, so contiguous ranges contain related orders. The results describe these slices and do not estimate corpus-wide prevalence.
- The CSV has **38 columns**. Its component identifier is named `DescriptiveCode`. It also has `WODescription`, `WOID`, `SystemCode`, `SubsystemCode`, `WOPriority`, and `PPM/UPM`. One description, 58 system codes, 1,139 priorities, and 41 maintenance-type values were missing. The median description was seven words. Thirty-nine descriptions were at most two words, and two nonempty descriptions contained no letters.
- Of **1,842 unplanned** work-order rows, 474 had source code **D20 Plumbing**, 413 had **D30 HVAC**, and 299 had **D50 Electrical**. Readable examples described leaking taps, ventilation faults, and lighting repairs. Other entries were shorthand, location-heavy, or planned-maintenance templates. The three trades are candidates for a reviewed mapping. Interior construction, exterior enclosure, general, and other codes need category-specific review. The app's local category table was empty when read in read-only mode.
- There were **464 extra exact duplicate rows**, **293 repeated university-and-order keys**, and **336 repeated normalized descriptions**. Seven order keys and four normalized descriptions spanned multiple system codes. The differences can reflect work involving several trades and should be reviewed before selecting training targets.
- The split manifest contains **3,889** rows after exact deduplication and removal of rows without usable text, an order ID, or a system code. It holds universities together and excludes normalized descriptions that span multiple partitions. The split has 3,068 train, 580 validation, and 241 test rows. It is a pipeline check. A later training split should review unplanned work orders and class coverage by university. Two validation runs produced byte-identical audit and split files.

## SDNET2018 Walls when an archive is available

No archive was found in the Downloads folder or the accessible FieldFix project locations. No CV bytes were downloaded or extracted in this pilot. If an existing ZIP is supplied, leave it in place and open it read-only. Compute its SHA-256, check the publisher checksum once its scope is established, validate ZIP CRCs, and compare image counts and readable 256 × 256 JPEGs with the authors' documentation. Inspect the member list before extraction. Allow only regular JPEG files in `W/CW` and `W/UW`, including any single verified wrapper folder. Reject absolute paths, traversal, links, Windows reserved names, and case-insensitive destination collisions. Check required space, stream files into staging under the Local AppData ML root, and promote the result after verifying counts and hashes. A manifest keyed by the archive hash and each member path and hash lets an interrupted run verify and reuse completed work. [Utah State dataset record](https://digitalcommons.usu.edu/all_datasets/48/) · [Authors' image counts and folder structure](https://pmc.ncbi.nlm.nih.gov/articles/PMC6247444/)

Validate the parent-photo identifier against the archive filenames or documented metadata before using it. Give cracked and uncracked patches from the same photo the same group ID. Compare the recovered grouping with the 72 parent wall photographs reported by Utah State and investigate discrepancies. Group exact duplicate content across paths using SHA-256. Review conflicting labels and possible near-duplicates before splitting. If duplicate content links different parent photos, keep the linked parent groups together. Freeze a seeded group-level split and verify that neither a parent group nor a duplicate hash appears in multiple folds. If parent IDs cannot be validated, stop before claiming an independent CV evaluation. [Utah State methodology](https://digitalcommons.usu.edu/all_datasets/48/)
