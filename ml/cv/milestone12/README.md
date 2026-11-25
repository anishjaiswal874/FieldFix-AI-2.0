# Milestone 12 · METU ResNet18

This isolated experiment reads the existing METU `provisional-10e-v2` split and the cached ImageNet ResNet18 checkpoint. It uses CUDA and writes each experiment to a fresh `metu-resnet18-m12-vN` directory under `%LOCALAPPDATA%\FieldFixAI\ml\runs\cv`. The application and frozen source data are read-only inputs to this milestone.

## Windows run order

Use a Windows PowerShell session from the project root with the isolated CV environment and the verified pre-Milestone 12 backup. The package versions are pinned in `requirements.txt`. The dedicated wheel fetcher can continue the earlier byte-range transfer in `wheelhouse-v3` after any other transfer in that directory has stopped. It checks the assembled file against PyTorch's published SHA-256 before making it available for installation. Its byte counts come from `ranges.jsonl` because the partial wheel is preallocated.

```powershell
$ml = Join-Path $env:LOCALAPPDATA 'FieldFixAI\ml'
$py = Join-Path $ml 'envs\cv-m12-v1\Scripts\python.exe'
$receipt = Join-Path $env:LOCALAPPDATA 'FieldFixAI\backups\milestone12-before-20261119T043942275674Z\verification.json'

& $py -B -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Check the isolated environment before continuing.' }
& $py -B -u -m ml.cv.milestone12.run smoke-cuda
if ($LASTEXITCODE -ne 0) { throw 'Stop after a failed CUDA smoke test.' }
& $py -B -u -m ml.cv.milestone12.run train --backup $receipt
if ($LASTEXITCODE -ne 0) { throw 'Inspect the training failure before evaluation.' }
```

Use the `RUN_DIR` printed by `train` for the next commands. Confirm that `freeze.json` exists in that directory and records the intended checkpoint, preprocessing and threshold before claiming the one authorized test evaluation.

```powershell
& $py -B -u -m ml.cv.milestone12.run evaluate --run $run
if ($LASTEXITCODE -ne 0) { throw 'Preserve any test claim and inspect the failure.' }
& $py -B -u -m ml.cv.milestone12.run smoke-inference --run $run
& $py -B -u -m ml.cv.milestone12.run inventory --run $run
```

## Experimental controls

- Frozen partition sizes are 30,511 train, 3,813 validation and 3,814 provisional test. The 264 quarantined patches are excluded.
- Training hashes source JPEG bytes as it reads them. It uses a reproducible epoch shuffle and horizontal flips with probability 0.5. All folds use RGB bilinear resizing to 224 × 224 and ImageNet normalization. Validation and test use deterministic preprocessing.
- A discarded scratch model probes the full fine-tuning step at batch size 32. Only a CUDA out-of-memory error permits the batch-16 fallback. Head training uses two epochs. Fine-tuning updates the last residual block and head for up to six more epochs, with patience two. BatchNorm running statistics stay frozen and are checked after every epoch.
- Validation macro-F1 at threshold 0.50 selects the checkpoint, with lower validation loss breaking ties. A 0.05–0.95 grid in steps of 0.01 then selects the crack threshold on that checkpoint's validation scores. Threshold ties favor the nearest value to 0.50, then the lower threshold.
- `freeze.json` binds the model, checkpoint, preprocessing, implementation and decision threshold before test scoring. `test-claim.json` is created before scoring. `provisional-test.json` records the saved result for reuse. If the claim exists without a completed result, preserve it and stop.
- The existing split keeps known groups together. Without verified parent-photo identities, the test remains a provisional, leakage-limited patch-level evaluation. Independent source photos are needed to measure generalization.
