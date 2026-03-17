---
name: brand-colors
description: Enforces AOLRC brand color palette (purple, gold) in styles and UI. Use when editing CSS, templates, adding or changing colors, or when the user mentions brand, palette, or theme colors.
---

# AOLRC Brand Colors

## Palette

| Role   | Hex       | Use |
|--------|-----------|-----|
| Purple | `#84608C` | Primary brand (headers, links, accents) |
| Gold   | `#F8AD26` | Secondary brand (buttons, highlights) |
| Gold highlight | `#FFDA94` | Optional: hover states, gradients, lighter gold |

**Status/feedback:** Use Bootstrap semantic colors only (e.g. success, danger, warning, info). Do not introduce new palette colors for statuses.

## Rules

1. **Stick to the palette.** Use only the colors above (and Bootstrap semantic colors for status/feedback) unless the user explicitly asks for something else.
2. **No new palette colors without asking.** Do not add new hex or named colors outside this palette + Bootstrap semantics. If a design needs another color, ask the user first.
3. **Optional gold highlight.** Prefer `#FFDA94` for hover, gradients, or a lighter gold when it improves contrast or readability.

## Quick reference (CSS)

```css
:root {
  --brand-purple: #84608C;
  --brand-gold: #F8AD26;
  --brand-gold-highlight: #FFDA94;
}
```

Use these variables (or the hex values) in styles; avoid introducing additional brand-like colors.
