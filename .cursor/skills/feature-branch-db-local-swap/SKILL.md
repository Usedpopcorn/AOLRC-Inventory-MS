---
name: feature-branch-db-local-swap
description: Safely handles database changes on non-main branches by switching DATABASE_URL to local SQLite, clearly communicating DB target and side effects, running Docker migrations/tests, and restoring Supabase URL afterward. Use when the user requests DB migrations, schema testing, or database validation outside main.
---

# Feature Branch DB Local Swap

Use this workflow when DB changes are needed outside `main`.

## Goal

- Prevent schema drift or accidental shared DB changes on feature branches.
- Keep the user informed at each step about the active DB target.

## Workflow

1. Confirm branch:
   - Run `git branch --show-current`.
2. If branch is not `main`, announce intent:
   - "I will switch `DATABASE_URL` to local SQLite for safe branch testing."
3. Update `.env` `DATABASE_URL` to:
   - `sqlite:////app/instance/local_test.db`
4. Run DB operations in Docker:
   - `docker compose up -d --build`
   - `docker compose exec web flask db upgrade`
5. If local DB is empty, seed data when needed for route testing.
6. Validate migration state:
   - `docker compose exec web flask db current`
7. After testing is complete, restore `.env` `DATABASE_URL` to Supabase pooler.
8. Confirm restoration and final DB target to the user.

## Communication Template

Use concise status updates:

- Before swap:
  - "You are on `<branch>`, so I am using local SQLite to avoid changing shared Supabase."
- Before migration:
  - "Running migration against `<target_db>` now."
- After migration:
  - "Migration completed on `<target_db>` at revision `<revision>`."
- On restore:
  - "Restored `.env` `DATABASE_URL` back to Supabase pooler."

## Safety Rules

- Never run shared Supabase upgrade from non-`main` branch.
- Never commit `.env`.
- Never propose manual Supabase schema edits.
- Always mention side effects: local SQLite may start empty and require seeding.
