# AGENTS

Use this file as the entry point for UI and general repo navigation.

## Repo Map

- `templates/`: Jinja templates and shared macros.
- `static/css/styles.css`: shared UI tokens, layout primitives, responsive rules, and state styles.
- `templates/_ui_macros.html`: page headers, toolbars, chips, form sections, empty/loading/banner/validation states.
- `templates/_inventory_macros.html`: shared inventory rows, family/group rows, singleton handling, detail panels.
- `templates/_item_macros.html`: item identity and structure badges.

## UI Source Of Truth

Before adding page-local UI, check these in order:

1. `templates/_ui_macros.html`
2. `templates/_inventory_macros.html`
3. `templates/_item_macros.html`
4. `static/css/styles.css`

Canonical docs:

- `UI_COMPONENTS.md`
- `RESPONSIVE_UI_RULES.md`
- `UI_REGRESSION_CHECKLIST.md`
- `docs/skills/ui-consistency/`

## Required Reuse Rules

- Use `ui.page_header` for page headers before building a custom title/action shell.
- Use `ui.toolbar` and shared control classes for search/filter/sort/action surfaces.
- Use shared chips/status/state helpers instead of Bootstrap badge drift.
- Use shared inventory macros for operational inventory rows, family rows, singleton rows, and detail panels.
- Keep singleton assets in the same row system, but use presence/condition language instead of quantity/par language.
- Preserve parent/child hierarchy; do not invent a second grouping style.

## Responsive Expectations

- Validate at `375`, `768`, `973`, `1199`, and `1440`.
- Operational workflows should not depend on horizontal scrolling on small screens.
- Supplies uses card/mobile inventory presentation below `1200px`; do not force the desktop audit table into smaller widths.
- Venue and dashboard toolbars should stay aligned at intermediate widths, not just full mobile or full desktop.

## Validation After UI Changes

- `git diff --check`
- Jinja compile pass across `templates/`
- `create_app()` boot smoke test
- UI regression sweep using `UI_REGRESSION_CHECKLIST.md`

## Avoid Reintroducing Drift

- No one-off spacing/alignment hacks when shared tokens/classes exist.
- No new page-local badge/card/header systems when a shared primitive already fits.
- No generic Bootstrap alert/badge usage for states that already have shared UI patterns.
