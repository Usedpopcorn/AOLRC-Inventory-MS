# UI Agent Prompt Snippet

Use this when assigning UI work to a coding agent:

```md
Inspect the shared UI source of truth first:
- templates/_ui_macros.html
- templates/_inventory_macros.html
- templates/_item_macros.html
- static/css/styles.css
- UI_COMPONENTS.md
- RESPONSIVE_UI_RULES.md
- UI_REGRESSION_CHECKLIST.md

Reuse shared macros, classes, and tokens before creating page-local markup or CSS.
Preserve inventory row alignment, parent/child hierarchy, and singleton asset presentation.
Follow the current responsive rules and validate at 375, 768, 973, 1199, and 1440 widths.
Use the shared state patterns for empty/no-results/info/warning/error/loading states.
Avoid one-off spacing hacks and avoid inventing new badge/card/header patterns when shared ones already exist.
Run git diff --check, a Jinja compile pass, and an app boot smoke test after UI changes.
```
