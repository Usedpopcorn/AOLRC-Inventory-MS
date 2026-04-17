# AOLRC-Inventory-MS

Flask inventory app for AOLRC venues, supply audits, quick checks, and admin item management.

## Local Dev

The repo is Docker-first for app runtime, but local validation is now standardized so humans and AI agents can run the same checks.

### Bootstrap Dev Environment

```powershell
.\scripts\bootstrap_dev.ps1
```

This creates `.venv`, installs dev dependencies, and installs both `pre-commit` and `pre-push` git hooks.
Use standard CPython 3.12 or 3.13 for local bootstrap. Avoid free-threaded `3.13t`.

### Start The App

```powershell
docker compose up --build
```

App URL: `http://127.0.0.1:5000/dashboard`

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
2. Use Docker for normal app runtime and shared workflow parity.
3. Use `.\scripts\check.ps1` before and after substantial changes.
4. Use `pytest` for fast regression checks that do not depend on Supabase.
5. Keep feature-branch DB experiments on local SQLite rather than the shared Supabase database.

## Important Repo Docs

- [AGENTS.md](AGENTS.md)
- [UI_COMPONENTS.md](UI_COMPONENTS.md)
- [RESPONSIVE_UI_RULES.md](RESPONSIVE_UI_RULES.md)
- [UI_REGRESSION_CHECKLIST.md](UI_REGRESSION_CHECKLIST.md)
- [AGENT_PROMPT_SNIPPET_UI.md](AGENT_PROMPT_SNIPPET_UI.md)
