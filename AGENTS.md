# AGENTS

Use this file as the entry point for UI and general repo navigation.

## Repo Map

- `templates/`: Jinja templates and shared macros.
- `templates/admin/layout.html`: shared admin shell with the persistent desktop rail and mobile admin nav.
- `templates/admin/_admin_macros.html`: admin nav, regions, panels, disclosures, summary cards, and admin-only list/feed shells.
- `templates/admin/_item_catalog_results.html`: replaceable item catalog results region used by live filtering and paging.
- `static/css/styles.css`: shared UI tokens, layout primitives, responsive rules, and state styles.
- `templates/_ui_macros.html`: page headers, toolbars, chips, form sections, empty/loading/banner/validation states.
- `templates/_inventory_macros.html`: shared inventory rows, family/group rows, singleton handling, detail panels.
- `templates/_item_macros.html`: item identity and structure badges.
- `scripts/dev_shell.ps1`: repo-local PowerShell bootstrap that puts the repo virtualenv on `PATH` and prefers a working local `rg` when one is available.
- `scripts/bootstrap_dev.ps1`: creates the standard `.venv`, installs dev dependencies, and on Windows can provision a repo-local `rg.exe` if the discovered copy cannot execute in place.

## Dev Environment Quick Start

- Run `.\scripts\dev_shell.ps1` first in a Windows PowerShell session when `rg`, `python`, `pytest`, `ruff`, or `pre-commit` resolve incorrectly.
- Run `.\scripts\bootstrap_dev.ps1` if `.venv` does not exist yet.
- Keep repo-local tooling in `.tools/`; it is intentionally ignored by git.

## UI Source Of Truth

Before adding page-local UI, check these in order:

1. `templates/_ui_macros.html`
2. `templates/admin/_admin_macros.html` when working in admin pages
3. `templates/_inventory_macros.html`
4. `templates/_item_macros.html`
5. `static/css/styles.css`

Canonical docs:

- `UI_COMPONENTS.md`
- `RESPONSIVE_UI_RULES.md`
- `UI_REGRESSION_CHECKLIST.md`
- `docs/skills/ui-consistency/`

## Required Reuse Rules

- Use `ui.page_header` for page headers before building a custom title/action shell.
- Use `ui.toolbar` and shared control classes for search/filter/sort/action surfaces.
- Use `templates/admin/layout.html` and `admin_ui` region/panel/disclosure helpers before introducing a page-local admin shell.
- Use shared chips/status/state helpers instead of Bootstrap badge drift.
- Use shared inventory macros for operational inventory rows, family rows, singleton rows, and detail panels.
- Keep singleton assets in the same row system, but use presence/condition language instead of quantity/par language.
- Preserve parent/child hierarchy; do not invent a second grouping style.
- For dense admin/supporting data, prefer preview-first sections with disclosures or capped lists instead of loading every record fully expanded.
- When a directory/filter surface is already expected to feel live in-app, prefer debounced instant filtering over an extra manual Apply step.

## Responsive Expectations

- Validate at `375`, `768`, `973`, `1199`, and `1440`.
- Operational workflows should not depend on horizontal scrolling on small screens.
- Supplies uses card/mobile inventory presentation below `1200px`; do not force the desktop audit table into smaller widths.
- Venue and dashboard toolbars should stay aligned at intermediate widths, not just full mobile or full desktop.
- Admin uses the shared sidebar shell at desktop/large tablet and the compact admin nav on mobile; do not reintroduce a primary horizontal admin subnav.

## Validation After UI Changes

- `git diff --check`
- Jinja compile pass across `templates/`
- `create_app()` boot smoke test
- UI regression sweep using `UI_REGRESSION_CHECKLIST.md`

## Avoid Reintroducing Drift

- No one-off spacing/alignment hacks when shared tokens/classes exist.
- No new page-local badge/card/header systems when a shared primitive already fits.
- No generic Bootstrap alert/badge usage for states that already have shared UI patterns.
