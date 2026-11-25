# Milestone 11 | FMUCD pilot NLP baseline

The selected model is **TF-IDF + LinearSVC**. Its forced-choice held-out score was **60/67 correct, 89.55% accuracy and 0.8843 macro-F1**. This is a three-class pilot measured on one withheld university. The margin rule requires further validation before integration. It gave a suggestion for every genuine out-of-scope example in the small qualitative smoke test.

## Inputs and split integrity

The input was the 13 saved HTTP ranges of **Facility Management Unified Classification Database v1**, DOI `10.17632/cb8d2nsjss.1`. They contain 4,393 complete sampled records. The 10B split retained 3,889. The reviewed 10C policy retained 917 after excluding 2,257 non-UPM rows, 714 out-of-scope systems or subsystems, and one conflicting system record. All supervised inputs were genuine `WODescription` text. Publisher codes, university, work-order identity and eligibility fields were used only for provenance or labeling. Lighting remains inside Electrical through `D5020`. [FMUCD v1 release](https://data.mendeley.com/datasets/cb8d2nsjss/1) · [Label audit](../pilot/milestone-10c-label-audit.md)

| Partition | Universities | Plumbing | HVAC | Electrical | Total | Distinct normalized descriptions |
|---|---|---:|---:|---:|---:|---:|
| Train | 5, 6, 10, 11, 12 | 303 | 320 | 141 | 764 | 627 |
| Validation | 1 | 25 | 24 | 37 | 86 | 77 |
| Test | 4 | 30 | 15 | 22 | 67 | 67 |

The frozen split SHA-256 is `0d8c751f618a7eaa8b30b74ef349db8f68fb1de716a40704cd03d8495c3474d7`. The canonical manifest of the cached range metadata hashes to `6da0b4005d5d2e272d16a81f281ff13d9f4a25f3eff4983cecfe66cb350bbad2`. The run's `source.json` records the individual range hashes, original audit hash, and reviewed label proposal hash. Record hashes, normalized-description hashes, university IDs and university/work-order keys were checked across partitions. The test-window text was opened only after freeze. The held-out script wrote a one-time scoring receipt before scoring. A rerun reused the saved result.

## Training and validation

Python 3.12.10 ran in an isolated Local AppData venv using NumPy 2.4.4, SciPy 1.17.1, scikit-learn 1.8.0, joblib 1.5.3, and threadpoolctl 3.6.0. Both pipelines used seed **11021**, single-threaded fitting, class-balanced losses, and `C=1.0`. TF-IDF used 1,516 learned word unigrams and bigrams, `min_df=2`, sublinear term frequency, L2 document normalization, and no stop-word filtering. The preprocessing applies Unicode NFC, case folding and whitespace collapse. Word tokenization retains negation and common domain abbreviations. Rare terms and numeric-leading tokens, including some equipment or voltage notation, may be absent from the features.

| Model | Validation correct | Accuracy | Macro-F1 | Weighted-F1 | Fit time | Compressed pipeline |
|---|---:|---:|---:|---:|---:|---:|
| Logistic Regression, LBFGS | 68/86 | 79.07% | 0.7842 | 0.7961 | 0.0435 s | 40,830 bytes |
| LinearSVC | 70/86 | 81.40% | 0.8083 | 0.8174 | 0.0180 s | 38,506 bytes |

LinearSVC won by the predeclared validation macro-F1 criterion. Accuracy and then Logistic Regression were the planned tie-breaks. Each pipeline was refit from scratch on the same training rows. Its vocabulary, IDF, validation decisions and predicted labels matched. Reloading each saved joblib pipeline gave identical validation scores and labels. `development_evaluation.json` contains both models' per-class precision, recall, F1, support, and confusion matrices. Only the selected model was scored on the test.

## Once-scored test

Forced choice yielded 89.55% accuracy, **0.8843 macro-F1** and **0.8946 weighted-F1**. Rows of the confusion matrix are actual labels, and columns are predicted labels. The order is Electrical, HVAC, Plumbing.

| Class | Precision | Recall | F1 | Support |
|---|---:|---:|---:|---:|
| Electrical | 0.9500 | 0.8636 | 0.9048 | 22 |
| HVAC | 0.8571 | 0.8000 | 0.8276 | 15 |
| Plumbing | 0.8788 | 0.9667 | 0.9206 | 30 |

```text
[[19,  1,  2],
 [ 1, 12,  2],
 [ 0,  1, 29]]
```

The pilot selected byte ranges, and the two evaluation folds each represent a single university. Only 15 test examples were HVAC. The training partition is imbalanced, and its 627 distinct normalized descriptions indicate repeated wording. Shorthand, site or building references, and campus work-order conventions can change across institutions. FieldFix reports may differ in wording, label boundaries, and the prevalence of out-of-scope requests. The test estimates classification on the selected three trades at University 4.

## Manual-review rule and smoke test

The rule uses the gap between the top and runner-up **native decision scores**. The gap is uncalibrated. Candidates were zero and fixed quantiles of training margins. Validation targeted at least 90% accuracy among accepted rows, at least 50% coverage, and at least five accepted records per true class. Neither model met all three targets. The selected model uses the best accuracy among validation candidates satisfying the coverage constraints. It uses a margin threshold of **0.02764207595632942**, with review also triggered by zero recognized vocabulary features.

- Validation accepted 85/86 and got 70/85 accepted rows correct (**82.35%**).
- The frozen rule accepted 66/67 test rows and got 60/66 accepted rows correct (**90.91%**). One HVAC row went to review. The full 67-row result remains the primary measured score.
- The four genuine in-scope development examples produced their expected suggestions, including ordinary lighting as Electrical. These were known examples. The four genuine excluded examples covering wall patching, cracked windows, a fallen ceiling tile and inspection support all received suggestions. The separate empty-input check correctly routed to review. `smoke_results.json` records the genuine source pointers and actions.

The model has three outputs and the margin gate leaves out-of-scope detection unresolved. Before FastAPI integration, validate on representative FieldFix descriptions with labeled in-scope and out-of-scope cases, establish an adequate manual-review policy, and verify the deployment's active category IDs via a separate integration decision. `label_map.json` version `1.0.0-m11-labels.1` leaves database IDs null and keeps the model labels stable.

## Run and artifacts

Run from the project root. The published run is `%LOCALAPPDATA%\FieldFixAI\ml\runs\nlp\milestone-11-v1`.

```powershell
$py = Join-Path $env:LOCALAPPDATA 'FieldFixAI\ml\envs\nlp-m11\Scripts\python.exe'
& $py -B -m ml.nlp.train_m11 train
& $py -B -m ml.nlp.train_m11 evaluate
& $py -B -m ml.nlp.smoke_m11
```

The run contains the two joblib pipelines, label order and mapping, per-record provenance without raw descriptions, development metrics, freeze and test receipts, test metrics, smoke results, and an artifact manifest with sizes and SHA-256 hashes. The inference module accepts `--text` for one local description and returns a stable label or a review action. Only deserialize trusted local joblib artifacts with the pinned environment. Loading an untrusted joblib can execute code. [Scikit-learn persistence guidance](https://scikit-learn.org/stable/model_persistence.html)

This milestone ended at model training, evaluation and artifact generation. The frozen METU v2 split report retained its original SHA-256.
