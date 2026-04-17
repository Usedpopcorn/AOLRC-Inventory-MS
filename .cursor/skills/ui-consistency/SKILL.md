---
name: ui-consistency
description: Use this when changing AOLRC UI structure, shared macros, responsive behavior, or state/empty patterns. It keeps work anchored to the shared UI system instead of page-local drift.
---

# UI Consistency

Use this skill whenever a task changes templates, shared CSS, operational inventory UI, or responsive behavior.

## Read First

- `AGENTS.md`
- `UI_COMPONENTS.md`
- `RESPONSIVE_UI_RULES.md`
- `UI_REGRESSION_CHECKLIST.md`

## Shared Source Of Truth

- `templates/_ui_macros.html`
- `templates/_inventory_macros.html`
- `templates/_item_macros.html`
- `static/css/styles.css`

## Required Workflow

```text
UI consistency workflow:
- [ ] Inspect shared macros and styles before editing
- [ ] Reuse shared primitives before adding page-local markup/CSS
- [ ] Preserve family/child/singleton inventory rules when inventory UI changes
- [ ] Use shared empty/loading/error/info state patterns
- [ ] Validate at 375, 768, 973, 1199, and 1440
- [ ] Run git diff --check, Jinja compile, and app boot smoke
```

## Special Inventory Rules

- Family rows summarize; child rows stay nested.
- Singleton assets share the row shell but not the quantity/par language.
- Detail panels should come from the shared inventory layer.

## Reject

- New badge/card/header systems when shared ones exist
- One-off spacing/alignment hacks before checking shared tokens/classes
- Operational small-screen layouts that depend on horizontal scrolling
