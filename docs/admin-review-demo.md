# Local administration and review demonstration

Use the existing authorized Administrator, Reviewer and Requester accounts. Account creation and role assignment are documented in [Local role and email setup](local-role-and-email-setup.md).

## Maintenance catalog

Review **Maintenance Teams** and **Maintenance Categories** before adding records. Reuse existing rows when their names and purpose match. The live database has no starter records until the administrator creates them or the owner explicitly approves initialization.

Create the teams first, then create the categories and select their default teams.

| Category | Default team |
|---|---|
| Plumbing | Plumbing Maintenance |
| Electrical | Electrical Maintenance |
| HVAC | HVAC Maintenance |
| Civil / Structural | Civil Maintenance |
| General Maintenance | General Maintenance |

Keep demo categories and teams active. Deactivation removes a row from new assignments and preserves its historical references. The reviewer selects the final category, priority and team.

## Demonstration

1. Sign in as Requester, submit a complaint and attach a supported photo. Use a concrete close-up if you intend to demonstrate image screening.
2. Sign in as Reviewer and open a pending complaint from the triage queue. Under **AI Recommendations & Reviewer Decision**, choose **Run NLP demo** or **Suggest priority by rule**. For a suitable JPEG, select the attachment, confirm it is a concrete close-up, and choose **Screen selected photo**.
3. Select the category, priority and receiving team. Approve and confirm to create one work order. The other choices are **Reject** and **Request More Info**.
4. Sign in as the Requester to see the updated status. Sign in as Administrator to confirm current users, categories, teams and active work orders.

The NLP demo uses the frozen Milestone 11 TF-IDF + LinearSVC model. For a viva example, submit a description such as “The sink is clogged.” The scope rule requires a clear fault involving one supported system. The model must agree with that system and pass its frozen decision-margin threshold. A margin is a model score, so the panel displays the predicted label without a confidence percentage. Try “The roof needs patching” to demonstrate abstention. The pilot did not establish reliable out-of-scope detection or meet the selective-accuracy target. Research output cannot fill the review form. Actionable suggestions remain disabled pending adequate scope validation and approved bindings.

Priority rules abstain without sufficient evidence. Concrete screening uses a provisional model with potentially related source photos across its evaluation split. The result requires human interpretation and cannot establish structural safety.

## Account management

Use **Users & Role Management** to change a name, email, role or active status. Email changes invalidate previous mailbox verification and require enrollment at the new address before OTP login. Role, email, password and activation changes revoke the account's sessions.

To reset another active user's password, verify their identity through your approved local process, confirm **Reset password** and share the displayed temporary password through a private channel. The credential expires in 24 hours and cannot open a session. The user enters it on the Password tab and sets a new password before signing in. Only its hash is stored. Issue a replacement if the temporary credential is lost or expires.

Archive Requester or Reviewer accounts when their access should end and their maintenance history must be retained. Use ordinary deactivation if the account may need to be reactivated later.

**System Settings** persists the Requester registration switch. Catalog changes use their own management pages. AI deployment availability and the safety disclaimer are read-only.

For a local restart with the NLP research demo, concrete screening and priority rules, open two PowerShell terminals from the project root. The optional demo backend launcher uses an existing Windows-encrypted signing key on a prepared demo machine.

```powershell
powershell.exe -NoProfile -File "$env:LOCALAPPDATA\FieldFixAI\auth\Start-DemoBackend.ps1"
```

```powershell
Push-Location .\frontend
npm.cmd run dev -- --host 127.0.0.1 --port 5173 --strictPort
Pop-Location
```
