from datetime import datetime, timedelta, timezone
from math import ceil

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import and_, func
from sqlalchemy.orm import aliased

from app import db
from app.authz import roles_required
from app.models import Item, SupplyNote, User, Venue, VenueItem, VenueItemCount
from app.services.csv_exports import (
    EXPORT_SCOPE_FILTERED,
    EXPORT_SCOPE_FULL,
    build_csv_response,
    build_dated_csv_filename,
    normalize_export_scope,
    sanitize_csv_cell,
)
from app.services.inventory_rules import (
    get_default_stale_threshold_days,
    resolve_effective_par_level,
    resolve_effective_stale_threshold_days,
)
from app.services.inventory_signals import (
    build_latest_count_signal_map,
    build_latest_status_signal_map,
)
from app.services.inventory_status import (
    derive_singleton_count_from_status as shared_derive_singleton_count,
)
from app.services.inventory_status import (
    ensure_utc as shared_ensure_utc,
)
from app.services.inventory_status import (
    is_signal_stale as shared_is_signal_stale,
)
from app.services.inventory_status import (
    normalize_singleton_status as shared_normalize_singleton_status,
)
from app.services.inventory_status import (
    normalize_status,
    restock_status_meta_for_item,
)
from app.services.notes import (
    NOTE_BODY_MAX_LENGTH,
    NOTE_TITLE_MAX_LENGTH,
    SUPPLY_NOTES_PAGE_SIZE,
    build_pagination,
    normalize_note_page,
    validate_note_fields,
)
from app.services.restocking import build_restock_count_state
from app.services.spreadsheet_compat import format_setup_group_display

supplies_bp = Blueprint("supplies", __name__)

SUPPLY_SORT_OPTIONS = {"par_ratio_asc", "missing_venues_desc", "last_updated", "item_name"}
SUPPLY_QUICK_FILTER_OPTIONS = {
    "missing_counts",
    "below_par",
    "complete_coverage",
    "consumable",
    "durable",
    "singleton_asset",
}
SUPPLY_NOTE_FOCUS_OPTIONS = {"list", "compose"}
SUPPLIES_AUDIT_EXPORT_HEADERS = (
    "Venue Name",
    "Item Name",
    "Setup Group Code",
    "Setup Group Label",
    "Tracking Mode",
    "Category",
    "Current Count",
    "Effective Par",
    "Suggested Order Qty",
    "Over Par Qty",
    "Current Quick Status / Last Saved Status",
    "Last Updated",
    "Checked By",
    "Effective Stale Threshold",
    "Is Stale",
    "Note Count",
)


def normalize_singleton_status_key(value):
    return shared_normalize_singleton_status(value)


def derive_singleton_count_from_status(value):
    return shared_derive_singleton_count(value)


def ensure_utc(value):
    return shared_ensure_utc(value)


def format_supply_timestamp(value):
    normalized = ensure_utc(value)
    if normalized is None:
        return "No counts yet"
    return normalized.strftime("%Y-%m-%d %I:%M %p")


def build_supply_timestamp_parts(value):
    normalized = ensure_utc(value)
    if normalized is None:
        return {
            "date_text": "No counts yet",
            "time_text": "",
        }
    return {
        "date_text": normalized.strftime("%Y-%m-%d"),
        "time_text": normalized.strftime("%I:%M %p"),
    }


def build_supply_updated_label(value, stale_threshold=None):
    updated_at = ensure_utc(value)
    if updated_at is None:
        return {
            "text": "No counts yet",
            "is_missing": True,
            "is_stale": True,
        }

    now = datetime.now(timezone.utc)
    delta = max(now - updated_at, timedelta(0))
    total_seconds = int(delta.total_seconds())

    if total_seconds < 60:
        relative = "Just now"
    elif total_seconds < 3600:
        relative = f"{total_seconds // 60}m ago"
    elif total_seconds < 86400:
        relative = f"{total_seconds // 3600}h ago"
    else:
        relative = f"{total_seconds // 86400}d ago"

    return {
        "text": relative,
        "is_missing": False,
        "is_stale": shared_is_signal_stale(updated_at, stale_threshold=stale_threshold),
    }


def is_supply_update_stale(value, stale_threshold=None):
    return shared_is_signal_stale(value, stale_threshold=stale_threshold)


def build_family_progress(counted, total, attention_level):
    if total <= 0:
        return {
            "segments": [False] * 10,
            "tone": "none",
            "label": "No variants",
        }

    ratio = counted / total
    filled_segments = min(10, max(0, ceil(ratio * 10)))

    if counted <= 0:
        tone = "low"
    elif counted < total or attention_level == "critical":
        tone = "low" if attention_level == "critical" else "caution"
    elif attention_level == "warning":
        tone = "caution"
    else:
        tone = "healthy"

    return {
        "segments": [index < filled_segments for index in range(10)],
        "tone": tone,
        "label": f"{counted}/{total} counted",
    }


def normalize_item_type(value):
    normalized = (value or "all").strip().lower()
    if normalized not in {"all", "durable", "consumable"}:
        return "all"
    return normalized


def normalize_coverage(value):
    normalized = (value or "all").strip().lower()
    if normalized not in {"all", "complete", "partial", "no_counts", "not_tracked"}:
        return "all"
    return normalized


def normalize_sort(value):
    normalized = (value or "par_ratio_asc").strip().lower()
    if normalized not in SUPPLY_SORT_OPTIONS:
        return "par_ratio_asc"
    return normalized


def normalize_quick_filters(values):
    normalized = []
    for value in values or []:
        key = (value or "").strip().lower()
        if key in SUPPLY_QUICK_FILTER_OPTIONS and key not in normalized:
            normalized.append(key)
    return normalized


def normalize_supply_note_focus(value):
    normalized = (value or "").strip().lower()
    if normalized in SUPPLY_NOTE_FOCUS_OPTIONS:
        return normalized
    return None


def normalize_supply_note_item_id(raw_value, valid_item_ids):
    raw_text = (raw_value or "").strip()
    if not raw_text or not raw_text.isdigit():
        return None
    item_id = int(raw_text)
    if item_id not in valid_item_ids:
        return None
    return item_id


def build_supply_filter_state(source):
    return {
        "q": (source.get("q") or "").strip(),
        "item_type": normalize_item_type(source.get("item_type")),
        "coverage": normalize_coverage(source.get("coverage")),
        "sort": normalize_sort(source.get("sort")),
        "quick_filters": normalize_quick_filters(source.getlist("quick_filter")),
    }


def build_supply_redirect_response(filters, *, note_item_id=None, note_focus=None):
    route_params = {}
    if filters["q"]:
        route_params["q"] = filters["q"]
    if filters["item_type"] != "all":
        route_params["item_type"] = filters["item_type"]
    if filters["coverage"] != "all":
        route_params["coverage"] = filters["coverage"]
    if filters["sort"] != "par_ratio_asc":
        route_params["sort"] = filters["sort"]
    if filters["quick_filters"]:
        route_params["quick_filter"] = filters["quick_filters"]
    if note_item_id is not None:
        route_params["note_item_id"] = note_item_id
    normalized_focus = normalize_supply_note_focus(note_focus)
    if normalized_focus:
        route_params["note_focus"] = normalized_focus
    return redirect(url_for("supplies.index", **route_params))


def format_supply_note_timestamp(value):
    normalized = ensure_utc(value)
    if normalized is None:
        return "Unknown time"
    return normalized.strftime("%Y-%m-%d %I:%M %p")


def get_supply_note_item(item_id):
    if item_id is None:
        return None
    return (
        db.session.query(Item.id, Item.name)
        .filter(
            Item.id == item_id,
            Item.active == True,
            Item.is_group_parent == False,
        )
        .one_or_none()
    )


def build_supply_note_rows(item_id, page=1):
    note_query = (
        db.session.query(
            SupplyNote,
            User.display_name.label("author_display_name"),
            User.email.label("author_email"),
        )
        .outerjoin(User, User.id == SupplyNote.author_user_id)
        .filter(SupplyNote.item_id == item_id)
    )
    note_total_count = note_query.order_by(None).count()
    note_pagination = build_pagination(note_total_count, page, SUPPLY_NOTES_PAGE_SIZE)
    note_query_rows = (
        note_query.order_by(SupplyNote.updated_at.desc(), SupplyNote.id.desc())
        .offset(note_pagination["offset"])
        .limit(note_pagination["page_size"])
        .all()
    )

    note_rows = []
    for note, author_display_name, author_email in note_query_rows:
        created_at = ensure_utc(note.created_at)
        updated_at = ensure_utc(note.updated_at)
        is_edited = bool(
            created_at
            and updated_at
            and (updated_at - created_at) > timedelta(seconds=1)
        )
        effective_at = updated_at if is_edited else created_at
        note_rows.append(
            {
                "id": note.id,
                "title": note.title,
                "body": note.body,
                "author_name": (author_display_name or "").strip() or (author_email or "Unknown user"),
                "display_time_label": "Edited" if is_edited else "Created",
                "display_time_text": format_supply_note_timestamp(effective_at),
                "can_manage": current_user.is_admin
                or (current_user.role == "staff" and note.author_user_id == current_user.id),
            }
        )

    return note_rows, note_pagination


def build_supply_note_modal_context(
    item_id,
    *,
    note_focus="list",
    feedback=None,
    editor_state=None,
    note_page=1,
    expanded_note_id=None,
):
    active_note_item = get_supply_note_item(item_id)
    if active_note_item is None:
        return None

    normalized_focus = normalize_supply_note_focus(note_focus) or "list"
    normalized_editor_state = {
        "mode": "create",
        "note_id": "",
        "title": "",
        "body": "",
    }
    if editor_state:
        normalized_editor_state.update(
            {
                "mode": editor_state.get("mode") or "create",
                "note_id": str(editor_state.get("note_id") or ""),
                "title": editor_state.get("title") or "",
                "body": editor_state.get("body") or "",
            }
        )
    if normalized_editor_state["mode"] == "edit":
        normalized_focus = "compose"

    supply_note_rows, note_pagination = build_supply_note_rows(active_note_item.id, note_page)
    return {
        "active_note_item": active_note_item,
        "supply_note_rows": supply_note_rows,
        "note_pagination": note_pagination,
        "note_focus": normalized_focus,
        "feedback": feedback,
        "editor_state": normalized_editor_state,
        "expanded_note_id": expanded_note_id,
        "note_title_max_length": NOTE_TITLE_MAX_LENGTH,
        "note_body_max_length": NOTE_BODY_MAX_LENGTH,
    }


def render_supply_note_modal_content(
    item_id,
    *,
    note_focus="list",
    feedback=None,
    editor_state=None,
    note_page=1,
    expanded_note_id=None,
    status_code=200,
):
    context = build_supply_note_modal_context(
        item_id,
        note_focus=note_focus,
        feedback=feedback,
        editor_state=editor_state,
        note_page=note_page,
        expanded_note_id=expanded_note_id,
    )
    if context is None:
        return (
            render_template(
                "supplies/_notes_modal_content.html",
                active_note_item=None,
                supply_note_rows=[],
                note_pagination=build_pagination(0, 1, SUPPLY_NOTES_PAGE_SIZE),
                note_focus="list",
                feedback=feedback
                or {
                    "tone": "error",
                    "title": "Supply item not found.",
                    "body": "Select an active supply item to view notes.",
                },
                editor_state={
                    "mode": "create",
                    "note_id": "",
                    "title": "",
                    "body": "",
                },
                expanded_note_id=None,
                note_title_max_length=NOTE_TITLE_MAX_LENGTH,
                note_body_max_length=NOTE_BODY_MAX_LENGTH,
            ),
            status_code,
        )
    return render_template("supplies/_notes_modal_content.html", **context), status_code


def build_par_progress(total_raw_count, total_par_count):
    segment_count = 10
    empty_segments = [False] * segment_count

    if total_par_count is None:
        return {
            "has_par": False,
            "segments": empty_segments,
            "tone": "none",
            "label": "No par set",
        }

    if total_par_count <= 0:
        return {
            "has_par": True,
            "segments": empty_segments,
            "tone": "none",
            "label": "Par is 0",
        }

    ratio = total_raw_count / total_par_count
    filled_segments = min(segment_count, max(1 if total_raw_count > 0 else 0, ceil(ratio * segment_count)))
    if ratio >= 1:
        tone = "full"
    elif ratio >= 0.75:
        tone = "healthy"
    elif ratio >= 0.4:
        tone = "caution"
    else:
        tone = "low"

    return {
        "has_par": True,
        "segments": [index < filled_segments for index in range(segment_count)],
        "tone": tone,
        "label": f"{round(ratio * 100)}% of par",
    }


def build_singleton_progress(present_count, tracked_count, missing_count):
    segment_count = 10
    empty_segments = [False] * segment_count
    if tracked_count <= 0:
        return {
            "has_par": False,
            "segments": empty_segments,
            "tone": "none",
            "label": "No tracked venues",
        }

    ratio = present_count / tracked_count
    filled_segments = min(segment_count, max(1 if present_count > 0 else 0, ceil(ratio * segment_count)))
    if ratio >= 1 and missing_count <= 0:
        tone = "full"
    elif ratio >= 0.75:
        tone = "healthy"
    elif ratio >= 0.4:
        tone = "caution"
    else:
        tone = "low"
    return {
        "has_par": False,
        "segments": [index < filled_segments for index in range(segment_count)],
        "tone": tone,
        "label": f"{present_count}/{tracked_count} venues present",
    }


def build_par_ratio(total_raw_count, total_par_count):
    if total_par_count is None or total_par_count <= 0:
        return None
    return total_raw_count / total_par_count


def build_singleton_primary_metric(row):
    tracked_count = row["tracked_venue_count"]
    present_count = row["singleton_present_venue_count"]
    if tracked_count <= 0:
        return {
            "value": "—",
            "note": "not tracked",
        }
    return {
        "value": f"{present_count}/{tracked_count}",
        "note": "present",
    }


def build_singleton_condition_summary(row):
    return " · ".join(
        [
            f'{row["singleton_damaged_venue_count"]} damaged',
            f'{row["singleton_missing_venue_count"]} missing',
            f'{row["missing_count_venue_count"]} unchecked',
        ]
    )


def build_issue_summary(row):
    if row["tracking_mode"] == "singleton_asset":
        if row["tracked_venue_count"] == 0:
            return "No active venue assignments"
        if row["missing_count_venue_count"] > 0:
            label = "venue" if row["missing_count_venue_count"] == 1 else "venues"
            return f'{row["missing_count_venue_count"]} not checked {label}'
        if row["singleton_missing_venue_count"] > 0 and row["singleton_damaged_venue_count"] > 0:
            missing_label = "venue" if row["singleton_missing_venue_count"] == 1 else "venues"
            damaged_label = "venue" if row["singleton_damaged_venue_count"] == 1 else "venues"
            return (
                f'{row["singleton_missing_venue_count"]} missing {missing_label}'
                f' · {row["singleton_damaged_venue_count"]} damaged {damaged_label}'
            )
        if row["singleton_missing_venue_count"] > 0:
            label = "venue" if row["singleton_missing_venue_count"] == 1 else "venues"
            return f'{row["singleton_missing_venue_count"]} missing {label}'
        if row["singleton_damaged_venue_count"] > 0:
            label = "venue" if row["singleton_damaged_venue_count"] == 1 else "venues"
            return f'{row["singleton_damaged_venue_count"]} damaged {label}'
        if row["stale_update_venue_count"] > 0:
            label = "update" if row["stale_update_venue_count"] == 1 else "updates"
            return f'{row["stale_update_venue_count"]} stale {label}'
        return "All tracked venues reported present"

    if row["tracked_venue_count"] == 0:
        return "No active venue assignments"
    if row["missing_count_venue_count"] > 0:
        label = "venue" if row["missing_count_venue_count"] == 1 else "venues"
        return f'{row["missing_count_venue_count"]} missing {label}'
    if row["stale_update_venue_count"] > 0:
        label = "update" if row["stale_update_venue_count"] == 1 else "updates"
        return f'{row["stale_update_venue_count"]} stale {label}'
    if row["zero_count_venue_count"] > 0:
        label = "venue" if row["zero_count_venue_count"] == 1 else "venues"
        return f'{row["zero_count_venue_count"]} zero-count {label}'
    return "All tracked venues counted"


def build_desktop_issue_summary(row, updated_label):
    issues = []

    if row["tracking_mode"] == "singleton_asset":
        if row["tracked_venue_count"] == 0:
            issues.append("Not tracked yet")
        elif row["counted_venue_count"] == 0:
            issues.append("No checks recorded")
        elif row["missing_count_venue_count"] > 0:
            label = "venue" if row["missing_count_venue_count"] == 1 else "venues"
            issues.append(f'{row["missing_count_venue_count"]} not checked {label}')
        if row["stale_update_venue_count"] > 0:
            label = "update" if row["stale_update_venue_count"] == 1 else "updates"
            issues.append(f'{row["stale_update_venue_count"]} stale {label}')
        if row["singleton_missing_venue_count"] > 0:
            label = "venue" if row["singleton_missing_venue_count"] == 1 else "venues"
            issues.append(f'{row["singleton_missing_venue_count"]} missing {label}')
        if row["singleton_damaged_venue_count"] > 0:
            label = "venue" if row["singleton_damaged_venue_count"] == 1 else "venues"
            issues.append(f'{row["singleton_damaged_venue_count"]} damaged {label}')
    else:
        if row["tracked_venue_count"] == 0:
            issues.append("Not tracked yet")
        elif row["counted_venue_count"] == 0:
            issues.append("No counts recorded")
        elif row["missing_count_venue_count"] > 0:
            label = "venue" if row["missing_count_venue_count"] == 1 else "venues"
            issues.append(f'{row["missing_count_venue_count"]} missing {label}')
        if row["stale_update_venue_count"] > 0:
            label = "update" if row["stale_update_venue_count"] == 1 else "updates"
            issues.append(f'{row["stale_update_venue_count"]} stale {label}')
        if row["is_below_par"]:
            issues.append("Below par")

    if updated_label["is_missing"] and row["tracked_venue_count"] > 0:
        issues.append("Never updated")

    if not issues and row["zero_count_venue_count"] > 0:
        label = "venue" if row["zero_count_venue_count"] == 1 else "venues"
        issues.append(f'{row["zero_count_venue_count"]} zero-count {label}')

    return " · ".join(issues[:3]) if issues else "Fully counted"


def build_attention_level(row, updated_label):
    if row["tracked_venue_count"] == 0 or row["counted_venue_count"] == 0 or row["missing_count_venue_count"] > 0:
        return "critical"
    if row["tracking_mode"] == "singleton_asset" and row["singleton_missing_venue_count"] > 0:
        return "critical"
    if row["tracking_mode"] == "singleton_asset" and row["singleton_damaged_venue_count"] > 0:
        return "warning"
    if row["is_below_par"] or row["stale_update_venue_count"] > 0:
        return "warning"
    return "healthy"


def resolve_supply_last_actor_label(*, tracking_mode, latest_status_meta, latest_count_meta):
    status_updated_at = ensure_utc((latest_status_meta or {}).get("updated_at"))
    count_updated_at = ensure_utc((latest_count_meta or {}).get("updated_at"))
    status_actor_label = (latest_status_meta or {}).get("actor_label", "")
    count_actor_label = (latest_count_meta or {}).get("actor_label", "")

    if tracking_mode == "singleton_asset":
        return status_actor_label or count_actor_label or ""
    if count_updated_at and (not status_updated_at or count_updated_at >= status_updated_at):
        return count_actor_label or status_actor_label or ""
    if status_updated_at:
        return status_actor_label or count_actor_label or ""
    return count_actor_label or status_actor_label or ""


def coverage_meta_for_row(row):
    if row["tracked_venue_count"] == 0:
        return {
            "key": "not_tracked",
            "label": "Not Tracked",
            "summary": "No active venues are tracking this item yet",
            "compact_summary": "No venues assigned",
        }
    if row["counted_venue_count"] == 0:
        return {
            "key": "no_counts",
            "label": "No Counts",
            "summary": f'0 / {row["tracked_venue_count"]} venues counted',
            "compact_summary": f'0/{row["tracked_venue_count"]} counted',
        }
    if row["missing_count_venue_count"] > 0:
        return {
            "key": "partial",
            "label": "Partial",
            "summary": f'{row["counted_venue_count"]} / {row["tracked_venue_count"]} venues counted',
            "compact_summary": (
                f'{row["counted_venue_count"]}/{row["tracked_venue_count"]} counted'
                f' · {row["missing_count_venue_count"]} missing'
            ),
        }
    return {
        "key": "complete",
        "label": "Complete",
        "summary": f'{row["counted_venue_count"]} / {row["tracked_venue_count"]} venues counted',
        "compact_summary": f'{row["counted_venue_count"]}/{row["tracked_venue_count"]} counted',
    }


def build_supply_audit_rows():
    global_stale_threshold_days = get_default_stale_threshold_days()
    parent_alias = aliased(Item)
    active_items = (
        db.session.query(
            Item.id,
            Item.name,
            Item.item_type,
            Item.item_category,
            Item.setup_group_code.label("setup_group_code"),
            Item.setup_group_label.label("setup_group_label"),
            Item.tracking_mode,
            Item.parent_item_id,
            parent_alias.name.label("parent_name"),
            Item.default_par_level.label("default_par_level"),
            Item.stale_threshold_days.label("item_stale_threshold_days"),
        )
        .outerjoin(parent_alias, parent_alias.id == Item.parent_item_id)
        .filter(Item.active == True, Item.is_group_parent == False)
        .order_by(Item.name.asc())
        .all()
    )

    items_by_id = {
        item.id: {
            "id": item.id,
            "name": item.name,
            "item_type": item.item_type,
            "item_category": item.item_category or item.item_type,
            "setup_group_code": item.setup_group_code,
            "setup_group_label": item.setup_group_label,
            "setup_group_display": format_setup_group_display(item.setup_group_code, item.setup_group_label),
            "tracking_mode": item.tracking_mode or "quantity",
            "default_par_level": item.default_par_level,
            "item_stale_threshold_days": item.item_stale_threshold_days,
            "parent_item_id": item.parent_item_id,
            "parent_name": item.parent_name,
            "total_raw_count": 0,
            "tracked_venue_count": 0,
            "counted_venue_count": 0,
            "missing_count_venue_count": 0,
            "zero_count_venue_count": 0,
            "stale_update_venue_count": 0,
            "singleton_present_venue_count": 0,
            "singleton_missing_venue_count": 0,
            "singleton_damaged_venue_count": 0,
            "total_par_count": 0,
            "par_venue_count": 0,
            "last_count_updated_at": None,
            "minimum_stale_threshold_days": None,
            "notes_count": 0,
            "has_notes": False,
            "venue_rows": [],
        }
        for item in active_items
    }

    if items_by_id:
        note_count_rows = (
            db.session.query(
                SupplyNote.item_id.label("item_id"),
                func.count(SupplyNote.id).label("note_count"),
            )
            .filter(SupplyNote.item_id.in_(list(items_by_id.keys())))
            .group_by(SupplyNote.item_id)
            .all()
        )
        for note_count_row in note_count_rows:
            item_row = items_by_id.get(note_count_row.item_id)
            if item_row is None:
                continue
            item_row["notes_count"] = note_count_row.note_count
            item_row["has_notes"] = note_count_row.note_count > 0

    item_ids = list(items_by_id.keys())
    latest_status_by_venue_item = build_latest_status_signal_map(item_ids=item_ids)
    latest_count_by_venue_item = build_latest_count_signal_map(item_ids=item_ids)

    query_rows = (
        db.session.query(
            Item.id.label("item_id"),
            Item.name.label("item_name"),
            Item.item_type.label("item_type"),
            Item.item_category.label("item_category"),
            Item.tracking_mode.label("tracking_mode"),
            Item.parent_item_id.label("parent_item_id"),
            parent_alias.name.label("parent_name"),
            Venue.id.label("venue_id"),
            Venue.name.label("venue_name"),
            Venue.stale_threshold_days.label("venue_stale_threshold_days"),
            VenueItem.expected_qty.label("par_override"),
            VenueItemCount.raw_count.label("raw_count"),
            VenueItemCount.updated_at.label("updated_at"),
        )
        .select_from(VenueItem)
        .join(Item, Item.id == VenueItem.item_id)
        .outerjoin(parent_alias, parent_alias.id == Item.parent_item_id)
        .join(Venue, Venue.id == VenueItem.venue_id)
        .outerjoin(
            VenueItemCount,
            and_(
                VenueItemCount.venue_id == VenueItem.venue_id,
                VenueItemCount.item_id == VenueItem.item_id,
            ),
        )
        .filter(
            VenueItem.active == True,
            Item.active == True,
            Item.is_group_parent == False,
            Venue.active == True,
        )
        .order_by(Item.name.asc(), Venue.name.asc())
        .all()
    )

    for row in query_rows:
        item_row = items_by_id[row.item_id]
        is_singleton = item_row["tracking_mode"] == "singleton_asset"
        latest_status_meta = latest_status_by_venue_item.get((row.venue_id, row.item_id))
        latest_count_meta = latest_count_by_venue_item.get((row.venue_id, row.item_id))
        singleton_status = "not_checked"
        updated_at = ensure_utc(row.updated_at)
        effective_raw_count = row.raw_count
        effective_par = resolve_effective_par_level(
            item_default_par_level=item_row["default_par_level"],
            venue_par_override=row.par_override,
        )
        effective_stale_threshold = resolve_effective_stale_threshold_days(
            item_stale_threshold_days=item_row["item_stale_threshold_days"],
            venue_stale_threshold_days=row.venue_stale_threshold_days,
            global_stale_threshold_days=global_stale_threshold_days,
        )
        status_key = normalize_status((latest_status_meta or {}).get("status"))

        if latest_count_meta and latest_count_meta.get("updated_at"):
            updated_at = latest_count_meta["updated_at"]

        if is_singleton:
            if latest_status_meta:
                singleton_status = normalize_singleton_status_key(latest_status_meta["status"])
                updated_at = latest_status_meta["updated_at"] or updated_at
            else:
                singleton_status = normalize_singleton_status_key(
                    "good" if row.raw_count and row.raw_count > 0 else ("out" if row.raw_count == 0 else "not_checked")
                )
            effective_raw_count = derive_singleton_count_from_status(singleton_status)
            status_key = singleton_status

        count_state = build_restock_count_state(
            tracking_mode=item_row["tracking_mode"],
            raw_count=effective_raw_count,
            par_value=effective_par.value,
            status_key=status_key,
        )
        status_label = restock_status_meta_for_item(status_key, item_row["tracking_mode"])["text"]
        last_actor_label = resolve_supply_last_actor_label(
            tracking_mode=item_row["tracking_mode"],
            latest_status_meta=latest_status_meta,
            latest_count_meta=latest_count_meta,
        )
        venue_is_stale = (
            is_supply_update_stale(updated_at, stale_threshold=effective_stale_threshold.value)
            if effective_raw_count is not None
            else False
        )

        item_row["tracked_venue_count"] += 1

        if effective_par.value is not None:
            item_row["total_par_count"] += effective_par.value
            item_row["par_venue_count"] += 1
        if item_row["minimum_stale_threshold_days"] is None:
            item_row["minimum_stale_threshold_days"] = effective_stale_threshold.value
        else:
            item_row["minimum_stale_threshold_days"] = min(
                item_row["minimum_stale_threshold_days"],
                effective_stale_threshold.value,
            )

        if effective_raw_count is None:
            item_row["missing_count_venue_count"] += 1
        else:
            item_row["counted_venue_count"] += 1
            item_row["total_raw_count"] += effective_raw_count
            if venue_is_stale:
                item_row["stale_update_venue_count"] += 1
            if is_singleton:
                if singleton_status == "low":
                    item_row["singleton_damaged_venue_count"] += 1
                    item_row["singleton_present_venue_count"] += 1
                elif singleton_status == "good":
                    item_row["singleton_present_venue_count"] += 1
                elif singleton_status == "out":
                    item_row["singleton_missing_venue_count"] += 1
            elif effective_raw_count == 0:
                item_row["zero_count_venue_count"] += 1

            if updated_at and (
                item_row["last_count_updated_at"] is None
                or updated_at > item_row["last_count_updated_at"]
            ):
                item_row["last_count_updated_at"] = updated_at

        item_row["venue_rows"].append(
            {
                "venue_id": row.venue_id,
                "venue_name": row.venue_name,
                "raw_count": effective_raw_count,
                "status_key": singleton_status if is_singleton else None,
                "raw_count_text": "Not Counted" if effective_raw_count is None else str(effective_raw_count),
                "is_missing": effective_raw_count is None,
                "is_stale": venue_is_stale,
                "par_count": effective_par.value,
                "par_count_text": "Not Set" if effective_par.value is None else str(effective_par.value),
                "suggested_order_qty": count_state.get("suggested_order_qty"),
                "over_par_qty": count_state.get("over_par_qty"),
                "current_status_label": status_label,
                "checked_by": last_actor_label,
                "last_updated_at": updated_at,
                "par_source": effective_par.source,
                "stale_threshold_days": effective_stale_threshold.value,
                "stale_threshold_source": effective_stale_threshold.source,
                "note_count": item_row["notes_count"],
                "updated_at_text": (
                    "No checks yet"
                    if is_singleton and updated_at is None
                    else format_supply_timestamp(updated_at)
                ),
            }
        )

    supply_rows = []
    for item_row in items_by_id.values():
        coverage_meta = coverage_meta_for_row(item_row)
        is_singleton = item_row["tracking_mode"] == "singleton_asset"
        if is_singleton:
            if coverage_meta["key"] == "no_counts":
                coverage_meta["label"] = "No Checks"
                coverage_meta["summary"] = f'0 / {item_row["tracked_venue_count"]} venues checked'
                coverage_meta["compact_summary"] = f'0/{item_row["tracked_venue_count"]} checked'
            elif coverage_meta["key"] in {"partial", "complete"}:
                coverage_meta["summary"] = f'{item_row["counted_venue_count"]} / {item_row["tracked_venue_count"]} venues checked'
                compact_summary = f'{item_row["counted_venue_count"]}/{item_row["tracked_venue_count"]} checked'
                if coverage_meta["key"] == "partial" and item_row["missing_count_venue_count"] > 0:
                    compact_summary = f'{compact_summary} · {item_row["missing_count_venue_count"]} missing'
                coverage_meta["compact_summary"] = compact_summary
        total_par_count = item_row["total_par_count"] if item_row["par_venue_count"] > 0 and not is_singleton else None
        item_row["par_ratio"] = build_par_ratio(item_row["total_raw_count"], total_par_count)
        item_row["is_below_par"] = bool(
            not is_singleton
            and total_par_count is not None
            and total_par_count > 0
            and item_row["total_raw_count"] < total_par_count
        )
        updated_label = build_supply_updated_label(
            item_row["last_count_updated_at"],
            stale_threshold=item_row["minimum_stale_threshold_days"],
        )
        updated_parts = build_supply_timestamp_parts(item_row["last_count_updated_at"])
        updated_text = format_supply_timestamp(item_row["last_count_updated_at"])
        if is_singleton and updated_label["is_missing"]:
            updated_label["text"] = "No checks yet"
            updated_parts = {
                "date_text": "No checks yet",
                "time_text": "",
            }
            updated_text = "No checks yet"
        item_row["coverage_key"] = coverage_meta["key"]
        item_row["coverage_label"] = coverage_meta["label"]
        item_row["coverage_summary"] = coverage_meta["summary"]
        item_row["coverage_compact_summary"] = coverage_meta["compact_summary"]
        item_row["total_par_count"] = total_par_count
        item_row["total_par_count_text"] = str(total_par_count) if total_par_count is not None else "Not Set"
        item_row["last_count_updated_at_text"] = updated_text
        item_row["last_count_updated_parts"] = updated_parts
        item_row["last_count_updated_label"] = updated_label
        item_row["last_updated_timestamp"] = (
            item_row["last_count_updated_at"].timestamp() if item_row["last_count_updated_at"] else None
        )
        item_row["effective_stale_threshold_days"] = (
            item_row["minimum_stale_threshold_days"] or global_stale_threshold_days
        )
        if is_singleton:
            item_row["par_progress"] = build_singleton_progress(
                item_row["singleton_present_venue_count"],
                item_row["tracked_venue_count"],
                item_row["missing_count_venue_count"],
            )
            singleton_primary_metric = build_singleton_primary_metric(item_row)
            item_row["singleton_primary_metric"] = singleton_primary_metric["value"]
            item_row["singleton_primary_note"] = singleton_primary_metric["note"]
            item_row["singleton_condition_summary"] = build_singleton_condition_summary(item_row)
        else:
            item_row["par_progress"] = build_par_progress(item_row["total_raw_count"], total_par_count)
        item_row["tracking_mode_label"] = "Singleton Asset" if is_singleton else "Quantity"
        item_row["family_name"] = item_row["parent_name"] or item_row["name"]
        item_row["is_child_item"] = bool(item_row["parent_item_id"])
        item_row["issue_summary"] = build_issue_summary(item_row)
        item_row["desktop_issue_summary"] = build_desktop_issue_summary(item_row, updated_label)
        item_row["attention_level"] = build_attention_level(item_row, updated_label)
        supply_rows.append(item_row)

    return supply_rows


def build_supply_summary(rows):
    quantity_rows = [row for row in rows if row["tracking_mode"] != "singleton_asset"]
    singleton_rows = [row for row in rows if row["tracking_mode"] == "singleton_asset"]
    return {
        "quantity_units_on_hand": sum(row["total_raw_count"] for row in quantity_rows),
        "singleton_present_total": sum(row["singleton_present_venue_count"] for row in singleton_rows),
        "total_active_supply_items": len(rows),
        "singleton_assets": len(singleton_rows),
        "items_with_complete_count_coverage": sum(
            1 for row in rows if row["coverage_key"] == "complete"
        ),
        "items_with_missing_counts": sum(
            1 for row in rows if row["missing_count_venue_count"] > 0
        ),
        "family_variants": sum(1 for row in rows if row.get("parent_name")),
    }


def supply_attention_severity(value):
    return {
        "healthy": 0,
        "warning": 1,
        "critical": 2,
    }.get((value or "healthy").strip().lower(), 0)


def build_supply_display_groups(rows):
    groups = []
    families = {}

    for row in rows:
        parent_name = row.get("parent_name")
        if not parent_name:
            groups.append(
                {
                    "kind": "item",
                    "id": f'item-{row["id"]}',
                    "row": row,
                    "family_name": row["name"],
                    "sort_name": row["family_name"].lower(),
                    "search_text": " ".join(
                        filter(None, [row["name"], row.get("parent_name"), row.get("tracking_mode_label")])
                    ).lower(),
                }
            )
            continue

        family_key = (row["parent_item_id"], parent_name)
        group = families.get(family_key)
        if group is None:
            group = {
                "kind": "family",
                "id": f'family-{row["parent_item_id"] or row["id"]}',
                "family_id": row["parent_item_id"],
                "family_name": parent_name,
                "sort_name": row["family_name"].lower(),
                "children": [],
                "search_text": parent_name.lower(),
                "item_type": row["item_type"],
                "worst_attention_level": "healthy",
                "worst_attention_severity": -1,
                "missing_count_venue_count": 0,
                "stale_update_venue_count": 0,
                "counted_child_count": 0,
                "tracked_child_count": 0,
                "has_singleton_children": False,
                "has_quantity_children": False,
                "last_count_updated_at": None,
                "par_ratio": None,
            }
            families[family_key] = group
            groups.append(group)

        group["children"].append(row)
        group["search_text"] = f'{group["search_text"]} {row["name"].lower()}'
        group["missing_count_venue_count"] += row["missing_count_venue_count"]
        group["stale_update_venue_count"] += row.get("stale_update_venue_count", 0)
        if row["counted_venue_count"] > 0:
            group["counted_child_count"] += 1
        if row["tracked_venue_count"] > 0:
            group["tracked_child_count"] += 1
        if row["tracking_mode"] == "singleton_asset":
            group["has_singleton_children"] = True
        else:
            group["has_quantity_children"] = True

        severity = supply_attention_severity(row["attention_level"])
        if severity > group["worst_attention_severity"]:
            group["worst_attention_severity"] = severity
            group["worst_attention_level"] = row["attention_level"]

        updated_at = row.get("last_count_updated_at")
        if updated_at and (
            group["last_count_updated_at"] is None or updated_at > group["last_count_updated_at"]
        ):
            group["last_count_updated_at"] = updated_at

        ratio = row.get("par_ratio")
        if ratio is not None and (group["par_ratio"] is None or ratio < group["par_ratio"]):
            group["par_ratio"] = ratio

    for group in groups:
        if group["kind"] != "family":
            continue
        child_count = len(group["children"])
        group["child_count"] = child_count
        stale_threshold_days = min(
            [child["effective_stale_threshold_days"] for child in group["children"]]
        ) if group["children"] else None
        updated_label = build_supply_updated_label(
            group["last_count_updated_at"],
            stale_threshold=stale_threshold_days,
        )
        if group["has_singleton_children"] and not group["has_quantity_children"] and updated_label["is_missing"]:
            updated_label["text"] = "No checks yet"
        group["last_count_updated_label"] = updated_label
        group["last_count_updated_parts"] = build_supply_timestamp_parts(group["last_count_updated_at"])
        group["last_count_updated_at_text"] = format_supply_timestamp(group["last_count_updated_at"])
        if group["has_singleton_children"] and not group["has_quantity_children"] and updated_label["is_missing"]:
            group["last_count_updated_parts"] = {"date_text": "No checks yet", "time_text": ""}
            group["last_count_updated_at_text"] = "No checks yet"
        group["last_updated_timestamp"] = (
            group["last_count_updated_at"].timestamp() if group["last_count_updated_at"] else None
        )
        if group["has_singleton_children"] and not group["has_quantity_children"]:
            group["tracking_summary"] = "Asset family"
            group["tracking_badge_kind"] = "asset"
            group["counted_summary"] = f'{group["counted_child_count"]} of {child_count} variants checked'
        elif group["has_quantity_children"] and not group["has_singleton_children"]:
            group["tracking_summary"] = "Quantity family"
            group["tracking_badge_kind"] = "quantity"
            group["counted_summary"] = f'{group["counted_child_count"]} of {child_count} variants counted'
        else:
            group["tracking_summary"] = "Mixed tracking family"
            group["tracking_badge_kind"] = "family"
            group["counted_summary"] = f'{group["counted_child_count"]} of {child_count} variants updated'
        if group["counted_child_count"] == 0:
            group["issue_summary"] = (
                "No variants checked yet"
                if group["has_singleton_children"] and not group["has_quantity_children"]
                else "No variants counted yet"
            )
            group["issue_summary_short"] = (
                "No variant checks yet"
                if group["has_singleton_children"] and not group["has_quantity_children"]
                else "No variant counts yet"
            )
        elif group["missing_count_venue_count"] > 0:
            group["issue_summary"] = f'{group["missing_count_venue_count"]} missing venue counts'
            group["issue_summary_short"] = f'{group["missing_count_venue_count"]} missing venue counts'
        elif group["stale_update_venue_count"] > 0:
            label = "update" if group["stale_update_venue_count"] == 1 else "updates"
            group["issue_summary"] = f'{group["stale_update_venue_count"]} stale {label}'
            group["issue_summary_short"] = f'{group["stale_update_venue_count"]} stale {label}'
        elif group["worst_attention_level"] == "critical":
            group["issue_summary"] = "At least one variant needs attention"
            group["issue_summary_short"] = "Variant needs attention"
        elif group["worst_attention_level"] == "warning":
            group["issue_summary"] = "Some variants are below par or stale"
            group["issue_summary_short"] = "Below par or stale"
        else:
            group["issue_summary"] = "All visible variants look healthy"
            group["issue_summary_short"] = "Variants look healthy"

        if group["counted_child_count"] == 0:
            group["coverage_key"] = "no_counts"
            group["coverage_label"] = (
                "No Checks" if group["has_singleton_children"] and not group["has_quantity_children"] else "No Counts"
            )
        elif group["counted_child_count"] < child_count or group["missing_count_venue_count"] > 0:
            group["coverage_key"] = "partial"
            group["coverage_label"] = "Partial"
        else:
            group["coverage_key"] = "complete"
            group["coverage_label"] = "Complete"

        group["family_progress"] = build_family_progress(
            group["counted_child_count"],
            child_count,
            group["worst_attention_level"],
        )
        if child_count <= 0:
            group["family_progress_caption"] = "No variants"
        else:
            progress_percent = round((group["counted_child_count"] / child_count) * 100)
            group["family_progress_caption"] = f"{progress_percent}% variants checked"

    return groups


def filter_supply_rows(rows, search_query, item_type, coverage, quick_filters=None):
    normalized_query = (search_query or "").strip().lower()
    quick_filters = quick_filters or []
    filtered_rows = []
    for row in rows:
        haystack = " ".join(filter(None, [row["name"], row.get("parent_name"), row.get("tracking_mode_label")])).lower()
        if normalized_query and normalized_query not in haystack:
            continue
        if item_type != "all" and row["item_type"] != item_type:
            continue
        if coverage != "all" and row["coverage_key"] != coverage:
            continue

        type_filters = [value for value in quick_filters if value in {"durable", "consumable"}]
        if type_filters and row["item_type"] not in type_filters:
            continue
        if "singleton_asset" in quick_filters and row["tracking_mode"] != "singleton_asset":
            continue
        if "missing_counts" in quick_filters and row["missing_count_venue_count"] <= 0:
            continue
        if "below_par" in quick_filters and not row["is_below_par"]:
            continue
        if "complete_coverage" in quick_filters and row["coverage_key"] != "complete":
            continue
        filtered_rows.append(row)
    return filtered_rows


def supply_sort_key(row, sort_key):
    family_name = (row.get("parent_name") or row["name"]).lower()
    display_name = row["name"].lower()

    if sort_key == "item_name":
        return (
            family_name,
            1 if row.get("parent_name") else 0,
            display_name,
            row["item_type"],
            row["id"],
        )
    if sort_key == "missing_venues_desc":
        return (
            -row["missing_count_venue_count"],
            -(row["tracked_venue_count"] - row["counted_venue_count"]),
            family_name,
            display_name,
        )
    if sort_key == "last_updated":
        has_updated_at = row["last_count_updated_at"] is not None
        timestamp = row["last_count_updated_at"].timestamp() if has_updated_at else float("-inf")
        return (
            0 if has_updated_at else 1,
            -timestamp,
            family_name,
            display_name,
        )
    if sort_key == "par_ratio_asc":
        has_ratio = row["par_ratio"] is not None
        ratio = row["par_ratio"] if has_ratio else float("inf")
        return (
            0 if has_ratio else 1,
            ratio,
            -row["missing_count_venue_count"],
            family_name,
            display_name,
        )
    return (
        -row["total_raw_count"],
        family_name,
        display_name,
        row["id"],
    )


def build_supplies_audit_export_rows(rows):
    export_rows = []
    for item_row in rows:
        sorted_venue_rows = sorted(
            item_row.get("venue_rows", []),
            key=lambda venue_row: (
                (venue_row.get("venue_name") or "").lower(),
                int(venue_row.get("venue_id") or 0),
            ),
        )
        for venue_row in sorted_venue_rows:
            export_rows.append(
                {
                    "Venue Name": sanitize_csv_cell(venue_row.get("venue_name") or ""),
                    "Item Name": sanitize_csv_cell(item_row.get("name") or ""),
                    "Setup Group Code": sanitize_csv_cell(item_row.get("setup_group_code") or ""),
                    "Setup Group Label": sanitize_csv_cell(item_row.get("setup_group_label") or ""),
                    "Tracking Mode": sanitize_csv_cell(item_row.get("tracking_mode_label") or ""),
                    "Category": sanitize_csv_cell(item_row.get("item_category") or ""),
                    "Current Count": (
                        ""
                        if venue_row.get("raw_count") is None
                        else venue_row.get("raw_count")
                    ),
                    "Effective Par": (
                        ""
                        if venue_row.get("par_count") is None
                        else venue_row.get("par_count")
                    ),
                    "Suggested Order Qty": (
                        ""
                        if venue_row.get("suggested_order_qty") is None
                        else int(venue_row.get("suggested_order_qty") or 0)
                    ),
                    "Over Par Qty": (
                        ""
                        if venue_row.get("over_par_qty") is None
                        else int(venue_row.get("over_par_qty") or 0)
                    ),
                    "Current Quick Status / Last Saved Status": sanitize_csv_cell(
                        venue_row.get("current_status_label") or ""
                    ),
                    "Last Updated": sanitize_csv_cell(venue_row.get("updated_at_text") or ""),
                    "Checked By": sanitize_csv_cell(venue_row.get("checked_by") or ""),
                    "Effective Stale Threshold": venue_row.get("stale_threshold_days") or "",
                    "Is Stale": "Yes" if venue_row.get("is_stale") else "No",
                    "Note Count": int(venue_row.get("note_count") or 0),
                }
            )
    return export_rows


def build_supplies_export_filename(*, scope):
    tokens = []
    if scope == EXPORT_SCOPE_FILTERED:
        tokens.append("filtered")
    return build_dated_csv_filename("supplies_audit", *tokens)


@supplies_bp.route("/supplies/notes/modal", methods=["GET", "POST"])
@roles_required("viewer", "staff", "admin")
def note_modal():
    note_page = normalize_note_page(
        request.args.get("note_page") or request.form.get("note_page")
    )
    if request.method == "GET":
        raw_item_id = (request.args.get("item_id") or "").strip()
        item_id = int(raw_item_id) if raw_item_id.isdigit() else None
        item_exists = get_supply_note_item(item_id) is not None
        return render_supply_note_modal_content(
            item_id,
            note_focus=request.args.get("note_focus"),
            note_page=note_page,
            status_code=200 if item_exists else 404,
        )

    action = (request.form.get("action") or "").strip().lower()
    raw_item_id = (request.form.get("item_id") or "").strip()
    item_id = int(raw_item_id) if raw_item_id.isdigit() else None

    if action in {"create_note", "edit_note", "delete_note"} and not current_user.is_staff:
        return render_supply_note_modal_content(
            item_id,
            note_focus="list",
            note_page=note_page,
            feedback={
                "tone": "error",
                "title": "Only staff and admins can manage notes.",
                "body": "Viewer accounts can read notes but cannot create, edit, or delete them.",
            },
            status_code=403,
        )

    if action == "create_note":
        active_note_item = get_supply_note_item(item_id)
        title = (request.form.get("title") or "").strip()
        body = (request.form.get("body") or "").strip()

        if active_note_item is None:
            return render_supply_note_modal_content(
                item_id,
                note_focus="compose",
                note_page=note_page,
                feedback={
                    "tone": "error",
                    "title": "Select an active supply item before adding a note.",
                    "body": None,
                },
                editor_state={"mode": "create", "title": title, "body": body},
                status_code=400,
            )
        validation_error = validate_note_fields(title, body)
        if validation_error:
            return render_supply_note_modal_content(
                active_note_item.id,
                note_focus="compose",
                note_page=note_page,
                feedback={"tone": "error", "title": validation_error, "body": None},
                editor_state={"mode": "create", "title": title, "body": body},
                status_code=400,
            )

        new_note = SupplyNote(
            item_id=active_note_item.id,
            author_user_id=current_user.id,
            title=title,
            body=body,
        )
        db.session.add(new_note)
        db.session.commit()
        return render_supply_note_modal_content(
            active_note_item.id,
            note_focus="list",
            note_page=1,
            feedback={"tone": "success", "title": "Note added.", "body": None},
            expanded_note_id=new_note.id,
        )

    if action in {"edit_note", "delete_note"}:
        note = None
        note_id_raw = (request.form.get("note_id") or "").strip()
        if note_id_raw.isdigit():
            note = db.session.get(SupplyNote, int(note_id_raw))

        note_item_id = note.item_id if note is not None else item_id
        active_note_item = get_supply_note_item(note_item_id)
        if note is None or active_note_item is None or note.item_id != active_note_item.id:
            return render_supply_note_modal_content(
                note_item_id,
                note_focus="list",
                note_page=note_page,
                feedback={"tone": "error", "title": "Note not found for this supply item.", "body": None},
                status_code=404,
            )

        can_manage_note = current_user.is_admin or (
            current_user.role == "staff" and note.author_user_id == current_user.id
        )
        if not can_manage_note:
            return render_supply_note_modal_content(
                active_note_item.id,
                note_focus="list",
                note_page=note_page,
                feedback={
                    "tone": "error",
                    "title": "You can only edit or delete your own notes.",
                    "body": None,
                },
                status_code=403,
            )

        if action == "edit_note":
            title = (request.form.get("title") or "").strip()
            body = (request.form.get("body") or "").strip()
            validation_error = validate_note_fields(title, body)
            if validation_error:
                return render_supply_note_modal_content(
                    active_note_item.id,
                    note_focus="compose",
                    note_page=note_page,
                    feedback={"tone": "error", "title": validation_error, "body": None},
                    editor_state={
                        "mode": "edit",
                        "note_id": note.id,
                        "title": title,
                        "body": body,
                    },
                    status_code=400,
                )
            note.title = title
            note.body = body
            db.session.commit()
            return render_supply_note_modal_content(
                active_note_item.id,
                note_focus="list",
                note_page=1,
                feedback={"tone": "success", "title": "Note updated.", "body": None},
                expanded_note_id=note.id,
            )

        db.session.delete(note)
        db.session.commit()
        return render_supply_note_modal_content(
            active_note_item.id,
            note_focus="list",
            note_page=note_page,
            feedback={"tone": "success", "title": "Note deleted.", "body": None},
        )

    return render_supply_note_modal_content(
        item_id,
        note_focus="list",
        note_page=note_page,
        feedback={"tone": "error", "title": "Unsupported note action.", "body": None},
        status_code=400,
    )


@supplies_bp.route("/supplies", methods=["GET", "POST"])
@roles_required("viewer", "staff", "admin")
def index():
    filters = build_supply_filter_state(request.values)
    all_supply_rows = build_supply_audit_rows()
    all_supply_rows_by_id = {row["id"]: row for row in all_supply_rows}
    valid_note_item_ids = set(all_supply_rows_by_id)
    active_note_item_id = normalize_supply_note_item_id(
        request.values.get("note_item_id"),
        valid_note_item_ids,
    )
    note_focus = normalize_supply_note_focus(request.values.get("note_focus"))
    if active_note_item_id is None:
        note_focus = None
    elif note_focus is None:
        note_focus = "list"

    if request.method == "POST":
        action = (request.form.get("action") or "").strip().lower()
        if action in {"create_note", "edit_note", "delete_note"} and not current_user.is_staff:
            flash("Only staff and admins can manage notes.", "error")
            return build_supply_redirect_response(
                filters,
                note_item_id=active_note_item_id,
                note_focus="list" if active_note_item_id is not None else None,
            )

        if action == "create_note":
            note_item_raw = request.form.get("item_id", "")
            note_item_id = normalize_supply_note_item_id(note_item_raw, valid_note_item_ids)
            title = (request.form.get("title") or "").strip()
            body = (request.form.get("body") or "").strip()

            if note_item_id is None:
                flash("Select an active supply item before adding a note.", "error")
                return build_supply_redirect_response(
                    filters,
                    note_item_id=active_note_item_id,
                    note_focus="compose",
                )
            validation_error = validate_note_fields(title, body)
            if validation_error:
                flash(validation_error, "error")
                return build_supply_redirect_response(
                    filters,
                    note_item_id=note_item_id,
                    note_focus="compose",
                )

            db.session.add(
                SupplyNote(
                    item_id=note_item_id,
                    author_user_id=current_user.id,
                    title=title,
                    body=body,
                )
            )
            db.session.commit()
            flash("Note added.", "success")
            return build_supply_redirect_response(
                filters,
                note_item_id=note_item_id,
                note_focus="list",
            )

        if action in {"edit_note", "delete_note"}:
            note = None
            note_id_raw = (request.form.get("note_id") or "").strip()
            if note_id_raw.isdigit():
                note = db.session.get(SupplyNote, int(note_id_raw))
            if note is None or note.item_id not in valid_note_item_ids:
                flash("Note not found for this supply item.", "error")
                return build_supply_redirect_response(
                    filters,
                    note_item_id=active_note_item_id,
                    note_focus="list" if active_note_item_id is not None else None,
                )

            can_manage_note = current_user.is_admin or (
                current_user.role == "staff" and note.author_user_id == current_user.id
            )
            if not can_manage_note:
                flash("You can only edit or delete your own notes.", "error")
                return build_supply_redirect_response(
                    filters,
                    note_item_id=active_note_item_id or note.item_id,
                    note_focus="list",
                )

            if action == "edit_note":
                title = (request.form.get("title") or "").strip()
                body = (request.form.get("body") or "").strip()
                validation_error = validate_note_fields(title, body)
                if validation_error:
                    flash(validation_error, "error")
                    return build_supply_redirect_response(
                        filters,
                        note_item_id=note.item_id,
                        note_focus="list",
                    )
                note.title = title
                note.body = body
                db.session.commit()
                flash("Note updated.", "success")
                return build_supply_redirect_response(
                    filters,
                    note_item_id=note.item_id,
                    note_focus="list",
                )

            redirect_note_item_id = note.item_id
            db.session.delete(note)
            db.session.commit()
            flash("Note deleted.", "success")
            return build_supply_redirect_response(
                filters,
                note_item_id=redirect_note_item_id,
                note_focus="list",
            )

    summary = build_supply_summary(all_supply_rows)
    filtered_rows = filter_supply_rows(
        all_supply_rows,
        filters["q"],
        filters["item_type"],
        filters["coverage"],
        filters["quick_filters"],
    )
    filtered_rows = sorted(filtered_rows, key=lambda row: supply_sort_key(row, filters["sort"]))
    supply_groups = build_supply_display_groups(filtered_rows)

    return render_template(
        "supplies/index.html",
        summary=summary,
        filters=filters,
        supply_rows=filtered_rows,
        supply_groups=supply_groups,
        supplies_export_base_url=url_for("supplies.export_supplies_audit"),
        total_supply_items=len(all_supply_rows),
        filtered_supply_items=len(filtered_rows),
        active_note_item_id=active_note_item_id,
        note_focus=note_focus,
    )


@supplies_bp.get("/supplies/export.csv")
@roles_required("viewer", "staff", "admin")
def export_supplies_audit():
    filters = build_supply_filter_state(request.args)
    scope = normalize_export_scope(request.args.get("scope"), default=EXPORT_SCOPE_FILTERED)
    all_supply_rows = build_supply_audit_rows()

    if scope == EXPORT_SCOPE_FULL:
        export_filters = {
            "q": "",
            "item_type": "all",
            "coverage": "all",
            "sort": filters["sort"],
            "quick_filters": [],
        }
    else:
        export_filters = filters

    export_rows = filter_supply_rows(
        all_supply_rows,
        export_filters["q"],
        export_filters["item_type"],
        export_filters["coverage"],
        export_filters["quick_filters"],
    )
    export_rows = sorted(
        export_rows,
        key=lambda row: supply_sort_key(row, export_filters["sort"]),
    )
    csv_rows = build_supplies_audit_export_rows(export_rows)
    filename = build_supplies_export_filename(scope=scope)
    return build_csv_response(SUPPLIES_AUDIT_EXPORT_HEADERS, csv_rows, filename)
