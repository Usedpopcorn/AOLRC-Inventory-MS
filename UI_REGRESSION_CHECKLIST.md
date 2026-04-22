# UI Regression Checklist

Use this checklist for any UI-affecting change.

## Pages To Check

- Dashboard
- Supplies
- Quick Check
- Venues list
- Venue detail
- Venue settings
- Venue supplies
- Admin overview
- Admin users
- Admin audit
- Admin history
- Admin items
- Admin add/edit item flows

## Widths To Check

- `375`
- `768`
- `973`
- `1199`
- `1440`

## What To Inspect

- Header consistency: shared title/back/action shell still reads like the rest of the app.
- Admin shell: sidebar rail remains sticky/reachable, active state is stable, mobile admin nav is discoverable.
- Toolbar sizing and wrapping: shared control heights, aligned gaps, no awkward jump points.
- Live filters: instant search/filter surfaces update cleanly, preserve URL/state, and do not require a redundant Apply step.
- Row alignment: count/status/action columns stay aligned where desktop row shells are used.
- Parent/child hierarchy: family summaries, child rows, and detail expansions still read as one system.
- Singleton presentation: presence/condition language remains distinct from quantity/par language.
- Chip clutter: no redundant chips or new Bootstrap badge drift.
- Copy discipline: no repeated helper text, “snapshot” filler, or explanatory copy that the surrounding UI already makes clear.
- Feed density: activity/history sections read like feeds or compact lists, not stacks of identical full cards.
- Preview-first behavior: disclosures, capped previews, and paging keep long admin sections from flooding the page.
- Empty/loading/error states: shared state components used, copy still clear, no broken spacing.
- Overflow and horizontal scroll: operational pages should not rely on sideways scrolling on small screens.
- Touch/focus behavior: focus rings visible, tap targets usable, no hover-only critical behavior.

## Pre-Merge UI Checklist

- Reused shared macros/classes/tokens before creating local markup or CSS.
- Checked `templates/_ui_macros.html`, `templates/_inventory_macros.html`, and `templates/_item_macros.html` first.
- Verified responsive behavior at the five required widths.
- Ran `git diff --check`.
- Ran a Jinja compile pass.
- Ran an app boot smoke test.

## If The Change Touches Operational Inventory

- Verify Supplies and Quick Check.
- Verify family rows, child rows, singleton rows, and detail panels.
- Verify no alignment drift was introduced between desktop and mobile variants.
