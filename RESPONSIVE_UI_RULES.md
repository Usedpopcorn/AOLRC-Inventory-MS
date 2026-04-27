# Responsive UI Rules

Use these rules when changing or adding UI.

## Validation Widths

- `375`
- `768`
- `973`
- `1199`
- `1440`

## Shared Rule

- Keep the same component language across breakpoints.
- Change layout structure when needed, not semantics.
- Operational workflows should not depend on horizontal scrolling on small screens.

## Page Headers

- Below `768px`, shared headers stack vertically.
- At `768px+`, back link, title, and primary action can align into the shared two-column header shell.
- Header meta chips should wrap instead of forcing overflow.

## Toolbars

- Below `768px`, toolbars stack into one column and action buttons should fill available width when appropriate.
- At `768px+`, use shared multi-column toolbar grids (`ui-toolbar-main-2` / `ui-toolbar-main-3`).
- Venue directory keeps the filter row multi-column at tablet widths, then splits filter row and action area at `992px+`.
- Live-search/filter toolbars should stay usable without depending on a separate Apply button.

## Admin Shell

- Admin uses the shared sidebar rail at `lg+` and the compact mobile admin nav below that.
- The sidebar should behave like navigation, not a second content column: lighter than the main workspace, always reachable, and non-competing.
- Do not reintroduce the old horizontal admin subnav as the primary navigation pattern.

## Admin Lists And Directories

- Preview-first admin sections should stay compact at first paint: summary counts first, fuller lists behind disclosures or paging.
- Users stays in card/list mode longer and should not switch to a wide table until the width is genuinely comfortable.
- Item catalog may use the desktop table at standard laptop widths, but only when columns remain stable at normal browser zoom.

## Inventory Rows

- Supplies keeps card/mobile inventory presentation below `1200px`, then switches to the desktop audit table at `1200px+`.
- Quick Check stays card/stack-first on small screens and uses the shared desktop row/header treatment from `768px+`.
- Parent and child rows must preserve the same hierarchy meaning at every width.

## Family / Group Rows

- Family rows remain summary-first across breakpoints.
- Child rows stay nested under the family parent; mobile may stack them, but the relationship should remain obvious.
- Do not flatten grouped inventory into unrelated standalone cards on small screens.

## Singleton Asset Rows

- Singleton assets keep the same row shell as quantity items.
- On mobile and desktop alike, singleton rows should present presence/condition rather than par-style quantity language.

## Detail Panels

- Desktop detail panels expand inline beneath the row.
- Mobile detail panels expand inside the card stack.
- Expansion affordances must keep keyboard focus visibility and maintain `aria-expanded` / `aria-controls`.

## Tables Vs Cards

- Admin/secondary tables may remain tables when usability stays acceptable.
- Operational inventory pages should prefer stacked cards on smaller widths instead of forcing horizontal scroll.
- If a table must stay on smaller widths, wrap it in a responsive container and verify important controls remain reachable.
- For large directories, combine responsive table/card switching with paging or capped previews so long datasets do not flood the page.

## Non-Happy-Path States

- Empty and no-results states should use the shared empty-state pattern.
- Info/warning/error messaging should use the shared state-banner pattern.
- Loading affordances should use the shared loading-state pattern when async behavior is introduced.

## Final Check

- No clipped controls.
- No one-word-per-line wrapping for major titles or row labels.
- No toolbar drift at intermediate widths.
- No operational action dependent on hover-only behavior.
- No admin destination hidden or undiscoverable at mobile widths.
- No activity/history region that overwhelms the page before the user opts in to more detail.
