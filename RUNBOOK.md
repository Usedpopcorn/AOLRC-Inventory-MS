# AOLRC Inventory Runbook

This runbook is the practical "how to run and maintain the app" guide for day-to-day use.
It is written for operators, developers, and future maintainers.

---

## 1) What This App Runs On

- **Backend**: Flask + SQLAlchemy + Alembic migrations.
- **Primary runtime (team standard)**: Docker Compose.
- **Live test runtime (Windows)**: Waitress + Cloudflare Tunnel.
- **Email**: SMTP provider (Resend in current live test).
- **Databases**:
  - **Feature branches**: local SQLite (safe, isolated).
  - **Main/shared**: team shared Supabase Postgres.

---

## 2) Daily Quick Start

### A. Standard Local Development (Docker-first)

1. Open PowerShell in repo root.
2. Start app and dependencies:
   - `docker compose up --build`
3. Open app:
   - `http://127.0.0.1:5000/dashboard`
4. Mail inbox (local Mailpit):
   - `http://127.0.0.1:8025`

### B. Live Test Mode (Windows host + Waitress + Cloudflare)

Use two terminals:

- **Terminal 1 (server)**:
  - `.\start_server.bat`
- **Terminal 2 (tunnel)**:
  - `.\start_tunnel.bat`

Quick control commands:
- Stop full live stack: `.\stop_live_stack.bat`
- Restart full live stack: `.\restart_live_stack.bat`

Public URL target:
- `https://www.aolrcinventory.org`

---

## 3) Start / Stop / Restart Workflows

## A. Docker dev runtime

- **Start**: `docker compose up --build`
- **Stop**: `docker compose down`
- **Restart cleanly**:
  1. `docker compose down`
  2. `docker compose up --build`

Tip: if `.env` values changed, recreate containers so environment changes are applied.

## B. Live test runtime (Waitress + Tunnel)

### Start (recommended)

- `.\start_server.bat`
- `.\start_tunnel.bat`

### Stop

- If those are running in foreground terminals: press `Ctrl+C` in each terminal.
- Script option:
  - `.\stop_live_stack.bat`

### Restart

1. Run:
   - `.\restart_live_stack.bat`
2. If you prefer manual restart:
   - `.\stop_live_stack.bat`
   - `.\start_server.bat`
   - `.\start_tunnel.bat`

### Alternate managed stop script

If you used `scripts\start_public_test.ps1` (PID-file workflow), stop with:
- `.\scripts\stop_public_test.ps1`

---

## 4) Health and Verification Checklist

Run this after restarts, config changes, or before demos.

1. App reachable:
   - Local: `http://127.0.0.1:8000` (Waitress mode) or `:5000` (Docker mode)
2. Login page loads and signs in.
3. Dashboard and admin pages load.
4. Static assets (CSS/icons) load.
5. Password reset email sends and link works.
6. 2FA/email verification flow works for unknown device.
7. CSRF-protected form submission still works.

Useful logs:
- Docker: `docker compose logs --tail=150`
- Live scripts logs: `instance\deploy\*.log` (when using the managed public-test script family)

---

## 5) Backup and Restore (Data Safety)

## A. Create backup (Postgres live-test DB)

Use:
- `.\scripts\backup_live_test_db.ps1`

What it does:
- Reads `DATABASE_URL` from `instance\deploy\public_test_app.env`
- Runs `pg_dump` custom format
- Writes timestamped backup into `backups\postgres\`

Expected output includes:
- backup file path
- backup file size

## B. Restore guidance

Use `pg_restore` only after confirming target DB.
Never restore over active live-test data unless intentionally replacing it.

Example pattern:
- Create a new target DB first.
- Restore into the new DB.
- Validate app behavior.
- Switch app connection only after confirmation.

---

## 6) Branch Update Workflow (Safe Team Workflow)

This section prevents migration and schema drift problems.

## A. Before updating a feature branch

1. Check branch:
   - `git branch --show-current`
2. Ensure feature branch uses SQLite (not shared Supabase).
3. Run:
   - `.\scripts\prepare_sqlite_workflow.ps1`

That script:
- switches to SQLite mode safely,
- runs migration checks,
- recreates Docker web service,
- verifies local dummy users.

## B. Bring feature branch up to date with main

1. Save/stash local WIP if needed.
2. Fetch latest refs:
   - `git fetch origin`
3. Update your branch (merge or rebase based on team practice).
4. Re-run:
   - `.\scripts\prepare_sqlite_workflow.ps1`
5. Run validation:
   - `.\scripts\check.ps1`

## C. Database target safety rule

- **Never run shared Supabase upgrades from feature branches.**
- Feature branches should run migration checks against local SQLite.
- Shared DB upgrades should happen from `main` (or migration captain workflow).

---

## 7) Migration / Database Integrity Workflow

## Live local Postgres one-command upgrade (current setup)

Use this as the default command for your current live-test environment:

- `.\upgrade_live_postgres_db.bat`

What it does:
- loads `instance\deploy\public_test_app.env`,
- verifies `DATABASE_URL` is PostgreSQL,
- blocks non-local hosts unless explicitly overridden,
- runs `flask db current`,
- runs `flask db upgrade heads`,
- prints post-upgrade `flask db current` and `flask db heads`.

## A. Standard integrity checks

Run (Docker-first):
- `docker compose exec web flask db current`
- `docker compose exec web flask db heads`

Interpretation:
- `db current` shows applied revision(s).
- `db heads` should normally return one head after branch is reconciled.

## B. If Alembic reports multiple heads

1. Inspect:
   - `docker compose exec web flask db heads`
2. Merge heads intentionally:
   - `docker compose exec web flask db merge <head1> <head2> -m "merge heads"`
3. Commit the merge migration file.

## C. If Alembic cannot locate revision

Likely cause: missing migration file in repo history.

Steps:
1. Check branch history and migration files.
2. Restore/commit missing migration file.
3. Re-run `flask db current` and `flask db upgrade`.

## D. Hard rules for integrity

- Migrations in git are the schema source of truth.
- Do not manually edit schema in Supabase UI.
- Do not auto-run destructive schema updates on app boot unintentionally.
- Do not commit `.env` secrets or connection passwords.

---

## 8) Configuration Management (What to edit where)

## A. Local dev defaults

- `.env` (local machine, not committed)
- `.env.example` (template only, safe values only)

## B. Live test environment file

- `instance\deploy\public_test_app.env`

Keep current:
- `APP_BASE_URL=https://www.aolrcinventory.org`
- SMTP `MAIL_*` values
- Auth security settings

After changing env values in running containers:
- recreate/restart relevant process/container.

---

## 9) Email + Login Verification Maintenance

## A. Password reset and setup emails

Verify:
- SMTP credentials valid.
- sender/domain verified.
- DNS SPF/DKIM/DMARC in Cloudflare.

## B. Risk-based login verification (email 2FA)

Key settings:
- `LOGIN_EMAIL_2FA_ENABLED`
- `LOGIN_2FA_CODE_TTL_MINUTES`
- `LOGIN_2FA_MAX_ATTEMPTS`
- `LOGIN_2FA_RESEND_COOLDOWN_SECONDS`
- `TRUSTED_DEVICE_DAYS_ADMIN|STAFF|VIEWER`

Admin operation:
- Admin Users page can revoke trusted devices for a user.

---

## 10) Routine Maintenance Schedule (Suggested)

### Daily
- Check app reachability.
- Verify login and key workflows.
- Check recent logs for repeated auth/mail failures.

### Weekly
- Run full local checks: `.\scripts\check.ps1`
- Perform and verify a backup using `backup_live_test_db.ps1`.
- Verify password reset + login verification flow.

### Before demo/release
- Restart server+tunnel.
- Run health checklist.
- Confirm email sending and login flows.
- Confirm latest backup exists.

---

## 11) Troubleshooting Guide

## Problem: App won't start in Docker

Check:
- `docker compose logs --tail=150`
- Is port already used?
- Did `.env` change without container recreate?

Fix:
1. `docker compose down`
2. `docker compose up --build`

## Problem: Waitress starts but site not reachable publicly

Check:
- Waitress terminal running?
- Tunnel terminal running?
- Cloudflare tunnel auth/state?

Fix:
1. Run `.\restart_live_stack.bat`.
2. Re-auth cloudflared if needed:
   - `cloudflared tunnel login`
3. Confirm DNS host still points to tunnel.

## Problem: Login says invalid credentials/account unavailable

Check:
- account active?
- account locked?
- password reset completed?
- forced password setup pending?

Admin action:
- unlock account in Admin Users.
- issue password reset/setup link if needed.

## Problem: Verification code not arriving

Check:
- `MAIL_ENABLED=true`
- SMTP host/port/user/password valid
- sender domain verified
- SPF/DKIM records present

Also check app logs for sanitized mail delivery failure status.

## Problem: `create_app()` fails on feature branch with DB policy error

Cause:
- feature branch using non-SQLite DB URL.

Fix:
- switch to SQLite safely:
  - `.\scripts\prepare_sqlite_workflow.ps1`

## Problem: Migration errors (`multiple heads`, `can't locate revision`)

Use Section 7 directly (migration integrity workflow).

---

## 12) AI Agent Prompt Snippets (Copy/Paste)

Use these prompts when you want fast, consistent AI help.

### A. Safe restart and health check

`Restart the app safely for current mode, verify login/dashboard/admin load, and report any errors with exact commands used.`

### B. Branch update + migration safety

`Help me update my feature branch from main using the repo’s safe SQLite workflow. Run migration integrity checks and summarize risks before any DB-affecting command.`

### C. Backup + restore rehearsal

`Run the live-test backup workflow, verify backup file integrity, and give a safe restore rehearsal plan to a non-production target database.`

### D. Email delivery verification

`Validate SMTP and APP_BASE_URL settings, run password reset email end-to-end, and report whether delivery, token use, and single-use protections all pass.`

### E. 2FA/risk-login verification testing

`Test risk-based login verification for unknown device, trusted device bypass, stale trusted device, and admin revoke workflow. Report pass/fail with reproduction steps.`

### F. Migration conflict recovery

`Investigate Alembic migration state, detect multiple heads or missing revisions, and provide the safest resolution workflow with commands and expected outputs.`

---

## 13) Command Reference (Quick Copy)

- Start dev:
  - `docker compose up --build`
- Stop dev:
  - `docker compose down`
- Logs:
  - `docker compose logs --tail=150`
- Validate repo:
  - `.\scripts\check.ps1`
- Prepare safe SQLite branch workflow:
  - `.\scripts\prepare_sqlite_workflow.ps1`
- Start live server:
  - `.\start_server.bat`
- Start live tunnel:
  - `.\start_tunnel.bat`
- Stop live server:
  - `.\stop_server.bat`
- Stop live tunnel:
  - `.\stop_tunnel.bat`
- Stop full live stack:
  - `.\stop_live_stack.bat`
- Restart full live stack:
  - `.\restart_live_stack.bat`
- Upgrade live local Postgres DB:
  - `.\upgrade_live_postgres_db.bat`
- Backup live Postgres:
  - `.\scripts\backup_live_test_db.ps1`
- Stop managed public-test PIDs:
  - `.\scripts\stop_public_test.ps1`

---

## 14) Final Safety Rules

- Never commit secrets (`.env`, SMTP password, DB passwords, tunnel tokens).
- Prefer Docker-first workflows for consistency.
- Keep feature branches on SQLite for DB safety.
- Treat migrations as source-of-truth for schema.
- Do not manually edit Supabase schema.
- Always create a backup before risky DB changes.
