# UI Regression Checklist

Use this checklist whenever a page, template, or shared style changes.

## Shared Shell

- Use `templates/_ui_macros.html` for page headers before creating a custom heading block.
- Use the shared toolbar shell and `ui-control` heights for search, filters, sorts, and primary actions.
- Use `ui.form_section` for admin/settings-style forms unless the page already has a stronger shared pattern.
- Prefer shared spacing tokens and shared layout classes over one-off padding or gap values.

## Inventory UI

- Use `templates/_inventory_macros.html` as the source of truth for operational inventory rows, family rows, child rows, detail panels, and inventory state/coverage helpers.
- Preserve the parent/child hierarchy treatment: one family summary row, one child indent system, one detail-panel style.
- Keep singleton assets in the same row shell as quantity items, but show presence/condition language instead of par/count language where appropriate.
- Do not rebuild issue summaries, coverage pills, or state badges locally if a shared macro already exists.

## Chips And Status

- Use shared chips or status pills instead of ad hoc Bootstrap badges on operational and admin pages.
- Keep one chip per concept where possible and avoid stacking redundant chips that repeat nearby column data.
- Use `status-pill` for item/venue status and shared inventory state chips for coverage, missing, stale, and similar audit states.

## Responsive QA

- Check layouts at `375`, `768`, `973`, `1199`, and `1440` widths before finishing UI work.
- Verify toolbars wrap cleanly and stay aligned at intermediate widths, not just mobile and full desktop.
- Verify operational inventory pages do not depend on horizontal scrolling below desktop breakpoints.
- Verify venue cards, row titles, and long names wrap naturally without collapsing into one-word-per-line stacks.

## Before Adding New Local Markup

- Reuse shared macros before inventing a new row, card, chip, or header pattern.
- If a page truly needs a local variation, extend the shared primitive first when the variation is likely to recur.
- Remove old aliases or page-only overrides only after confirming the shared primitive is the active source of truth.
- Run `git diff --check`, a Jinja compile pass, and a quick app boot smoke test after UI consolidation work.
