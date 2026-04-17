# UI Consistency Examples

## Good Fixes

- Replacing a hand-built title/action block with `ui.page_header`.
- Reusing `ui.toolbar` and `ui-control` instead of adding page-local control heights.
- Moving a page-local inventory row into `templates/_inventory_macros.html` when another page needs the same structure.
- Converting an ad hoc alert or dashed empty box to `ui.state_banner` or `ui.empty_state`.

## Bad Fixes

- Adding a new Bootstrap badge style for a state that already maps to `ui.chip`, `ui.status_pill`, or `inventory_ui.state_badge`.
- Creating a second family/child hierarchy treatment because it feels faster locally.
- Using one-off padding, widths, or breakpoint hacks without checking shared classes/tokens first.
- Forcing an operational table to remain horizontally scrollable on small screens when the app already has stacked/mobile row patterns.

## Decision Rule

If a local fix would be useful on a second page, it probably belongs in:

- `templates/_ui_macros.html`
- `templates/_inventory_macros.html`
- `templates/_item_macros.html`
- `static/css/styles.css`
