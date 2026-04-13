---
name: render-live-test-deploy
description: Guides safe deployment of this AOLRC live-test copy to Render using a dedicated Supabase project. Use when preparing the live-test database, checking Render settings, verifying admin/bootstrap state, or keeping advice aligned with Render free-tier limits and this repo's Docker setup.
---

# Render Live-Test Deploy

Use this skill when the task is about deploying or validating this live-test copy on Render.

## Goal

- Keep deployment advice aligned with this repo's actual setup.
- Avoid mixing shared-dev DB rules with the dedicated live-test Supabase workflow.
- Verify bootstrap state before the app is deployed.

## Workflow

1. Confirm the target environment:
   - dedicated live-test Supabase
   - local Docker/SQLite
   - Render free web service
2. For live-test DB prep, prefer these commands:
   - `venv\Scripts\python.exe -m flask --app run.py db upgrade`
   - `venv\Scripts\python.exe seed_core_venues.py`
   - `venv\Scripts\python.exe seed_from_csv.py`
   - `venv\Scripts\python.exe -m flask --app run.py create-admin --email <email>`
3. Verify bootstrap state before saying deploy is ready:
   - migration head applied
   - schema tables exist
   - seed counts look correct
   - at least one admin user exists
4. For Render settings, keep guidance minimal:
   - health check `/healthz`
   - build context `.`
   - Dockerfile `./Dockerfile`
   - leave Docker command blank
   - free tier means no shell/pre-deploy assumptions
5. Remind the user to commit and push migration fixes before deploy.

## Safety Rules

- Never recommend manual Supabase schema edits.
- Never recommend demo/live-mixed seed scripts for the Render live test.
- Do not assume the database is ready just because migrations ran; verify counts and admin presence.
- If users are missing, stop and have the first admin created before continuing.
