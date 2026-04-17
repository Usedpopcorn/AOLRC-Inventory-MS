# UI Consistency Checklists

## Before Editing

- Identify whether the page already uses `ui.page_header`, `ui.toolbar`, `ui.form_section`, or shared inventory macros.
- Check whether the UI change is really new, or whether an existing primitive can be extended.
- Confirm whether the page is operational inventory, admin/settings, or directory/list UI.

## Inventory Work

- Check `templates/_inventory_macros.html` first.
- Preserve parent/family summary rows.
- Keep child rows nested.
- Keep singleton assets in the shared row shell.
- Preserve detail panel structure and drilldown copy.

## Responsive QA

- Check `375`, `768`, `973`, `1199`, `1440`.
- Verify no clipped controls.
- Verify no horizontal scrolling for operational workflows on small screens.
- Verify toolbar wrapping and row alignment at intermediate widths.

## Final Validation

- `git diff --check`
- Jinja compile pass
- `create_app()` boot smoke test
- UI sweep using `UI_REGRESSION_CHECKLIST.md`
