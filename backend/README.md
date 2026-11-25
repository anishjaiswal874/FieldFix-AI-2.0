# FieldFix AI 2.0 database

SQLite stores the local database at `backend/fieldfix.db`. The path is resolved from the backend source directory. Apply schema changes through Alembic using Python 3.12 from the backend virtual environment.

Run these commands from the project root in VS Code PowerShell.

```powershell
& .\backend\.venv\Scripts\python.exe -m alembic -c .\backend\alembic.ini upgrade head
& .\backend\.venv\Scripts\python.exe -m alembic -c .\backend\alembic.ini current
```

Inspect the tables through a read-only connection.

```powershell
@'
from pathlib import Path
from sqlite3 import connect

database = Path('backend/fieldfix.db').resolve().as_uri() + '?mode=ro'
with connect(database, uri=True) as connection:
    for (name,) in connection.execute("SELECT name FROM sqlite_schema WHERE type = 'table' ORDER BY name"):
        print(name)
'@ | & .\backend\.venv\Scripts\python.exe -B -
```

Run `PRAGMA table_info(maintenance_requests)` to inspect columns or `PRAGMA foreign_key_list(maintenance_requests)` to inspect references.

## Model conventions

- The SQLite engine enables foreign keys for every connection. Referenced rows are protected from accidental deletion.
- Timestamps represent UTC. SQLModel returns timezone-aware values. `updated_at` is refreshed by SQLAlchemy model updates. Code issuing raw SQL updates should set it explicitly.
- `users.password_hash` holds the encoded Argon2id hash. Email uniqueness uses SQLite's `NOCASE` collation, and the auth routes normalize addresses before lookup and insertion.
- Attachment storage uses a generated lowercase UUID basename under the Git-ignored `backend/uploads/` directory. The original filename is display metadata.

## Authorization, decisions, and optional AI

- The authentication layer reads roles from user records on each protected request. Requester routes scope requests and photos to the caller's account. Reviewer routes enforce the Reviewer role.
- The approval service persists an explicit human decision and creates a work order in one transaction. A unique request reference enforces at most one work order per request.
- The optional Reviewer-only AI pilot returns transient recommendations. Final categories, priorities, and reviewer outcomes belong in `review_decisions`. Work order creation follows the explicit human review workflow. See the [pilot guide](../ml/inference/README.md).
- Administrator-created categories are operational configuration. NLP label mapping and model retraining require separate decisions and an explicit model release process.

## Password authentication

Startup requires `FIELDFIX_JWT_SECRET`. Use at least 32 cryptographically random bytes encoded as unpadded base64url. Keep a separate secret for each environment and load production keys through the deployment secret manager. Replacing the key invalidates tokens signed with the previous key.

From the project root, start a local development instance in Windows PowerShell. The first run generates a key and uses Windows DPAPI to encrypt it in the Git-ignored `backend/.env.jwt.clixml`. Subsequent starts load that key into the process environment using the same Windows account and computer.

```powershell
Set-Location -LiteralPath .\backend
if (-not (Test-Path -LiteralPath .\.env.jwt.clixml)) {
    $localKey = & .\.venv\Scripts\python.exe -B -c "import secrets; print(secrets.token_urlsafe(32))"
    $protectedKey = ConvertTo-SecureString -String $localKey -AsPlainText -Force
    $protectedKey | Export-Clixml -LiteralPath .\.env.jwt.clixml -NoClobber
    $protectedKey.Dispose()
    Remove-Variable -Name localKey, protectedKey
}
$protectedKey = Import-Clixml -LiteralPath .\.env.jwt.clixml
$env:FIELDFIX_JWT_SECRET = [System.Net.NetworkCredential]::new('', $protectedKey).Password
$protectedKey.Dispose()
Remove-Variable -Name protectedKey
& .\.venv\Scripts\python.exe -B -m uvicorn main:app --host 127.0.0.1 --port 8000
```

In a second PowerShell terminal at the project root, start the React frontend with a Vite-compatible Node.js version.

```powershell
Set-Location -LiteralPath .\frontend
npm.cmd run dev
```

Open the URL printed by Vite, normally `http://127.0.0.1:5173`. Vite forwards same-origin `/api` requests to FastAPI on port 8000. A production host should serve the frontend and proxy `/api` on the same HTTPS origin. JWT signing configuration belongs in the backend terminal or deployment environment.

Create an intended requester account through the registration page and sign in with its password. The portal follows the role returned by `/api/auth/me`. A browser reload or logout clears the in-memory session. A previously issued JWT remains valid until it expires while its signing key and account are valid.

For direct backend checks, open `http://127.0.0.1:8000/docs` for the API schema and check `/api/health`.

```powershell
Invoke-RestMethod 'http://127.0.0.1:8000/api/health'
```

For an existing requester, verify login and the authenticated profile without displaying the access token. The optional registration lines create a real requester in the local application database. Use the disposable test suite for a verification run that leaves the application database untouched.

```powershell
$email = Read-Host 'Requester email'
$securePassword = Read-Host 'Requester password' -AsSecureString
$credentials = @{
    email = $email
    password = [System.Net.NetworkCredential]::new('', $securePassword).Password
}
try {
    # Optional first-time registration for an intended local account.
    # $registration = $credentials + @{ full_name = (Read-Host 'Full name') }
    # Invoke-RestMethod 'http://127.0.0.1:8000/api/auth/register' -Method Post -ContentType 'application/json' -Body ($registration | ConvertTo-Json -Compress)

    $login = Invoke-RestMethod 'http://127.0.0.1:8000/api/auth/login' -Method Post -ContentType 'application/json' -Body ($credentials | ConvertTo-Json -Compress)
    Invoke-RestMethod 'http://127.0.0.1:8000/api/auth/me' -Headers @{ Authorization = "Bearer $($login.access_token)" }
} finally {
    $securePassword.Dispose()
    Remove-Variable -Name securePassword, credentials, registration, login -ErrorAction SilentlyContinue
}
```

Run the isolated integration tests from `backend/`. They start a loopback server with a disposable migrated SQLite database and an ephemeral key.

```powershell
& .\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

Registration accepts `full_name`, `email`, and a password of 15–128 characters. Public registration creates requester accounts. Login issues a 20-minute HS256 token for issuer `fieldfix-ai` and audience `fieldfix-api`. Protected endpoints reload active status and role from SQLite. Future endpoints can use `Depends(require_roles(UserRole.REVIEWER))` or pass multiple allowed roles.

Before exposing these endpoints publicly, deploy behind HTTPS and configure rate limits and abuse monitoring. Registration currently returns a conflict for a duplicate address and email ownership verification is pending. Tokens remain usable until expiry while the associated account is active and the signing key is valid. Refresh, persistent sessions, OTP, email delivery, and password reset are future work.

## Requester maintenance workflow

Install the pinned dependencies into the existing virtual environment from the project root.

```powershell
& .\backend\.venv\Scripts\python.exe -m pip install -r .\backend\requirements.txt
```

Start both servers as described above. Sign in with a requester account, select **New Request**, and submit a title, description, and location. Category and photos are optional. After a successful submission the details page shows **Submitted** and any attached photos. Return to **My Requests** to test search and filters, and verify that the dashboard totals and recent requests reflect the submission. These steps add real local records. The integration tests use disposable records and an isolated upload directory.

| Method and path | Behavior |
| --- | --- |
| `POST /api/requests` | Accepts multipart fields `title`, `description`, `location`, optional `category_id`, and up to five repeated `photos` files. Returns the created request with status `submitted`. |
| `GET /api/requests` | Lists the caller's requests. Supports `status`, `q`, `page` and `per_page` (default 5, maximum 50). `pending_review` covers `submitted` and `in_review`. Search matches titles, locations, selected category names, and request numbers. Status counts cover all requests owned by the caller. |
| `GET /api/requests/categories` | Lists active categories available to requesters. |
| `GET /api/requests/{request_id}` | Returns the caller's request details and attachment metadata. |
| `GET /api/requests/{request_id}/attachments/{attachment_id}` | Streams the image after checking its parent request and owner. |

The API accepts still JPEG, PNG, and WebP images up to 5 MiB and 16 megapixels each. Pillow verifies, decodes, and re-encodes accepted photos before storage. Title and location are limited to 255 characters and description to 1,000. Anonymous calls receive HTTP 401, non-requester roles receive HTTP 403, and links to another requester's records or photos receive HTTP 404.

The submission handler rolls back database changes and removes files it wrote if validation, storage, or commit fails. An abrupt process or system crash between writing a file and committing SQLite can leave an unreferenced file. Back up the database and private upload directory together.

## Reviewer workflow

Active Reviewer accounts share the maintenance queue. The schema has no reviewer assignment, so they can read any submitted request, its evidence, the linked requester's display name, and shared review and work-order history. The reviewer responses omit requester email, password hashes, and private storage paths. Reviewer routes require the Reviewer role. Requesters still use their ownership-scoped endpoints, and Administrator accounts do not inherit Reviewer access.

| Method and path | Behavior |
| --- | --- |
| `GET /api/reviewer/dashboard` | Counts requests and work orders by actual status and returns the oldest actionable requests. |
| `GET /api/reviewer/requests` | Lists `submitted`, `in_review`, and `needs_info` requests. Supports `status`, `q`, `page`, and `per_page`. |
| `GET /api/reviewer/requests/{request_id}` | Returns the request, requester display name, and attachment metadata. |
| `GET /api/reviewer/requests/{request_id}/attachments/{attachment_id}` | Streams private evidence after checking the reviewer role and attachment parent. |
| `GET /api/reviewer/options` | Returns active categories and teams plus the schema's priority values. |
| `POST /api/reviewer/requests/{request_id}/decisions` | Saves an explicit human decision and returns its ID, the new request status, and any new work-order ID. |
| `GET /api/reviewer/history` | Lists review decisions. Supports `decision`, `q`, `page`, and `per_page`. |
| `GET /api/reviewer/work-orders` | Lists real work orders. Supports `status`, `q`, `page`, and `per_page`. |
| `GET /api/reviewer/work-orders/{order_id}` | Returns the work order, assigned team, and its recorded approval when available. |

Queue results are oldest first. History and work orders are newest first. All sorts break timestamp ties by ID. Lists default to 10 rows and accept up to 50 per page. Status counts represent the full corresponding queue, history, or work-order list before filters and search.

From `submitted` or `in_review`, a Reviewer may move a request to `approved`, `rejected`, or `needs_info`. Approval requires an active final category, a priority, and an active receiving team. It creates one `open` work order in the same transaction as the request update and review decision. Repeat or stale decisions return HTTP 409. Rejection and requests for more information create history entries without work orders. Requesting more information requires a note. The note appears in reviewer history and on the owning requester's detail page. Requester replies, requeueing, and work-order updates remain future workflows. The Reviewer-only NLP, computer vision, and priority pilot is disabled by default.

### First reviewer account

The intended staff member registers and sets their own password. An authorized administrator verifies their identity and account, assigns **Reviewer** in **Users & Roles**, and records the change in the operator's access log. The staff member signs in again to enter the Reviewer Portal.

Use the Administrator Portal to provision at least one active category and receiving team for manual approval. Start both servers, submit a request as a Requester, then sign in as a Reviewer. Open the request from **Triage Queue**, choose the category, priority, and team, and confirm approval. Verify **Work Orders** and **History**, then revisit **My Requests** as the original Requester. Use another request to verify **Request More Info** and its requester-visible note. The automated tests use disposable accounts, records, and evidence.

## Administrator management

### Bootstrap the first administrator

An authorized local operator runs the bootstrap in an interactive VS Code PowerShell terminal with the backend stopped. Run it from the project root.

```powershell
Set-Location -LiteralPath .\backend
& .\.venv\Scripts\python.exe -m alembic upgrade head
& .\.venv\Scripts\python.exe -B -m app.bootstrap_admin
```

Enter the operator's name, a new staff email, and the password twice at the hidden prompts. The command uses the existing Argon2id hasher and prints the created user ID. It refuses to run again if any administrator exists, including an inactive one. Its conditional insert also protects against simultaneous bootstrap attempts. Existing registered addresses are rejected. Start both servers as described above and sign in with the new account. Use **Users & Roles** to assign staff roles after verifying the intended account. Public registration continues to create Requester accounts.

### Administration API

The API checks the caller's current database role and active status. Administrative writes recheck the caller within the SQLite write transaction. Concurrent account changes preserve at least one active administrator.

| Method and path | Behavior |
| --- | --- |
| `GET /api/admin/dashboard` | Returns actual user, active-role, category, team, and work-order counts. |
| `GET /api/admin/users` | Searches by name or email and optionally filters by `role` and `is_active`. |
| `PUT /api/admin/users/{user_id}` | Updates role and active status. A change removing the last active administrator returns HTTP 409. |
| `GET /api/admin/categories`, `GET /api/admin/team-options` | Lists categories and provides active choices for default teams. |
| `POST /api/admin/categories`, `PUT /api/admin/categories/{category_id}` | Creates or edits a category's name, active status, and optional default team. |
| `GET /api/admin/teams` | Lists teams and their number of open or in-progress work orders. |
| `POST /api/admin/teams`, `PUT /api/admin/teams/{team_id}` | Creates or edits a team's name, specialty, and active status. |
| `GET /api/admin/settings` | Returns the fixed AI safety disclaimer and current public Requester registration policy. |

Management lists support `q`, `page`, and `per_page` (default 10, maximum 50). Category and team names are trimmed and checked for duplicates using SQLite's case-insensitive comparison for ASCII. Inactivation preserves existing IDs and references. A new default-team assignment requires an active team, while a category with a previously assigned inactive team can keep that link. Active work-order counts measure workload; the schema has no capacity or staffing availability data. The Settings page is read-only because the schema has no editable system settings. Changing policy requires a separately implemented configuration workflow.

### Future NLP category mapping

Keep a reviewed, versioned mapping from each released model's label to a stable `categories.id`. Record the model version and mapping version with each future recommendation. A category rename preserves the ID. An unmapped label or inactive category should yield no automatic category suggestion and route to manual review. Review and release the mapping independently of category CRUD. Adding, renaming, or disabling a category never changes the model's labels or retrains the model. The installed pilot uses frozen models and keeps NLP suggestions research-only while validation and live binding approval are pending.
