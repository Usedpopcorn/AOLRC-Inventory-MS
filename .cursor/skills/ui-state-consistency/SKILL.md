---
name: ui-state-consistency
description: Prevents UI state drift between client and server by enforcing consistency for filters, tabs, search controls, and unsaved edits. Use when implementing or modifying pages with interactive controls, query params, tab navigation, collapses/drawers, or client-side filtering.
---

# UI State Consistency

Use this skill whenever a feature introduces interactive UI state that can diverge from rendered data after refresh, navigation, or back/forward.

Before changing UI behavior, also inspect:

- `AGENTS.md`
- `UI_COMPONENTS.md`
- `RESPONSIVE_UI_RULES.md`
- `UI_REGRESSION_CHECKLIST.md`
- `templates/_ui_macros.html`
- `templates/_inventory_macros.html`
- `static/css/styles.css`

## Goal

Keep user-visible state and data source aligned:

- If data is server-owned, UI state should be URL- or form-driven.
- If data is client-owned, state should persist intentionally (or reset intentionally).
- Avoid hidden unsaved state that disappears on reload without warning.

## Required Workflow

Copy this checklist and complete it for every relevant feature:

```text
UI consistency checklist:
- [ ] Identify all controls that change what the user sees
- [ ] Decide source of truth for each control (server vs client)
- [ ] Ensure refresh behavior is explicit and consistent
- [ ] Ensure back/forward preserves expected state
- [ ] Add unsaved-change indicator if edits are not auto-applied
- [ ] Verify mobile + desktop behavior
- [ ] Reuse shared UI primitives before adding local markup or CSS
- [ ] Run the UI regression checklist if shared layout or operational UI changed
```

## 1) Identify stateful controls

Scan the page for:

- Search inputs
- Filter selects/checkboxes
- Tabs/pills
- Collapse/drawer open state
- Sort selectors
- Client-side row/card visibility toggles

For each control, answer:

- Does it change rendered data?
- Is the change applied immediately or only after submit?
- What happens on refresh?

## 2) Choose source of truth

### Server-owned state (default for list/query behavior)

Use this when controls affect dataset/result semantics.

- Put state in URL query params and/or GET form fields.
- Render controls from server values.
- Render results from same params.
- On reload, controls and results match automatically.

Typical examples:

- `?tab=restocking`
- `?venue_q=...&venue_status=...`
- Restocking filter GET submit

### Client-owned state (only when intentional)

Use this for presentation-only state (for example, temporary typeahead inside a panel).

- Persist with `sessionStorage`/`localStorage` if user expects continuity.
- Or intentionally reset on reload; make that behavior obvious.

## 3) Prevent drift patterns

### Pattern A: Tab drift

If tabs are switched with Bootstrap JS, sync URL on tab change.

- On `shown.bs.tab`, update `?tab=...` with `history.replaceState`.

### Pattern B: Client filter drift

If filtering is done by JS (show/hide rows/cards), mirror control values in URL.

- Read initial values from URL on load.
- Apply those to controls.
- Re-run filtering immediately.
- Update URL on input/change.

### Pattern C: Unsaved selection drift

If controls change selection but server data updates only on submit:

- Show a subtle unsaved indicator (for example, `* filter changes not saved`).
- On refresh/load, restore controls to server-rendered defaults (`defaultChecked` for checkboxes).

## 4) UX requirements

- Keep notices unobtrusive and non-layout-shifting when possible.
- Mobile labels must fit small widths (avoid long toggling copy that overflows).
- Do not rely on hover for key behavior.
- Maintain touch-friendly controls (44px target minimum where practical).
- Reuse shared empty/no-results/error patterns instead of ad hoc alerts when applicable.

## 5) Verification (must run)

For each changed page, test:

1. Change a filter/search/tab and refresh.
2. Confirm visible controls match results after refresh.
3. Use back/forward and confirm state consistency.
4. Repeat in narrow mobile width.
5. If there is an unsaved mode, confirm unsaved hint appears/disappears correctly.
6. If shared rows, headers, toolbars, chips, or non-happy-path states changed, run the checks in `UI_REGRESSION_CHECKLIST.md`.

## Common anti-patterns to reject

- Client-only filter UI with no URL/state restore, while data is server-rendered.
- Tab selection that only exists in DOM classes and not in URL.
- Unsaved local control changes surviving visually after refresh while results are old.
- Silent reset of meaningful user choices without warning.

## Output expectations when applying this skill

When you finish an implementation, explicitly report:

- Source of truth for each changed control
- Refresh/back behavior
- Whether unsaved states exist and how they are communicated
- Mobile consistency checks performed
