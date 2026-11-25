# FieldFix AI 2.0 demonstration and viva

## Demonstrate the full workflow

Use a versioned directory under `%LOCALAPPDATA%\FieldFixAI\qa` with a disposable SQLite database, private upload directory, Chrome profile, JWT key and frontend build. Verify the resolved database and uploads before creating the schema or any users. Bind the API and preview to separate loopback ports and route `/api` only to the verified disposable listener. The Phase B evidence directory contains the browser runner, backend launcher and final report. Run its `prepare_replay.ps1` from PowerShell 7 with the project path, evidence directory and browser-runner filename recorded in the report. The launchers use a hash-verified source copy and the installed dependencies. The normal project startup targets the application database.

1. Start with an empty disposable schema. Bootstrap one Administrator using the existing bootstrap service. Register a staff account, verify its identity, and assign Reviewer in **Users & Roles**. Create an active team and an active category and associate the team as its default. Approval still requires an explicit team selection.
2. Register a distinct Requester. Submit a complaint and a demo JPEG. Open the new request and check Submitted status, Dashboard and My Requests. Verify the photo loads through the private attachment route. The automated run uses a generated JPEG to exercise upload and retrieval. It is an integration fixture.
3. Sign in as Reviewer and find the request in **Triage Queue**. Check description, location, requester and photo. Select category, priority and team. Open the confirmation and cancel. Verify the request stays Submitted and no decision or order exists. Open confirmation again and confirm. Verify exactly one Open order in **Work Orders**, the decision in **History**, the team workload in the Administrator portal, and Approved status in the Requester portal.
4. Use separate requests for rejection and an information request. Check that both write a review decision and create zero orders. Confirm the owning Requester can read the information note.
5. For AI, restart only the disposable listener with one temporary feature enabled at a time. Open **Request Detail** to see the active pilot controls. The Reviewer dashboard shows fixed “AI unavailable” text. Keep the NLP binding variable unset. Use an authored maintenance description for the NLP smoke check and a verified METU training JPEG for the CV smoke check. Restore the default by stopping the disposable processes.
6. Preserve redacted test evidence. Stop owned processes and clear disposable profiles, databases, uploads and builds. Verify the protected database, models and flags against their pre-demo state.

## How decisions are made

The Requester supplies the report. The system enforces access and validates attachments. A Reviewer may ask separately for a text suggestion, concrete screening, or a priority suggestion. Each uses its own inputs and returns a transient result. The Reviewer inspects all evidence, chooses the fields, and confirms. Only that transaction creates the work order. SQLite enforces at most one work order per request.

## NLP experiment

The FMUCD v1 pilot sampled 4,393 work orders. After cleaning, scope review and label mapping, 917 genuine descriptions were eligible for supervised learning, with 764 train, 86 validation and 67 held-out test records. The supported labels are Plumbing, HVAC and Electrical. Lighting remains within Electrical under the reviewed mapping. The partitions use separate universities. The test fold represents one university and includes 15 HVAC examples.

TF-IDF represents each description with weighted word unigrams and bigrams learned on training text. LinearSVC learns a linear decision boundary with a margin objective. Class-balanced Logistic Regression was the comparison model. Validation macro-F1 was 0.7842 for Logistic Regression and 0.8083 for LinearSVC. The frozen LinearSVC had **89.55% accuracy (60/67)** and **0.8843 macro-F1** on the held-out three-class test.

The margin is an uncalibrated decision-score gap. All four genuine out-of-scope examples in the original qualitative smoke check received a suggestion from the model. The API pilot adds a conservative scope and agreement gate and can abstain. Suggestions are research-only. Representative reviewer-labeled validation and separately approved live category bindings are pending. [NLP experiment](../ml/nlp/README.md)

## CV experiment

METU Concrete Crack Images for Classification v2 was acquired from Mendeley Data as a SHA-256-verified, CC BY 4.0 archive. The pipeline validated 40,000 JPEG patches and retained 38,402 after excluding 1,598 exact duplicates. The leakage audit grouped validated near-duplicate overlap and quarantined 264 patches. The frozen split used 30,511 train, 3,813 validation and 3,814 provisional test patches. Parent-photo identifiers were unavailable, and inferred groups provide only partial leakage control.

ResNet18 uses residual connections and started with pretrained ImageNet weights. Training fitted the new head and then fine-tuned the final residual block and head. Inference uses 224 × 224 RGB inputs with ImageNet normalization. Validation selected the checkpoint and the crack decision threshold of **0.86** before the one-time provisional test evaluation. Accuracy was **99.8689%**, with **five false negatives and zero false positives**. The test estimates performance on this provisional patch split. New-site performance requires a source-photo-independent evaluation. Screening is limited to Reviewer-confirmed concrete close-ups and qualified people assess structural safety. [Leakage audit](../ml/cv/milestone-10e-readiness.md) · [Training procedure](../ml/cv/milestone12/README.md)

## Priority and security

Versioned rules can suggest High for an explicit active hazard, Medium for loss of service, and Low for cosmetic damage with explicit lack of functional impact. Ambiguous or contradictory reports can yield manual review. The result shows the matched evidence and an explanation where applicable. Priority is rule-based and the Reviewer chooses the saved value.

The API combines database-backed role checks, owner-scoped request reads and parent-bound attachment access. Upload handling checks the byte and image limits, re-encodes supported still images and uses generated private storage filenames. UI logout clears the in-memory token. Server-side account deactivation denies access with a previously issued token, and a role change updates authorization immediately on guarded endpoints.

## Viva prompts

| Question | Concise answer |
| --- | --- |
| How did you choose the text model? | Compare validation macro-F1 on identical, university-separated partitions. Freeze the selected classifier before the held-out evaluation. |
| Why report macro-F1 as well as accuracy? | It weights classes equally, so a small class can influence the assessment. |
| What happens when the complaint is unclear? | The reviewer continues manually. The pilot gates unsupported or ambiguous input and can abstain. |
| Why is the CV result provisional? | Related patches may still share an original photo across partitions. The source does not provide reliable parent-photo identifiers. |
| What does the 0.86 threshold control? | It converts the crack softmax score into the frozen screening outcome. Validation selected it before the provisional test. |
| Can AI create a work order? | The authenticated Reviewer chooses an assignment and confirms. Only the human decision transaction writes the order. |
| What does a worker failure do? | The recommendation becomes unavailable. The Reviewer can continue with the manual decision form. |
| What would you validate before a wider release? | Measure NLP scope and abstention on representative reports, test CV on independent source photos, and verify host security, privacy and recovery controls. |

The project is scoped to a local academic demonstration. Future work includes requester replies, work-order progression, independent model evaluation, approved NLP bindings, and a deployment plan covering HTTPS, account recovery, abuse controls, backups and monitoring.

