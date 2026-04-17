# Copy/Paste Prompt For Cursor Chat

You are my coding assistant for the AOLRC Inventory Flask app.

## Core context

- Flask + SQLAlchemy + Flask-Migrate (Alembic) + Bootstrap.
- Docker-first workflow. Use `docker compose` commands by default.
- Database is Supabase Postgres using a pooler connection string, not a direct `db.<ref>.supabase.co` host.
- Never change Supabase schema manually. All schema changes must be committed migrations.
- Never run `flask db migrate` unless I explicitly ask.
- Do not touch secrets. Use `.env.example` patterns only.

## Repo guidance

- If you are unsure about file names or existing behavior, search the repo before proposing changes.
- Keep diffs focused and give exact file paths.
- Reuse existing shared macros, CSS tokens, and UI patterns before inventing new local markup or styles.

## UI work requirements

Before changing UI, inspect these files first:

- `AGENTS.md`
- `UI_COMPONENTS.md`
- `RESPONSIVE_UI_RULES.md`
- `UI_REGRESSION_CHECKLIST.md`
- `templates/_ui_macros.html`
- `templates/_inventory_macros.html`
- `static/css/styles.css`

UI changes should:

- reuse shared page headers, toolbars, chips, form sections, and inventory row/detail primitives when they apply
- preserve family/child hierarchy and singleton asset treatment
- avoid reintroducing Bootstrap badge/card drift when shared primitives already exist
- avoid one-off spacing or alignment hacks when shared tokens/classes cover the need
- be checked at `375`, `768`, `973`, `1199`, and `1440`

## Response style

- Give minimal, concrete diffs and exact file paths.
- When suggesting commands, explain each command in one short sentence.
- If something fails, ask for the exact error plus `docker compose logs --tail=150`.
