# AOLRC-Inventory-MS

Flask inventory app for AOLRC venues, supply audits, quick checks, and admin item management.

## Local Dev

The repo is Docker-first for app runtime, but local validation is now standardized so humans and AI agents can run the same checks.

### Bootstrap Dev Environment

```powershell
.\scripts\bootstrap_dev.ps1
```

This creates `.venv`, installs dev dependencies, installs both `pre-commit` and `pre-push` git hooks, and on Windows can provision a repo-local `rg.exe` when a discovered copy is blocked by permissions.
Use standard CPython 3.12 or 3.13 for local bootstrap. Avoid free-threaded `3.13t`.

### Load Repo Tooling Into The Current Shell

```powershell
.\scripts\dev_shell.ps1
```

This prepends `.tools\bin` when present and the repo virtualenv to `PATH` for the current PowerShell session so `python`, `pytest`, `ruff`, and `pre-commit` resolve cleanly, and `rg` does too when a usable ripgrep binary is available.
It is the quickest fix when Windows resolves `python` to the Microsoft Store alias or `rg` to a discovered copy that cannot execute in place.

### Start The App

```powershell
docker compose up --build
```

App URL: `http://127.0.0.1:5000/dashboard`

Docker Compose also starts Mailpit for local email capture:

- Mailpit inbox: `http://127.0.0.1:8025`
- Mailpit SMTP: `mailpit:1025` from the Docker web container
- Local host SMTP, if you run the app outside Docker: `127.0.0.1:1025`

With the `.env.example` mail settings, password reset/setup emails are sent to Mailpit and never leave your machine. Click the reset/setup link from the Mailpit inbox to test the full flow locally.

### Password Reset Email

The account system sends password reset and managed-account setup links through the internal mail service in `app/services/mail_service.py`. Configure links with `APP_BASE_URL`; local Docker development should use `http://127.0.0.1:5000`, while production must use the deployed HTTPS origin.

Development defaults:

```dotenv
APP_BASE_URL=http://127.0.0.1:5000
MAIL_ENABLED=true
MAIL_BACKEND=smtp
MAIL_SERVER=mailpit
MAIL_PORT=1025
MAIL_USE_TLS=false
MAIL_USE_SSL=false
MAIL_DEFAULT_SENDER=AOLRC Inventory <noreply@localhost>
MAIL_SUPPRESS_SEND=false
MAIL_CAPTURE_UI_URL=http://127.0.0.1:8025
```

For a non-Docker Mailpit run, install/run Mailpit locally and use:

```dotenv
MAIL_SERVER=127.0.0.1
MAIL_PORT=1025
```

Production SMTP is provider-agnostic and should work with services such as Resend, Postmark, SendGrid, or similar SMTP providers:

```dotenv
APP_BASE_URL=https://inventory.example.org
MAIL_ENABLED=true
MAIL_BACKEND=smtp
MAIL_SERVER=smtp.provider.example
MAIL_PORT=587
MAIL_USERNAME=provider-username-or-api-key
MAIL_PASSWORD=provider-secret
MAIL_USE_TLS=true
MAIL_USE_SSL=false
MAIL_DEFAULT_SENDER=AOLRC Inventory <inventory@example.org>
MAIL_SUPPRESS_SEND=false
```

Production requires a verified sending domain or verified sender address, depending on the provider. Do not commit real SMTP credentials. If mail is disabled or misconfigured, password reset requests keep a generic user-facing response and log a sanitized error for operators.

Security notes:

- There is no public registration flow; admins create managed accounts.
- `AUTH_ALLOW_DEV_QUICK_LOGIN` and `AUTH_DEV_EXPOSE_PASSWORD_LINKS` are development-only and are blocked outside development.
- Keep `SECRET_KEY` unique in production and set `APP_BASE_URL` to the public HTTPS origin so reset/setup links do not point at localhost.
- Login protection allows 8 recent failed password attempts before a 15-minute account lockout by default. Stale failed-attempt counters are cleared when the recent throttling window is empty, so a user should not be locked by one typo after a quiet period or app restart.

### Prepare SQLite + Docker + Migration Status

```powershell
.\scripts\prepare_sqlite_workflow.ps1
```

This enforces branch-safe SQLite config, runs migration checks, recreates the Docker `web` service, and verifies local dummy auth users so the browser session is ready without manual cleanup steps.

## Standard Validation Commands

### Repo Validation

Runs app boot smoke, compiles every Jinja template through Flask, and checks a few core routes against an isolated SQLite database.

```powershell
.\.venv\Scripts\python.exe scripts\validate_repo.py
```

### Smoke Tests

```powershell
.\.venv\Scripts\python.exe -m pytest
```

### Lint

```powershell
.\.venv\Scripts\python.exe -m ruff check .
```

### Diff Hygiene

```powershell
git diff --check
```

### Full Local Check

```powershell
.\scripts\check.ps1
```

This runs repo validation, smoke tests, `ruff`, `pre-commit`, and `git diff --check`.
The lint step is currently scoped to repo automation and test files so it stays actionable while legacy app lint debt is still being paid down.

## Recommended Workflow For Agents

1. Read [AGENTS.md](AGENTS.md) and the shared UI docs before touching templates or CSS.
2. Run `.\scripts\dev_shell.ps1` before tool-heavy local work so `rg` and the repo Python tools resolve correctly.
3. Use Docker for normal app runtime and shared workflow parity.
4. Run `.\scripts\prepare_sqlite_workflow.ps1` before browser verification to enforce SQLite + migration parity.
5. Use `.\scripts\check.ps1` before commit/push to catch git/test/lint issues early.
6. Use `pytest` for fast regression checks that do not depend on Supabase.
7. Keep feature-branch DB experiments on local SQLite rather than the shared Supabase database.

## Important Repo Docs

- [AGENTS.md](AGENTS.md)
- [UI_COMPONENTS.md](UI_COMPONENTS.md)
- [RESPONSIVE_UI_RULES.md](RESPONSIVE_UI_RULES.md)
- [UI_REGRESSION_CHECKLIST.md](UI_REGRESSION_CHECKLIST.md)
- [AGENT_PROMPT_SNIPPET_UI.md](AGENT_PROMPT_SNIPPET_UI.md)
