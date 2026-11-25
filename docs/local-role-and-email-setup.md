# Local portal and email setup

Open http://127.0.0.1:5173/login on the demo machine. Select the account's assigned role. A valid password or a valid email code used on another tab produces a role-mismatch message. The API checks the current role and active status in SQLite on protected requests.

## First Administrator

From the project root, stop the backend and make a verified backup of the local database before running these commands in an interactive PowerShell terminal. The project migration and bootstrap commands create the initial Administrator. Choose a new staff email you control and enter a unique password of 15–128 characters at the hidden prompts.

```powershell
Push-Location .\backend
& .\.venv\Scripts\python.exe -m alembic upgrade head
& .\.venv\Scripts\python.exe -B -m app.bootstrap_admin
Pop-Location
```

An existing Administrator, even an inactive one, blocks bootstrap. Sign in using **Admin**. Keep the password in your password manager. The application has no public staff registration or self-service password reset.

## Reviewer

1. Have the intended Reviewer register a Requester account using their own email and password.
2. As Administrator, open **Users & Role Management**, locate that specific email, and choose **Edit**. Select **Reviewer**, keep the account active, then choose **Review changes** and **Confirm and save**.
3. Have the user sign out and sign back in through **Reviewer** with the same credentials. Open the triage queue. Role changes revoke remembered sessions and pending codes. A verified mailbox remains enrolled while the address stays the same.

## Private SMTP configuration on Windows

Use an authorized sender account and a provider that supports authenticated SMTP over **implicit TLS**. Confirm its hostname, port (default 465), permitted From address, username and SMTP password or provider-issued app password. The adapter verifies the TLS certificate. Providers requiring STARTTLS or OAuth-only authentication need another supported configuration.

In a private backend PowerShell terminal, save the provider settings once. Windows DPAPI encrypts the credential password for this Windows account and computer. Use the credential prompt for the provider username and password. Keep this file outside the project.

```powershell
$smtpPath = Join-Path $env:LOCALAPPDATA 'FieldFixAI\auth\smtp.clixml'
$smtp = [pscustomobject]@{
    Server = Read-Host 'SMTP hostname'
    Port = Read-Host 'Implicit TLS port (usually 465)'
    From = Read-Host 'Authorized sender address'
    Credential = Get-Credential -Message 'SMTP username and provider password'
}
$smtp | Export-Clixml -LiteralPath $smtpPath -NoClobber
Remove-Variable smtp
```

For each backend restart with mail enabled, stop the current backend with Ctrl+C. In a dedicated backend terminal, load the private settings and start the existing launcher. Keep the frontend in a separate terminal.

```powershell
$smtp = Import-Clixml -LiteralPath "$env:LOCALAPPDATA\FieldFixAI\auth\smtp.clixml"
try {
    $env:FIELDFIX_SMTP_HOST = $smtp.Server
    $env:FIELDFIX_SMTP_PORT = $smtp.Port
    $env:FIELDFIX_SMTP_FROM = $smtp.From
    $env:FIELDFIX_SMTP_USER = $smtp.Credential.UserName
    $env:FIELDFIX_SMTP_PASSWORD = $smtp.Credential.GetNetworkCredential().Password
    powershell.exe -NoProfile -File "$env:LOCALAPPDATA\FieldFixAI\auth\Start-Backend.ps1"
} finally {
    foreach ($name in 'HOST','PORT','FROM','USER','PASSWORD') {
        [Environment]::SetEnvironmentVariable("FIELDFIX_SMTP_$name", $null, 'Process')
    }
    Remove-Variable smtp
}
```

To verify one real delivery, choose a mailbox you control and authorize for the check. Select the correct role and **Email OTP → Enable email sign-in with my password**. Enter the account email and password, send one enrollment code, then enter the six digits in the application within five minutes. Successful verification establishes mailbox ownership. Subsequent OTP sign-in uses a fresh code after the 60-second send cooldown. An accepted send request alone does not confirm delivery. Never share codes or credentials in QA reports.

## Normal local launch

For password and remembered sign-in without SMTP, start the backend with the existing launcher. It loads the Windows-user-encrypted signing key and configures the trusted loopback origin.

```powershell
powershell.exe -NoProfile -File "$env:LOCALAPPDATA\FieldFixAI\auth\Start-Backend.ps1"
```

From the project root, start the frontend separately. Use Ctrl+C in each service's terminal to stop it.

```powershell
Push-Location .\frontend
npm.cmd run dev -- --host 127.0.0.1 --port 5173 --strictPort
Pop-Location
```
