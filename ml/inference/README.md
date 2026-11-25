# Milestone 13 reviewer pilot

The recommendation panel is optional. The approval, rejection, request-for-information, and work-order operations still use the original review endpoint. The pilot reads existing records and returns transient suggestions.

## Controls

Each flag enables only its own feature and accepts the exact value `1`. Unset flags and all other values leave the feature disabled. The pilot uses the existing backend and model environments.

| Flag | Service |
|---|---|
| `FIELDFIX_AI_NLP_ENABLED` | Research-only category suggestion |
| `FIELDFIX_AI_CV_ENABLED` | Concrete JPEG screening |
| `FIELDFIX_AI_PRIORITY_ENABLED` | Rule-based priority recommendation |

Use these controls only in an approved disposable pilot. Roll back by leaving all three unset. The existing APIs continue to serve submissions and manual reviews. There are no AI data or schema changes to reverse.

## Reviewer API

All endpoints require a current active Reviewer JWT. Request-specific endpoints require a reviewable request. Screening also requires a matching private attachment. JSON responses are private and non-cacheable.

| Method | Route |
|---|---|
| GET | `/api/reviewer/recommendations/features` |
| POST | `/api/reviewer/requests/{request_id}/recommendations/nlp` |
| POST | `/api/reviewer/requests/{request_id}/recommendations/priority` |
| POST | `/api/reviewer/requests/{request_id}/attachments/{attachment_id}/screening?concrete_closeup=true` |

The POST routes take their evidence from storage. The screening query is the reviewer's explicit confirmation for the selected photo. The API checks JPEG format, decodes the content, and enforces 5 MiB and 16 megapixel limits. The confirmation records the Reviewer's judgment within the request and does not independently verify the material. Wide scenes and uncertain materials are unsuitable. The existing patch-level evidence has unverified parent-photo independence, and a qualified person must assess structural safety.

NLP requires one supported system, a fault in the same clause, and agreement with the frozen classifier and its existing margin filter. Unclear, contradictory, historical, mixed-system, and clearly unrelated reports go to manual review. The rules are conservative checks with incomplete coverage. The margin is an internal gate and has no probability interpretation. Priority uses separate versioned rules over explicit reported hazards, loss of service, or cosmetic impact. Neither service changes the decision form automatically.

NLP remains non-actionable through a code-level rollout gate even if the flag is enabled and a binding matches. The optional FIELDFIX_AI_NLP_BINDINGS JSON expects a `version` of `m13-bindings-v1`, a `model` of `fmucd-pilot-m11-v1`, and a `categories` object keyed by the three machine labels. Each approved entry requires the stable positive integer database ID and exact approved name, Plumbing, HVAC, or Electrical. The API checks the name and active status on each action. No real bindings are configured. An independent, representative reviewer-labeled scope validation and approval are required before releasing the gate.

## Worker limits

FastAPI launches the compatible local Python executable without a shell. The workers verify the exact frozen artifacts before loading and receive only bounded text or JPEG bytes over stdin. The local process environment omits application secrets. Neither worker connects to SQLite. Outputs are bounded to 1 KiB and contain only the label or screening result. The Windows launcher tree is terminated on timeout, and each worker has its own watchdog. There is one subprocess slot per model per API process and one concurrent backend JPEG decode. NLP has a 16-second supervisor limit and CV has a 28-second limit. CV requires the existing CUDA runtime. Keep this pilot to one API process so the process-local limits also cap local GPU concurrency. The processes share the Windows account, so these are resource limits and protocol boundaries rather than OS privilege isolation.

## Verification commands

Run the backend suite from the backend directory using its existing venv. The test harness uses disposable SQLite and upload fixtures.

```powershell
& .\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

Run the isolated model check from the project root. Only the child smoke process enables the flags. The text is an authored technical fixture. The JPEGs are one known training patch per class. These checks provide no new evaluation metrics.

```powershell
& .\backend\.venv\Scripts\python.exe -B -m ml.inference.test_model_loading
```

On the Windows pilot machine, one successful cold-start check returned Plumbing in 1.56 seconds and the two CUDA screenings in 6.27 and 5.22 seconds. A separate single-image CUDA probe observed about 53 MiB allocated GPU memory, 70 MiB reserved GPU memory, and 1.13 GiB peak process working set. These observations are device-specific and do not impose an operating-system memory quota.
