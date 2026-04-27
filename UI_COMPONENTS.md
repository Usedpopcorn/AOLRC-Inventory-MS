# UI Components

Use this file as the practical map of the shared UI system.

## Canonical Sources

- `static/css/styles.css`: tokens, shared layout classes, state styles, focus/tap sizing, responsive behavior.
- `templates/_ui_macros.html`: page headers, toolbars, chips, form sections, empty/loading/banner/validation states.
- `templates/admin/_admin_macros.html`: admin nav rail, regions, panels, disclosures, summary cards, and feed/list shells.
- `templates/_inventory_macros.html`: inventory state helpers, family rows, child rows, singleton rows, detail panels.
- `templates/_item_macros.html`: item identity block and structure badges.

## Shared Primitives

### Surface System

- Use the shared surface tokens in `styles.css` before introducing page-local whites, borders, or shadows.
- Primary app tokens:
  - `--ui-surface-canvas`
  - `--ui-surface-primary`
  - `--ui-surface-secondary`
  - `--ui-surface-quiet`
  - `--ui-border-subtle`
  - `--ui-border-strong`
  - `--ui-shadow-soft`
  - `--ui-shadow-lifted`
- Use the opt-in utilities for legacy wrappers that still need an explicit shell:
  - `.ui-surface-card`
  - `.ui-surface-card-secondary`
  - `.ui-surface-card-quiet`
  - `.ui-surface-card-elevated`
- Do not rely on raw Bootstrap `card` defaults when a surface needs to match the rest of the app.

### Page Header

- Use `ui.page_header(...)` for page-level title, subtitle, back link, primary action, and header meta.
- Best for dashboard, venues, admin, settings, and audit pages.
- Do not hand-build a new title/action shell unless the page clearly cannot fit the shared header.

### Toolbar

- Use `ui.toolbar(...)` with `ui-toolbar-main-*`, `ui-toolbar-split`, and `ui-toolbar-actions`.
- Pair with `ui-control` / `ui-control-sm` for consistent heights.
- Use for search + filter + sort + primary action surfaces.
- Prefer live/debounced filtering for in-page directories when that matches existing app behavior.
- Avoid adding a separate Apply button unless the workflow truly needs an explicit submit.

### Admin Shell

- Use `templates/admin/layout.html` as the admin page shell.
- Use `admin_ui.nav(...)` / `admin_ui.mobile_nav(...)` for admin navigation instead of building page-local subnavs.
- Use `admin_ui.region(...)` and `admin_ui.panel(...)` for admin sections before introducing a new admin card/container pattern.

### Disclosure / Preview-First Sections

- Use `admin_ui.disclosure(...)` for secondary admin data that should stay preview-first.
- Best for retained activity, recent changes, archive lists, rankings, and other long supporting sections.
- Default to showing a concise preview plus counts/meta, then reveal the rest on demand.

### Chips And Status

- Use `ui.chip(...)` for neutral metadata chips.
- Use `ui.status_pill(...)` for venue/item status language.
- Use `inventory_ui.state_badge(...)` and `inventory_ui.coverage_pill(...)` for audit and inventory state.
- Do not introduce new Bootstrap badge patterns when these exist.

### Form Section

- Use `ui.form_section(...)` for admin/settings forms and structured edit blocks.
- Keep labels/help/action rhythm inside the shared section instead of building a new card layout every time.

### State Patterns

- Use `ui.state_banner(...)` for shared info, warning, success, muted, and error banners.
- Use `ui.empty_state(...)` for empty and no-results states.
- Use `ui.loading_state(...)` for async loading affordances when needed.
- Use `ui.validation_summary(...)` for grouped form errors.
- Use `ui.table_empty_row(...)` for empty rows inside tables.

## Inventory Primitives

### Inventory Row Shell

- Use `inventory_ui.desktop_item_row(...)` and `inventory_ui.mobile_item_card(...)` for standalone tracked items.
- These are the source of truth for row alignment, identity, coverage, count/par layout, and detail expansion.

### Family Parent / Child Rows

- Use `inventory_ui.desktop_family_group(...)` and `inventory_ui.mobile_family_card(...)` for grouped inventory.
- Family parents summarize the group.
- Child rows stay inside the family container; do not flatten them into a second custom hierarchy treatment.

### Singleton Asset Rows

- Singleton assets use the same shared row shell as quantity items.
- Swap count/par language for presence/condition language.
- Do not invent fake par/count presentation for singleton assets.

### Detail / Expansion Panels

- Use `inventory_ui.desktop_detail_panel(...)` and `inventory_ui.mobile_detail_panel(...)`.
- These are the canonical venue-level drilldown surfaces for audit details and quick-check links.

## When To Reuse What

- New page shell: `ui.page_header`.
- New filter bar: `ui.toolbar` + `ui-control`.
- New admin page shell or section stack: `admin/layout.html` + `admin/_admin_macros.html`.
- New settings/admin block: `ui.form_section`.
- New metadata/status display: `ui.chip`, `ui.status_pill`, or inventory state helpers.
- New operational inventory surface: `_inventory_macros.html` first, not page-local table/card markup.
- New item label or structure badges: `_item_macros.html`.

## Do Not Reinvent Locally

- Do not create new page-local badge systems when the shared chip/state system already fits.
- Do not create a second admin navigation pattern when the rail/mobile admin shell already fits.
- Do not dump long admin lists fully expanded by default when a disclosure or capped preview will keep the page readable.
- Do not create a second inventory hierarchy style for family rows or singleton assets.
- Do not add one-off spacing or control-height hacks before checking shared tokens/classes in `styles.css`.
- Do not add new near-white background shades, border values, or box shadows before checking the shared surface tokens and utilities.
- Do not rebuild empty/no-results/info banners with raw Bootstrap alerts unless the shared state primitives truly cannot fit.
