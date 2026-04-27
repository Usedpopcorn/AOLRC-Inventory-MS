from __future__ import annotations

import re
from dataclasses import dataclass

from app import db
from app.models import Item
from app.services.inventory_status import suggest_status_from_count

CUSTOM_SETUP_GROUP_OPTION_VALUE = "__custom__"
MAX_SETUP_GROUP_CODE_LENGTH = 32
MAX_SETUP_GROUP_LABEL_LENGTH = 120
SETUP_GROUP_CODE_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9 .&()/_-]{0,31}$")
BUILTIN_SETUP_GROUPS = (
    ("01", "Yoga/Meditation Materials"),
    ("02", "Standard Signature Set-up Needs"),
    ("03", "Silence Course Needs"),
    ("04", "Teachers Set-up Needs"),
    ("05", "Misc."),
)
BUILTIN_SETUP_GROUP_LABELS = {
    code: label
    for code, label in BUILTIN_SETUP_GROUPS
}


class SpreadsheetCompatibilityError(ValueError):
    """Raised when setup-group input cannot be normalized safely."""


@dataclass(frozen=True)
class ReorderDecision:
    state: str
    is_comparable: bool
    effective_par: int | None
    raw_count: int | None
    suggested_order_qty: int | None
    over_par_qty: int | None
    severity_key: str | None


def normalize_setup_group_code(raw_value):
    value = " ".join((raw_value or "").strip().split()).upper()
    if not value:
        return None
    if len(value) > MAX_SETUP_GROUP_CODE_LENGTH:
        raise SpreadsheetCompatibilityError(
            f"Setup group code must be {MAX_SETUP_GROUP_CODE_LENGTH} characters or fewer."
        )
    if not SETUP_GROUP_CODE_PATTERN.match(value):
        raise SpreadsheetCompatibilityError(
            "Setup group code can only use letters, numbers, spaces, and . & ( ) / _ - characters."
        )
    return value


def normalize_setup_group_label(raw_value):
    value = " ".join((raw_value or "").strip().split())
    if not value:
        return None
    if len(value) > MAX_SETUP_GROUP_LABEL_LENGTH:
        raise SpreadsheetCompatibilityError(
            f"Setup group label must be {MAX_SETUP_GROUP_LABEL_LENGTH} characters or fewer."
        )
    return value


def format_setup_group_display(code=None, label=None):
    normalized_code = normalize_setup_group_code(code) if code else None
    normalized_label = normalize_setup_group_label(label) if label else None
    if normalized_code and normalized_label:
        return f"{normalized_code} - {normalized_label}"
    return normalized_code or normalized_label


def build_builtin_setup_group_options():
    return [
        {
            "code": code,
            "label": label,
            "display": format_setup_group_display(code, label),
            "is_builtin": True,
        }
        for code, label in BUILTIN_SETUP_GROUPS
    ]


def get_distinct_setup_groups():
    rows = (
        db.session.query(Item.setup_group_code, Item.setup_group_label)
        .filter(Item.setup_group_code.is_not(None))
        .order_by(Item.setup_group_code.asc(), Item.setup_group_label.asc())
        .all()
    )
    groups_by_code = {}
    for row in rows:
        code = normalize_setup_group_code(row.setup_group_code)
        label = normalize_setup_group_label(row.setup_group_label)
        if not code:
            continue
        if code in BUILTIN_SETUP_GROUP_LABELS:
            groups_by_code[code] = BUILTIN_SETUP_GROUP_LABELS[code]
            continue
        if label and code not in groups_by_code:
            groups_by_code[code] = label

    merged_groups = []
    for builtin in build_builtin_setup_group_options():
        merged_groups.append(builtin)

    for code, label in sorted(groups_by_code.items()):
        if code in BUILTIN_SETUP_GROUP_LABELS:
            continue
        merged_groups.append(
            {
                "code": code,
                "label": label,
                "display": format_setup_group_display(code, label),
                "is_builtin": False,
            }
        )
    return merged_groups


def _build_setup_group_registry():
    groups_by_code = {}
    groups_by_label = {}
    for group in get_distinct_setup_groups():
        code = normalize_setup_group_code(group.get("code")) if group.get("code") else None
        label = normalize_setup_group_label(group.get("label")) if group.get("label") else None
        if code:
            groups_by_code[code] = label
        if label:
            groups_by_label[label.casefold()] = {
                "code": code,
                "label": label,
            }
    return groups_by_code, groups_by_label


def resolve_setup_group_selection(
    *,
    selection_value,
    custom_code=None,
    custom_label=None,
):
    selection = (selection_value or "").strip()
    if not selection:
        return None, None

    if selection == CUSTOM_SETUP_GROUP_OPTION_VALUE:
        normalized_code = normalize_setup_group_code(custom_code)
        normalized_label = normalize_setup_group_label(custom_label)
        if not normalized_code:
            raise SpreadsheetCompatibilityError("Custom setup group code is required.")
        if not normalized_label:
            raise SpreadsheetCompatibilityError("Custom setup group label is required.")

        existing_groups_by_code, existing_groups_by_label = _build_setup_group_registry()

        existing_label = existing_groups_by_code.get(normalized_code)
        if existing_label:
            existing_display = format_setup_group_display(normalized_code, existing_label)
            raise SpreadsheetCompatibilityError(
                f'That setup group code already exists as "{existing_display}". '
                "Select the existing setup group from the list instead."
            )

        existing_group_for_label = existing_groups_by_label.get(normalized_label.casefold())
        if existing_group_for_label:
            existing_display = format_setup_group_display(
                existing_group_for_label["code"],
                existing_group_for_label["label"],
            )
            raise SpreadsheetCompatibilityError(
                f'That setup group label already exists as "{existing_display}". '
                "Select the existing setup group from the list instead."
            )
        return normalized_code, normalized_label

    normalized_code = normalize_setup_group_code(selection)
    if not normalized_code:
        return None, None
    if normalized_code in BUILTIN_SETUP_GROUP_LABELS:
        return normalized_code, BUILTIN_SETUP_GROUP_LABELS[normalized_code]

    existing_groups = {
        group["code"]: group["label"]
        for group in get_distinct_setup_groups()
        if group["code"] not in BUILTIN_SETUP_GROUP_LABELS
    }
    existing_label = existing_groups.get(normalized_code)
    if not existing_label:
        raise SpreadsheetCompatibilityError("Selected setup group is no longer available.")
    return normalized_code, existing_label


def build_reorder_decision(*, tracking_mode, raw_count, effective_par):
    if tracking_mode == "singleton_asset":
        return ReorderDecision(
            state="singleton_asset",
            is_comparable=False,
            effective_par=None,
            raw_count=raw_count,
            suggested_order_qty=None,
            over_par_qty=None,
            severity_key=None,
        )

    if raw_count is None:
        return ReorderDecision(
            state="no_count",
            is_comparable=False,
            effective_par=effective_par,
            raw_count=None,
            suggested_order_qty=None,
            over_par_qty=None,
            severity_key=None,
        )

    if effective_par is None or effective_par <= 0:
        return ReorderDecision(
            state="no_par",
            is_comparable=False,
            effective_par=effective_par,
            raw_count=raw_count,
            suggested_order_qty=None,
            over_par_qty=None,
            severity_key=None,
        )

    suggested_order_qty = max(int(effective_par) - int(raw_count), 0)
    over_par_qty = max(int(raw_count) - int(effective_par), 0)
    severity_key = suggest_status_from_count(raw_count, effective_par, tracking_mode) or "good"
    return ReorderDecision(
        state="comparable",
        is_comparable=True,
        effective_par=int(effective_par),
        raw_count=int(raw_count),
        suggested_order_qty=suggested_order_qty,
        over_par_qty=over_par_qty,
        severity_key=severity_key,
    )
