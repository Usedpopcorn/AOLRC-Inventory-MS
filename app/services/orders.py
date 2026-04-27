from app import db
from app.models import (
    ORDER_BATCH_TYPES,
    ORDER_LINE_STATUSES,
    Item,
    OrderBatch,
    OrderLine,
    Venue,
    VenueItem,
    normalize_order_batch_type,
    normalize_order_line_status,
)
from app.services.csv_exports import build_dated_csv_filename, sanitize_csv_cell
from app.services.inventory_rules import InventoryRuleError, normalize_optional_tracking_value
from app.services.restocking import build_restock_rows
from app.services.spreadsheet_compat import (
    BUILTIN_SETUP_GROUP_LABELS,
    format_setup_group_display,
    normalize_setup_group_code,
    normalize_setup_group_label,
)

ORDER_BATCH_TYPE_LABELS = {
    "monthly": "Monthly",
    "quarterly": "Quarterly",
    "ad_hoc": "Ad Hoc",
}
ORDER_LINE_STATUS_LABELS = {
    "planned": "Planned",
    "ordered": "Ordered",
    "received": "Received",
    "skipped": "Skipped",
}
ORDER_SCOPE_UNASSIGNED_VALUE = "__unassigned__"
ORDER_BULK_ACTION_SET_STATUS = "set_status"
ORDER_LINE_EXPORT_HEADERS = (
    "Batch Name",
    "Batch Type",
    "Batch Created Date",
    "Batch Created By",
    "Item Name",
    "Venue Name",
    "Setup Group Code",
    "Setup Group Label",
    "Count Snapshot",
    "Par Snapshot",
    "Suggested Order Qty",
    "Over Par Qty",
    "Actual Ordered Qty",
    "Status",
    "Line Notes",
)
ORDER_BATCH_EXPORT_HEADERS = ORDER_LINE_EXPORT_HEADERS
ORDER_PURCHASE_SUMMARY_EXPORT_HEADERS = (
    "Batch Name",
    "Batch Type",
    "Item Name",
    "Setup Group Code",
    "Setup Group Label",
    "Contributing Venue Count",
    "Contributing Line Count",
    "Total Count",
    "Total Par",
    "Total Suggested Order",
    "Total Actual Ordered",
    "Total Over Par",
    "Status Summary",
    "Note Count",
)


def _coerce_int(value):
    return int(value or 0)


def _format_compact_count(value, singular, plural=None):
    resolved_plural = plural or f"{singular}s"
    label = singular if value == 1 else resolved_plural
    return f"{value:,} {label}"


def format_order_batch_type_label(batch_type):
    normalized = normalize_order_batch_type(batch_type)
    return ORDER_BATCH_TYPE_LABELS.get(normalized, ORDER_BATCH_TYPE_LABELS["monthly"])


def format_order_line_status_label(status):
    normalized = normalize_order_line_status(status)
    return ORDER_LINE_STATUS_LABELS.get(normalized, ORDER_LINE_STATUS_LABELS["planned"])


def format_user_label(user):
    if user is None:
        return "System"
    display_name = (getattr(user, "display_name", "") or "").strip()
    if display_name:
        return display_name
    return getattr(user, "email", "") or "Unknown user"


def normalize_optional_notes(raw_value):
    notes = (raw_value or "").strip()
    return notes or None


def format_order_snapshot_setup_group_display(code=None, label=None):
    try:
        return format_setup_group_display(code, label)
    except ValueError:
        raw_code = " ".join((code or "").strip().split()) or None
        raw_label = " ".join((label or "").strip().split()) or None
        if raw_code and raw_label:
            return f"{raw_code} - {raw_label}"
        return raw_code or raw_label


def validate_order_batch_type_value(raw_value):
    batch_type = (raw_value or "").strip()
    if batch_type not in ORDER_BATCH_TYPES:
        raise InventoryRuleError("Batch type selection is invalid.")
    return batch_type


def validate_order_line_status_value(raw_value):
    status = (raw_value or "").strip()
    if status not in ORDER_LINE_STATUSES:
        raise InventoryRuleError("Order line status selection is invalid.")
    return status


def validate_order_bulk_action_value(raw_value):
    action = (raw_value or "").strip()
    if action != ORDER_BULK_ACTION_SET_STATUS:
        raise InventoryRuleError("Bulk action selection is invalid.")
    return action


def parse_order_line_ids(raw_values):
    line_ids = []
    for raw_value in raw_values or []:
        value = (raw_value or "").strip()
        if not value:
            continue
        if not value.isdigit():
            raise InventoryRuleError("Selected order lines are invalid.")
        line_id = int(value)
        if line_id not in line_ids:
            line_ids.append(line_id)
    if not line_ids:
        raise InventoryRuleError("Select at least one order line.")
    return line_ids


def _normalize_scope_group_value(code, label):
    try:
        normalized_code = normalize_setup_group_code(code) if code else None
        normalized_label = normalize_setup_group_label(label) if label else None
    except ValueError:
        return None

    if normalized_code in BUILTIN_SETUP_GROUP_LABELS:
        normalized_label = BUILTIN_SETUP_GROUP_LABELS[normalized_code]
    if not normalized_code:
        return None
    return {
        "value": normalized_code,
        "code": normalized_code,
        "label": normalized_label,
        "display": format_setup_group_display(normalized_code, normalized_label),
    }


def build_order_scope_options():
    rows = (
        db.session.query(
            Venue.id.label("venue_id"),
            Venue.name.label("venue_name"),
            Item.setup_group_code.label("setup_group_code"),
            Item.setup_group_label.label("setup_group_label"),
        )
        .select_from(VenueItem)
        .join(Venue, Venue.id == VenueItem.venue_id)
        .join(Item, Item.id == VenueItem.item_id)
        .filter(
            VenueItem.active.is_(True),
            Venue.active.is_(True),
            Item.active.is_(True),
            Item.is_group_parent.is_(False),
        )
        .order_by(Venue.name.asc(), Item.setup_group_code.asc(), Item.setup_group_label.asc())
        .all()
    )

    venues_by_id = {}
    setup_groups_by_value = {}
    has_unassigned_group = False
    for row in rows:
        venues_by_id.setdefault(row.venue_id, row.venue_name)
        option = _normalize_scope_group_value(row.setup_group_code, row.setup_group_label)
        if option is None:
            has_unassigned_group = True
            continue
        setup_groups_by_value.setdefault(option["value"], option)

    venues = [
        {"id": venue_id, "name": venue_name}
        for venue_id, venue_name in sorted(
            venues_by_id.items(),
            key=lambda item: item[1].lower(),
        )
    ]
    setup_groups = sorted(
        setup_groups_by_value.values(),
        key=lambda option: option["display"].lower(),
    )
    if has_unassigned_group:
        setup_groups.append(
            {
                "value": ORDER_SCOPE_UNASSIGNED_VALUE,
                "code": None,
                "label": None,
                "display": "Unassigned",
            }
        )

    return {
        "venues": venues,
        "setup_groups": setup_groups,
        "valid_venue_ids": {row["id"] for row in venues},
        "valid_setup_group_values": {row["value"] for row in setup_groups},
    }


def validate_order_scope_venue_id(raw_value, *, valid_venue_ids):
    value = (raw_value or "").strip()
    if not value:
        return None
    if not value.isdigit():
        raise InventoryRuleError("Venue scope selection is invalid.")
    venue_id = int(value)
    if venue_id not in valid_venue_ids:
        raise InventoryRuleError("Venue scope selection is invalid.")
    return venue_id


def validate_order_scope_setup_group(raw_value, *, valid_setup_group_values):
    value = (raw_value or "").strip()
    if not value:
        return None
    if value not in valid_setup_group_values:
        raise InventoryRuleError("Setup group scope selection is invalid.")
    return value


def is_actionable_restock_row(row):
    count_state = row.get("count_state") or {}
    if not count_state.get("is_comparable"):
        return False
    suggested_order_qty = int(count_state.get("suggested_order_qty") or 0)
    over_par_qty = int(count_state.get("over_par_qty") or 0)
    return suggested_order_qty > 0 or over_par_qty > 0


def _row_matches_setup_group_scope(row, scope_setup_group):
    if scope_setup_group is None:
        return True
    row_code = row.get("setup_group_code")
    if scope_setup_group == ORDER_SCOPE_UNASSIGNED_VALUE:
        return not row_code
    return row_code == scope_setup_group


def build_order_seed_rows(*, scope_venue_id=None, scope_setup_group=None):
    venue_ids = [scope_venue_id] if scope_venue_id is not None else None
    live_rows = build_restock_rows(
        venue_ids=venue_ids,
        mode="counts",
        sort="status_priority",
        limit=None,
        offset=0,
    )["rows"]
    return [
        row
        for row in live_rows
        if _row_matches_setup_group_scope(row, scope_setup_group) and is_actionable_restock_row(row)
    ]


def create_order_batch(
    *,
    name,
    batch_type,
    notes,
    created_by_user_id,
    scope_venue_id=None,
    scope_setup_group=None,
):
    normalized_name = (name or "").strip()
    if not normalized_name:
        raise InventoryRuleError("Batch name is required.")

    batch = OrderBatch(
        name=normalized_name,
        batch_type=validate_order_batch_type_value(batch_type),
        notes=normalize_optional_notes(notes),
        created_by_user_id=created_by_user_id,
    )
    db.session.add(batch)
    db.session.flush()

    seed_rows = build_order_seed_rows(
        scope_venue_id=scope_venue_id,
        scope_setup_group=scope_setup_group,
    )
    for row in seed_rows:
        db.session.add(build_order_line_from_restock_row(batch.id, row))

    db.session.flush()
    return batch


def build_order_line_from_restock_row(batch_id, row):
    count_state = row.get("count_state") or {}
    return OrderLine(
        order_batch_id=batch_id,
        item_id=row.get("item_id"),
        venue_id=row.get("venue_id"),
        item_name_snapshot=row.get("item_name") or "Unnamed item",
        venue_name_snapshot=row.get("venue_name") or "Unnamed venue",
        setup_group_code_snapshot=row.get("setup_group_code"),
        setup_group_label_snapshot=row.get("setup_group_label"),
        count_snapshot=count_state.get("raw_count"),
        par_snapshot=count_state.get("par_value"),
        suggested_order_qty_snapshot=int(count_state.get("suggested_order_qty") or 0),
        over_par_qty_snapshot=int(count_state.get("over_par_qty") or 0),
        status="planned",
    )


def update_order_batch_notes(batch, *, notes):
    batch.notes = normalize_optional_notes(notes)
    return batch


def update_order_line(*, line, actual_ordered_qty_raw, status_raw, notes):
    actual_ordered_qty = normalize_optional_tracking_value(
        actual_ordered_qty_raw,
        field_label="Actual ordered quantity",
    )
    status = validate_order_line_status_value(status_raw)
    line.actual_ordered_qty = actual_ordered_qty
    line.status = status
    line.notes = normalize_optional_notes(notes)
    return line


def bulk_update_order_lines_status(*, batch_id, raw_line_ids, status_raw):
    line_ids = parse_order_line_ids(raw_line_ids)
    status = validate_order_line_status_value(status_raw)
    lines = (
        OrderLine.query.filter(
            OrderLine.order_batch_id == batch_id,
            OrderLine.id.in_(line_ids),
        )
        .order_by(OrderLine.id.asc())
        .all()
    )
    if len(lines) != len(line_ids):
        raise InventoryRuleError("Selected order lines are invalid.")
    for line in lines:
        line.status = status
    return lines


def _blank_status_counts():
    return {status: 0 for status in ORDER_LINE_STATUSES}


def _build_status_count_items(status_counts):
    return [
        {
            "key": status,
            "label": ORDER_LINE_STATUS_LABELS[status],
            "count": count,
        }
        for status, count in status_counts.items()
        if count
    ]


def _build_status_breakdown_text(status_counts):
    return " · ".join(
        f"{count} {ORDER_LINE_STATUS_LABELS[status]}"
        for status, count in status_counts.items()
        if count
    )


def _resolve_rollup_status(status_counts):
    active_statuses = [
        status
        for status, count in status_counts.items()
        if count
    ]
    if len(active_statuses) == 1:
        status_key = active_statuses[0]
        return status_key, ORDER_LINE_STATUS_LABELS[status_key]
    if active_statuses:
        return "mixed", "Mixed"
    return "planned", ORDER_LINE_STATUS_LABELS["planned"]


def build_order_batch_summary(batch):
    lines = list(batch.lines or [])
    status_counts = _blank_status_counts()
    total_suggested_qty = 0
    total_over_par_qty = 0
    total_actual_ordered_qty = 0
    for line in lines:
        normalized_status = normalize_order_line_status(line.status)
        status_counts[normalized_status] += 1
        total_suggested_qty += int(line.suggested_order_qty_snapshot or 0)
        total_over_par_qty += int(line.over_par_qty_snapshot or 0)
        total_actual_ordered_qty += int(line.actual_ordered_qty or 0)

    return {
        "line_count": len(lines),
        "total_suggested_qty": total_suggested_qty,
        "total_over_par_qty": total_over_par_qty,
        "total_actual_ordered_qty": total_actual_ordered_qty,
        "status_counts": status_counts,
        "status_count_items": _build_status_count_items(status_counts),
    }


def build_purchase_summary_rows(line_rows):
    rows_by_key = {}
    for line in line_rows:
        item_id = line.get("item_id")
        item_name = line.get("item_name") or "Unnamed item"
        setup_group_code = line.get("setup_group_code")
        setup_group_label = line.get("setup_group_label")
        setup_group_display = line.get("setup_group_display") or "Unassigned"
        group_key = (
            ("item", item_id)
            if item_id is not None
            else (
                "snapshot",
                item_name,
                setup_group_code or "",
                setup_group_label or "",
            )
        )
        summary = rows_by_key.setdefault(
            group_key,
            {
                "group_key": group_key,
                "item_id": item_id,
                "item_name": item_name,
                "setup_group_code": setup_group_code,
                "setup_group_label": setup_group_label,
                "setup_group_display": setup_group_display,
                "line_count": 0,
                "venue_count": 0,
                "total_count_snapshot": 0,
                "total_par_snapshot": 0,
                "total_suggested_order_qty": 0,
                "total_actual_ordered_qty": 0,
                "total_over_par_qty": 0,
                "note_count": 0,
                "status_counts": _blank_status_counts(),
                "contributing_lines": [],
                "_venue_identities": set(),
            },
        )
        summary["line_count"] += 1
        summary["total_count_snapshot"] += _coerce_int(line.get("count_snapshot"))
        summary["total_par_snapshot"] += _coerce_int(line.get("par_snapshot"))
        summary["total_suggested_order_qty"] += _coerce_int(
            line.get("suggested_order_qty_snapshot")
        )
        summary["total_actual_ordered_qty"] += _coerce_int(line.get("actual_ordered_qty"))
        summary["total_over_par_qty"] += _coerce_int(line.get("over_par_qty_snapshot"))
        if normalize_optional_notes(line.get("notes")):
            summary["note_count"] += 1

        venue_identity = line.get("venue_id")
        if venue_identity is None:
            venue_identity = line.get("venue_name") or ""
        summary["_venue_identities"].add(venue_identity)

        status_key = normalize_order_line_status(line.get("status"))
        summary["status_counts"][status_key] += 1
        summary["contributing_lines"].append(line)

    rows = list(rows_by_key.values())
    for row in rows:
        row["venue_count"] = len(row.pop("_venue_identities"))
        row["status_count_items"] = _build_status_count_items(row["status_counts"])
        row["status_breakdown_text"] = _build_status_breakdown_text(row["status_counts"])
        row["rollup_status_key"], row["rollup_status_label"] = _resolve_rollup_status(
            row["status_counts"]
        )
        row["note_summary_text"] = (
            _format_compact_count(row["note_count"], "venue note")
            if row["note_count"]
            else "No venue notes"
        )
        row["venue_count_text"] = _format_compact_count(row["venue_count"], "venue")
        row["contributing_lines"].sort(
            key=lambda line: (
                (line.get("venue_name") or "").lower(),
                int(line.get("id") or 0),
            )
        )
        row["search_text"] = " ".join(
            [
                row["item_name"],
                row["setup_group_display"],
                row["status_breakdown_text"],
                row["note_summary_text"],
                " ".join(line.get("venue_name") or "" for line in row["contributing_lines"]),
            ]
        ).lower()

    rows.sort(
        key=lambda row: (
            -row["total_suggested_order_qty"],
            row["item_name"].lower(),
            row["setup_group_display"].lower(),
        )
    )
    return rows


def filter_purchase_summary_rows(purchase_summary_rows, search_query):
    normalized_query = (search_query or "").strip().lower()
    if not normalized_query:
        return purchase_summary_rows
    return [
        row
        for row in purchase_summary_rows
        if normalized_query in (row.get("search_text") or "")
    ]


def build_purchase_summary_support_text(purchase_summary_rows):
    item_count = len(purchase_summary_rows)
    if item_count == 0:
        return "No items in the current filtered view."

    total_suggested_qty = sum(
        _coerce_int(row.get("total_suggested_order_qty"))
        for row in purchase_summary_rows
    )
    total_over_par_qty = sum(
        _coerce_int(row.get("total_over_par_qty"))
        for row in purchase_summary_rows
    )
    return (
        f"{_format_compact_count(item_count, 'item')} · "
        f"{total_suggested_qty:,} suggested · "
        f"{total_over_par_qty:,} over par"
    )


def build_grouped_order_line_summaries(line_rows, *, group_by):
    if group_by not in {"setup_group", "venue"}:
        raise ValueError("group_by must be 'setup_group' or 'venue'.")

    summaries_by_key = {}
    for row in line_rows:
        if group_by == "setup_group":
            group_key = row.get("setup_group_display") or "Unassigned"
        else:
            group_key = row.get("venue_name") or "Unknown venue"

        summary = summaries_by_key.setdefault(
            group_key,
            {
                "group_key": group_key,
                "label": group_key,
                "line_count": 0,
                "total_suggested_qty": 0,
                "total_actual_ordered_qty": 0,
                "over_par_line_count": 0,
                "status_counts": _blank_status_counts(),
            },
        )
        summary["line_count"] += 1
        summary["total_suggested_qty"] += int(row.get("suggested_order_qty_snapshot") or 0)
        summary["total_actual_ordered_qty"] += int(row.get("actual_ordered_qty") or 0)
        if int(row.get("over_par_qty_snapshot") or 0) > 0:
            summary["over_par_line_count"] += 1

        status_key = normalize_order_line_status(row.get("status"))
        summary["status_counts"][status_key] += 1

    rows = list(summaries_by_key.values())
    for row in rows:
        row["status_count_items"] = _build_status_count_items(row["status_counts"])

    rows.sort(
        key=lambda row: (
            -row["total_suggested_qty"],
            row["label"].lower(),
        )
    )
    return rows


def build_grouped_summary_support_text(summary_rows, *, singular_label, plural_label):
    count = len(summary_rows)
    if count == 0:
        return f"No {plural_label} in the current filtered view."

    total_suggested_qty = sum(
        _coerce_int(row.get("total_suggested_qty"))
        for row in summary_rows
    )
    total_over_par_lines = sum(
        _coerce_int(row.get("over_par_line_count"))
        for row in summary_rows
    )
    scope_text = _format_compact_count(count, singular_label, plural_label)
    over_par_text = _format_compact_count(
        total_over_par_lines,
        "over-par line",
    )
    return f"{scope_text} · {total_suggested_qty:,} suggested · {over_par_text}"


def format_order_export_timestamp(value):
    if value is None:
        return ""
    return value.strftime("%Y-%m-%d")


def build_order_line_csv_rows(batch, line_rows):
    batch_created_at = format_order_export_timestamp(batch.created_at)
    batch_type_label = format_order_batch_type_label(batch.batch_type)
    batch_created_by = format_user_label(batch.created_by)
    rows = []
    for line in line_rows:
        rows.append(
            {
                "Batch Name": sanitize_csv_cell(batch.name),
                "Batch Type": sanitize_csv_cell(batch_type_label),
                "Batch Created Date": batch_created_at,
                "Batch Created By": sanitize_csv_cell(batch_created_by),
                "Item Name": sanitize_csv_cell(line.get("item_name") or ""),
                "Venue Name": sanitize_csv_cell(line.get("venue_name") or ""),
                "Setup Group Code": sanitize_csv_cell(line.get("setup_group_code") or ""),
                "Setup Group Label": sanitize_csv_cell(line.get("setup_group_label") or ""),
                "Count Snapshot": (
                    "" if line.get("count_snapshot") is None else line.get("count_snapshot")
                ),
                "Par Snapshot": (
                    "" if line.get("par_snapshot") is None else line.get("par_snapshot")
                ),
                "Suggested Order Qty": int(line.get("suggested_order_qty_snapshot") or 0),
                "Over Par Qty": int(line.get("over_par_qty_snapshot") or 0),
                "Actual Ordered Qty": (
                    ""
                    if line.get("actual_ordered_qty") is None
                    else int(line.get("actual_ordered_qty") or 0)
                ),
                "Status": (
                    sanitize_csv_cell(line.get("status_label"))
                    or sanitize_csv_cell(format_order_line_status_label(line.get("status")))
                ),
                "Line Notes": sanitize_csv_cell(line.get("notes") or ""),
            }
        )
    return rows


def build_purchase_summary_csv_rows(batch, purchase_summary_rows):
    batch_type_label = format_order_batch_type_label(batch.batch_type)
    rows = []
    for summary in purchase_summary_rows:
        rows.append(
            {
                "Batch Name": sanitize_csv_cell(batch.name),
                "Batch Type": sanitize_csv_cell(batch_type_label),
                "Item Name": sanitize_csv_cell(summary.get("item_name") or ""),
                "Setup Group Code": sanitize_csv_cell(summary.get("setup_group_code") or ""),
                "Setup Group Label": sanitize_csv_cell(summary.get("setup_group_label") or ""),
                "Contributing Venue Count": int(summary.get("venue_count") or 0),
                "Contributing Line Count": int(summary.get("line_count") or 0),
                "Total Count": int(summary.get("total_count_snapshot") or 0),
                "Total Par": int(summary.get("total_par_snapshot") or 0),
                "Total Suggested Order": int(summary.get("total_suggested_order_qty") or 0),
                "Total Actual Ordered": int(summary.get("total_actual_ordered_qty") or 0),
                "Total Over Par": int(summary.get("total_over_par_qty") or 0),
                "Status Summary": sanitize_csv_cell(summary.get("status_breakdown_text") or ""),
                "Note Count": int(summary.get("note_count") or 0),
            }
        )
    return rows


def build_order_line_export_filename(batch, *, scope):
    return build_dated_csv_filename(
        "orders_lines",
        scope,
        batch.name or f"batch-{batch.id}",
    )


def build_order_purchase_summary_export_filename(batch, *, scope):
    return build_dated_csv_filename(
        "orders_purchase_summary",
        scope,
        batch.name or f"batch-{batch.id}",
    )


def build_order_line_search_text(line):
    return " ".join(
        [
            line.item_name_snapshot or "",
            line.venue_name_snapshot or "",
            format_order_snapshot_setup_group_display(
                line.setup_group_code_snapshot,
                line.setup_group_label_snapshot,
            )
            or "",
            line.notes or "",
            format_order_line_status_label(line.status),
        ]
    ).lower()
