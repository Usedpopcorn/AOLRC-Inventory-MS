from datetime import datetime, time, timedelta, timezone

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import Integer, String, and_, case, cast, func, literal, or_, select, union_all
from sqlalchemy.orm import selectinload

from app import db
from app.authz import roles_required
from app.models import (
    Check,
    CheckLine,
    CountLine,
    CountSession,
    Item,
    User,
    Venue,
    VenueItem,
    VenueNote,
)
from app.security import normalize_safe_redirect_path
from app.services.csv_exports import (
    EXPORT_SCOPE_FILTERED,
    build_csv_response,
    normalize_export_scope,
)
from app.services.inventory_rules import (
    InventoryRuleError,
    copy_venue_tracking_setup,
    get_default_stale_threshold_days,
    log_inventory_admin_event,
    normalize_optional_threshold_days,
    resolve_effective_stale_threshold_days,
    sync_venue_tracked_items,
)
from app.services.inventory_signals import (
    build_latest_count_signal_map,
    build_latest_status_signal_map,
)
from app.services.inventory_status import normalize_singleton_status, normalize_status
from app.services.notes import (
    NOTE_BODY_MAX_LENGTH,
    NOTE_TITLE_MAX_LENGTH,
    VENUE_NOTES_PAGE_SIZE,
    build_pagination,
    normalize_note_page,
    validate_note_fields,
)
from app.services.restocking import (
    RESTOCK_STATUS_META,
    build_restock_rows,
    normalize_restock_mode,
    normalize_restock_sort,
)
from app.services.venue_profile import (
    VENUE_INVENTORY_EXPORT_HEADERS,
    build_venue_inventory_csv_rows,
    build_venue_inventory_export_filename,
    build_venue_profile_view_model,
    filter_venue_inventory_rows,
    normalize_venue_inventory_filters,
)

main_bp = Blueprint("main", __name__)
RESTOCK_PAGE_SIZE = 50
ACTIVITY_PAGE_SIZE = 50

ACTIVITY_TYPE_META = {
    "status": {
        "text": "Status",
        "icon_class": "bi-clipboard-check",
        "badge_class": "activity-type-status",
    },
    "raw_count": {
        "text": "Count",
        "icon_class": "bi-123",
        "badge_class": "activity-type-count",
    },
}

ACTIVITY_SORT_OPTIONS = {"newest", "oldest", "venue", "item", "actor", "type"}


def normalize_next_path(next_candidate, fallback_path):
    return normalize_safe_redirect_path(next_candidate, fallback_path)


def describe_back_destination(next_path, venue_id):
    target_path = next_path.split("?", 1)[0]

    if target_path == url_for("main.dashboard"):
        return "Dashboard"
    if target_path == url_for("main.venues"):
        return "Venues"
    if target_path == url_for("main.venue_detail", venue_id=venue_id):
        return "Venue Profile"
    if target_path == url_for("venue_items.quick_check", venue_id=venue_id):
        return "Venue Check"
    if target_path == url_for("venue_settings.settings", venue_id=venue_id):
        return "Venue Settings"
    if target_path == url_for("venue_items.supplies", venue_id=venue_id):
        return "Venue Supplies"
    return "Previous Page"


def normalize_note_focus(value):
    normalized = (value or "").strip().lower()
    if normalized not in {"compose", "list"}:
        return None
    return normalized


def normalize_note_kind_filter(value):
    normalized = (value or "").strip().lower()
    if normalized not in {"general", "tagged"}:
        return "all"
    return normalized


def normalize_note_search_query(value):
    return (value or "").strip()


def load_valid_venue_note_items(venue_id):
    rows = (
        db.session.query(Item.id, Item.name)
        .join(VenueItem, VenueItem.item_id == Item.id)
        .filter(
            VenueItem.venue_id == venue_id,
            VenueItem.active == True,
            Item.active == True,
            Item.is_group_parent == False,
        )
        .all()
    )
    return {row.id: row.name for row in rows}


def load_valid_venue_note_item_ids(venue_id):
    return set(load_valid_venue_note_items(venue_id))


def normalize_venue_note_item_id(raw_value, valid_item_ids):
    value = (raw_value or "").strip()
    if not value or not value.isdigit():
        return None
    item_id = int(value)
    if item_id not in valid_item_ids:
        return None
    return item_id


def redirect_to_venue_detail(
    venue_id,
    *,
    next_path,
    profile_tab="overview",
    note_item_id=None,
    note_focus=None,
    note_q=None,
    note_kind="all",
    note_page=None,
):
    route_values = {
        "venue_id": venue_id,
        "next": next_path,
        "profile_tab": profile_tab,
    }
    if note_item_id is not None:
        route_values["note_item_id"] = note_item_id
    if note_focus:
        route_values["note_focus"] = note_focus
    if note_q:
        route_values["note_q"] = note_q
    if note_kind and note_kind != "all":
        route_values["note_kind"] = note_kind
    if note_page and int(note_page) > 1:
        route_values["note_page"] = int(note_page)
    return redirect(url_for("main.venue_detail", **route_values))


def build_status_detail_counts():
    return {
        "low_quantity": 0,
        "low_singleton": 0,
        "out_quantity": 0,
        "out_singleton": 0,
    }


def build_overall_status_badge(total_tracked, counts, detail_counts=None):
    if total_tracked <= 0:
        return {"key": "not_checked", "text": "Not Checked", "icon_class": "bi-dash-circle"}

    detail_counts = detail_counts or build_status_detail_counts()
    checked_count = total_tracked - counts["not_checked"]

    if counts["out"] > 0:
        out_count = counts["out"]
        if detail_counts["out_singleton"] > 0 and detail_counts["out_quantity"] == 0:
            label = "item missing" if out_count == 1 else "items missing"
            return {"key": "out", "text": f"{out_count} {label}", "icon_class": "bi-x-circle-fill"}
        if detail_counts["out_singleton"] > 0 and detail_counts["out_quantity"] > 0:
            label = "item needs attention" if out_count == 1 else "items need attention"
            return {"key": "out", "text": f"{out_count} {label}", "icon_class": "bi-x-circle-fill"}
        label = "item out of stock" if out_count == 1 else "items out of stock"
        return {"key": "out", "text": f"{out_count} {label}", "icon_class": "bi-x-circle-fill"}

    if counts["low"] > 0:
        low_count = counts["low"]
        if detail_counts["low_singleton"] > 0 and detail_counts["low_quantity"] == 0:
            label = "item damaged" if low_count == 1 else "items damaged"
            return {
                "key": "low",
                "text": f"{low_count} {label}",
                "icon_class": "bi-exclamation-triangle-fill",
            }
        if detail_counts["low_singleton"] > 0 and detail_counts["low_quantity"] > 0:
            label = "item needs attention" if low_count == 1 else "items need attention"
            return {
                "key": "low",
                "text": f"{low_count} {label}",
                "icon_class": "bi-exclamation-triangle-fill",
            }
        label = "item low" if low_count == 1 else "items low"
        return {
            "key": "low",
            "text": f"{low_count} {label}",
            "icon_class": "bi-exclamation-triangle-fill",
        }

    if checked_count > 0 and counts["ok"] > 0 and (counts["ok"] * 2 >= checked_count):
        return {"key": "ok", "text": "OK", "icon_class": "bi-check-circle-fill"}
    if counts["good"] > 0:
        good_has_partial_attention = counts["ok"] > 0 or counts["not_checked"] > 0
        return {
            "key": "good",
            "text": "Good*" if good_has_partial_attention else "Good",
            "icon_class": "bi-check-circle-fill",
        }
    return {"key": "not_checked", "text": "Not Checked", "icon_class": "bi-dash-circle"}


def normalize_activity_type(value):
    normalized = (value or "").strip().lower()
    if normalized not in ACTIVITY_TYPE_META:
        return "all"
    return normalized


def normalize_activity_sort(value):
    normalized = (value or "newest").strip().lower()
    if normalized not in ACTIVITY_SORT_OPTIONS:
        return "newest"
    return normalized


def normalize_activity_actor_user_id(value):
    raw_value = (value or "").strip()
    if not raw_value or not raw_value.isdigit():
        return None
    parsed_value = int(raw_value)
    if parsed_value <= 0:
        return None
    return parsed_value


def format_activity_timestamp(value):
    if value is None:
        return "Unknown time"
    return value.strftime("%Y-%m-%d %I:%M %p")


def parse_activity_date(value):
    raw_value = (value or "").strip()
    if not raw_value:
        return None
    try:
        return datetime.strptime(raw_value, "%Y-%m-%d").date()
    except ValueError:
        return None


def normalize_activity_date_range(start_date, end_date):
    if start_date and end_date and start_date > end_date:
        return end_date, start_date
    return start_date, end_date


def activity_date_boundary_utc(date_value, day_offset=0):
    boundary_date = date_value + timedelta(days=day_offset)
    return datetime.combine(boundary_date, time.min, tzinfo=timezone.utc)


def activity_actor_name_expr(display_name_col, email_col):
    trimmed_display_name = func.nullif(func.trim(display_name_col), "")
    trimmed_email = func.nullif(func.trim(email_col), "")
    return func.coalesce(trimmed_display_name, func.lower(trimmed_email), literal("Unknown user"))


def activity_status_text_expr(column):
    return case(
        (column == "good", literal("Good")),
        (column == "ok", literal("OK")),
        (column == "low", literal("Low")),
        (column == "out", literal("Out")),
        else_=literal("Not Checked"),
    )


def serialize_activity_row(row):
    type_key = row["type_key"]
    changed_at = row["changed_at"]
    actor_name = row["actor_name"] or "Unknown user"

    if type_key == "status":
        old_status_key = normalize_status(row["old_status_key"]) if row["old_status_key"] else None
        new_status_key = normalize_status(row["new_status_key"])
        new_status_meta = RESTOCK_STATUS_META[new_status_key]
        if old_status_key is None:
            old_value_text = "No prior status"
            detail_text = f'Initial status recorded as {new_status_meta["text"]}'
        else:
            old_value_text = RESTOCK_STATUS_META[old_status_key]["text"]
            detail_text = f'Status changed from {old_value_text} to {new_status_meta["text"]}'

        search_text = " ".join(
            [
                "status",
                row["venue_name"] or "",
                row["item_name"] or "",
                actor_name,
                old_value_text,
                new_status_meta["text"],
                detail_text,
            ]
        ).lower()

        return {
            "type_key": "status",
            "type_meta": ACTIVITY_TYPE_META["status"],
            "venue_name": row["venue_name"],
            "item_name": row["item_name"],
            "actor_name": actor_name,
            "changed_at_text": format_activity_timestamp(changed_at),
            "old_value_text": old_value_text,
            "new_value_text": new_status_meta["text"],
            "old_status_key": old_status_key,
            "new_status_key": new_status_key,
            "old_value_missing": old_status_key is None,
            "detail_text": detail_text,
            "search_text": search_text,
        }

    previous_raw_count = row["old_raw_count"]
    new_raw_count = row["new_raw_count"]
    if previous_raw_count is None:
        old_value_text = "No prior count"
        detail_text = f"Initial count recorded as {new_raw_count}"
    else:
        old_value_text = str(previous_raw_count)
        detail_text = f"Count changed from {previous_raw_count} to {new_raw_count}"

    new_value_text = str(new_raw_count)
    search_text = " ".join(
        [
            "count",
            row["venue_name"] or "",
            row["item_name"] or "",
            actor_name,
            old_value_text,
            new_value_text,
            detail_text,
        ]
    ).lower()

    return {
        "type_key": "raw_count",
        "type_meta": ACTIVITY_TYPE_META["raw_count"],
        "venue_name": row["venue_name"],
        "item_name": row["item_name"],
        "actor_name": actor_name,
        "changed_at_text": format_activity_timestamp(changed_at),
        "old_value_text": old_value_text,
        "new_value_text": new_value_text,
        "old_status_key": None,
        "new_status_key": None,
        "old_value_missing": previous_raw_count is None,
        "detail_text": detail_text,
        "search_text": search_text,
    }


def build_activity_page(
    search="",
    activity_type="all",
    start_date=None,
    end_date=None,
    sort="newest",
    page=1,
    actor_user_id=None,
    page_size=None,
):
    requested_page = max(int(page or 1), 1)

    status_actor_name = activity_actor_name_expr(User.display_name, User.email)
    previous_status = func.lag(CheckLine.status).over(
        partition_by=(Check.venue_id, CheckLine.item_id),
        order_by=(Check.created_at.asc(), Check.id.asc()),
    )
    status_inner = (
        select(
            literal("status").label("type_key"),
            Check.id.label("event_id"),
            Check.created_at.label("changed_at"),
            Venue.name.label("venue_name"),
            Item.name.label("item_name"),
            Check.user_id.label("actor_user_id"),
            status_actor_name.label("actor_name"),
            previous_status.label("old_status_key"),
            CheckLine.status.label("new_status_key"),
            cast(literal(None), Integer).label("old_raw_count"),
            cast(literal(None), Integer).label("new_raw_count"),
        )
        .select_from(Check)
        .join(Venue, Venue.id == Check.venue_id)
        .join(CheckLine, CheckLine.check_id == Check.id)
        .join(Item, Item.id == CheckLine.item_id)
        .outerjoin(User, User.id == Check.user_id)
        .subquery()
    )
    status_events = select(status_inner).where(
        or_(
            and_(
                status_inner.c.old_status_key.is_(None),
                status_inner.c.new_status_key != "not_checked",
            ),
            status_inner.c.old_status_key != status_inner.c.new_status_key,
        )
    )
    if actor_user_id is not None:
        status_events = status_events.where(status_inner.c.actor_user_id == actor_user_id)

    count_actor_name = activity_actor_name_expr(User.display_name, User.email)
    previous_raw_count = func.lag(CountLine.raw_count).over(
        partition_by=(CountSession.venue_id, CountLine.item_id),
        order_by=(CountSession.created_at.asc(), CountSession.id.asc()),
    )
    count_inner = (
        select(
            literal("raw_count").label("type_key"),
            CountSession.id.label("event_id"),
            CountSession.created_at.label("changed_at"),
            Venue.name.label("venue_name"),
            Item.name.label("item_name"),
            CountSession.user_id.label("actor_user_id"),
            count_actor_name.label("actor_name"),
            cast(literal(None), String).label("old_status_key"),
            cast(literal(None), String).label("new_status_key"),
            previous_raw_count.label("old_raw_count"),
            CountLine.raw_count.label("new_raw_count"),
        )
        .select_from(CountSession)
        .join(Venue, Venue.id == CountSession.venue_id)
        .join(CountLine, CountLine.count_session_id == CountSession.id)
        .join(Item, Item.id == CountLine.item_id)
        .outerjoin(User, User.id == CountSession.user_id)
        .subquery()
    )
    count_events = select(count_inner).where(
        or_(
            count_inner.c.old_raw_count.is_(None),
            count_inner.c.old_raw_count != count_inner.c.new_raw_count,
        )
    )
    if actor_user_id is not None:
        count_events = count_events.where(count_inner.c.actor_user_id == actor_user_id)

    activity_events = union_all(status_events, count_events).subquery()
    filtered_activity = select(activity_events)

    if activity_type in ACTIVITY_TYPE_META:
        filtered_activity = filtered_activity.where(activity_events.c.type_key == activity_type)

    if start_date:
        filtered_activity = filtered_activity.where(
            activity_events.c.changed_at >= activity_date_boundary_utc(start_date)
        )
    if end_date:
        filtered_activity = filtered_activity.where(
            activity_events.c.changed_at < activity_date_boundary_utc(end_date, day_offset=1)
        )

    search_query = (search or "").strip().lower()
    if search_query:
        old_value_search = case(
            (
                activity_events.c.type_key == "status",
                func.lower(activity_status_text_expr(activity_events.c.old_status_key)),
            ),
            else_=func.lower(
                func.coalesce(cast(activity_events.c.old_raw_count, String), literal("no prior count"))
            ),
        )
        new_value_search = case(
            (
                activity_events.c.type_key == "status",
                func.lower(activity_status_text_expr(activity_events.c.new_status_key)),
            ),
            else_=func.lower(func.coalesce(cast(activity_events.c.new_raw_count, String), literal(""))),
        )
        filtered_activity = filtered_activity.where(
            or_(
                func.lower(activity_events.c.venue_name).contains(search_query),
                func.lower(activity_events.c.item_name).contains(search_query),
                func.lower(activity_events.c.actor_name).contains(search_query),
                func.lower(func.replace(activity_events.c.type_key, "_", " ")).contains(search_query),
                old_value_search.contains(search_query),
                new_value_search.contains(search_query),
            )
        )

    filtered_subquery = filtered_activity.subquery()
    total_count = db.session.execute(
        select(func.count()).select_from(filtered_subquery)
    ).scalar_one()

    effective_page_size = max(int(page_size or ACTIVITY_PAGE_SIZE), 1)
    total_pages = max((total_count + effective_page_size - 1) // effective_page_size, 1) if total_count else 1
    current_page = min(requested_page, total_pages) if total_count else 1
    offset = (current_page - 1) * effective_page_size

    type_sort_rank = case(
        (filtered_subquery.c.type_key == "status", 0),
        else_=1,
    )
    if sort == "oldest":
        order_by = (
            filtered_subquery.c.changed_at.asc(),
            filtered_subquery.c.venue_name.asc(),
            filtered_subquery.c.item_name.asc(),
            type_sort_rank.asc(),
            filtered_subquery.c.event_id.asc(),
        )
    elif sort == "venue":
        order_by = (
            filtered_subquery.c.venue_name.asc(),
            filtered_subquery.c.item_name.asc(),
            filtered_subquery.c.changed_at.desc(),
            type_sort_rank.asc(),
            filtered_subquery.c.event_id.desc(),
        )
    elif sort == "item":
        order_by = (
            filtered_subquery.c.item_name.asc(),
            filtered_subquery.c.venue_name.asc(),
            filtered_subquery.c.changed_at.desc(),
            type_sort_rank.asc(),
            filtered_subquery.c.event_id.desc(),
        )
    elif sort == "actor":
        order_by = (
            filtered_subquery.c.actor_name.asc(),
            filtered_subquery.c.changed_at.desc(),
            filtered_subquery.c.venue_name.asc(),
            filtered_subquery.c.item_name.asc(),
            type_sort_rank.asc(),
            filtered_subquery.c.event_id.desc(),
        )
    elif sort == "type":
        order_by = (
            type_sort_rank.asc(),
            filtered_subquery.c.changed_at.desc(),
            filtered_subquery.c.venue_name.asc(),
            filtered_subquery.c.item_name.asc(),
            filtered_subquery.c.event_id.desc(),
        )
    else:
        order_by = (
            filtered_subquery.c.changed_at.desc(),
            filtered_subquery.c.venue_name.asc(),
            filtered_subquery.c.item_name.asc(),
            type_sort_rank.asc(),
            filtered_subquery.c.event_id.desc(),
        )

    page_rows = db.session.execute(
        select(filtered_subquery).order_by(*order_by).offset(offset).limit(effective_page_size)
    ).mappings().all()
    serialized_rows = [serialize_activity_row(row) for row in page_rows]

    showing_from = offset + 1 if total_count else 0
    showing_to = min(offset + len(serialized_rows), total_count)
    return {
        "rows": serialized_rows,
        "total_count": total_count,
        "page_size": effective_page_size,
        "current_page": current_page,
        "total_pages": total_pages,
        "has_prev": current_page > 1,
        "has_next": current_page < total_pages,
        "showing_from": showing_from,
        "showing_to": showing_to,
    }


def parse_activity_request_args(args):
    activity_search = (args.get("activity_q", "") or "").strip()
    activity_type = normalize_activity_type(args.get("activity_type", "all"))
    activity_sort = normalize_activity_sort(args.get("activity_sort", "newest"))
    activity_actor_user_id = normalize_activity_actor_user_id(
        args.get("activity_actor_user_id", "")
    )
    activity_start_date = parse_activity_date(args.get("activity_start", ""))
    activity_end_date = parse_activity_date(args.get("activity_end", ""))
    activity_start_date, activity_end_date = normalize_activity_date_range(
        activity_start_date, activity_end_date
    )
    try:
        activity_page = max(int(args.get("activity_page", "1")), 1)
    except ValueError:
        activity_page = 1

    return {
        "search": activity_search,
        "type": activity_type,
        "sort": activity_sort,
        "actor_user_id": activity_actor_user_id,
        "start_date": activity_start_date,
        "end_date": activity_end_date,
        "page": activity_page,
    }


def build_activity_base_params(activity_filters):
    params = {"tab": "activity"}
    if activity_filters["search"]:
        params["activity_q"] = activity_filters["search"]
    if activity_filters["actor_user_id"] is not None:
        params["activity_actor_user_id"] = activity_filters["actor_user_id"]
    if activity_filters["type"] != "all":
        params["activity_type"] = activity_filters["type"]
    if activity_filters["sort"] != "newest":
        params["activity_sort"] = activity_filters["sort"]
    if activity_filters["start_date"]:
        params["activity_start"] = activity_filters["start_date"].isoformat()
    if activity_filters["end_date"]:
        params["activity_end"] = activity_filters["end_date"].isoformat()
    return params


def build_activity_pagination(activity_page_data, activity_filters):
    activity_base_params = build_activity_base_params(activity_filters)

    activity_prev_url = None
    if activity_page_data["has_prev"]:
        activity_prev_url = url_for(
            "main.dashboard",
            **activity_base_params,
            activity_page=activity_page_data["current_page"] - 1,
        )

    activity_next_url = None
    if activity_page_data["has_next"]:
        activity_next_url = url_for(
            "main.dashboard",
            **activity_base_params,
            activity_page=activity_page_data["current_page"] + 1,
        )

    return {
        "page_size": activity_page_data["page_size"],
        "current_page": activity_page_data["current_page"],
        "total_pages": activity_page_data["total_pages"],
        "total_count": activity_page_data["total_count"],
        "showing_from": activity_page_data["showing_from"],
        "showing_to": activity_page_data["showing_to"],
        "has_prev": activity_page_data["has_prev"],
        "has_next": activity_page_data["has_next"],
        "prev_url": activity_prev_url,
        "next_url": activity_next_url,
    }


def serialize_activity_filters(activity_filters):
    return {
        "search": activity_filters["search"],
        "type": activity_filters["type"],
        "sort": activity_filters["sort"],
        "actor_user_id": activity_filters["actor_user_id"],
        "start_date": activity_filters["start_date"].isoformat() if activity_filters["start_date"] else "",
        "end_date": activity_filters["end_date"].isoformat() if activity_filters["end_date"] else "",
    }


def ensure_utc(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def format_updated_label(value, stale_threshold=None):
    updated_at = ensure_utc(value)
    if updated_at is None:
        return {
            "text": "No updates yet",
            "is_missing": True,
            "is_stale": True,
        }

    now = datetime.now(timezone.utc)
    delta = max(now - updated_at, timedelta(0))
    total_seconds = int(delta.total_seconds())
    if total_seconds < 60:
        relative = "Updated just now"
    elif total_seconds < 3600:
        minutes = total_seconds // 60
        relative = f"Updated {minutes}m ago"
    elif total_seconds < 86400:
        hours = total_seconds // 3600
        relative = f"Updated {hours}h ago"
    else:
        days = total_seconds // 86400
        relative = f"Updated {days}d ago"

    return {
        "text": relative,
        "is_missing": False,
        "is_stale": delta >= timedelta(days=max(int(stale_threshold or 2), 1)),
    }


def build_venue_last_updated_map(venue_ids):
    if not venue_ids:
        return {}

    last_check_map = {
        row.venue_id: row.last_check_at
        for row in (
            db.session.query(
                Check.venue_id.label("venue_id"),
                func.max(Check.created_at).label("last_check_at"),
            )
            .filter(Check.venue_id.in_(venue_ids))
            .group_by(Check.venue_id)
            .all()
        )
    }
    last_count_map = {
        row.venue_id: row.last_count_at
        for row in (
            db.session.query(
                CountSession.venue_id.label("venue_id"),
                func.max(CountSession.created_at).label("last_count_at"),
            )
            .filter(CountSession.venue_id.in_(venue_ids))
            .group_by(CountSession.venue_id)
            .all()
        )
    }

    output = {}
    for venue_id in venue_ids:
        last_check_at = ensure_utc(last_check_map.get(venue_id))
        last_count_at = ensure_utc(last_count_map.get(venue_id))
        if last_check_at and last_count_at:
            output[venue_id] = max(last_check_at, last_count_at)
        else:
            output[venue_id] = last_check_at or last_count_at
    return output


def build_recent_venue_activity_rows(venue_id, limit=20):
    max_rows = max(int(limit or 0), 1)
    status_actor_name = activity_actor_name_expr(User.display_name, User.email)
    previous_status = func.lag(CheckLine.status).over(
        partition_by=(Check.venue_id, CheckLine.item_id),
        order_by=(Check.created_at.asc(), Check.id.asc()),
    )
    status_inner = (
        select(
            literal("status").label("type_key"),
            Check.id.label("event_id"),
            Check.created_at.label("changed_at"),
            Venue.name.label("venue_name"),
            Item.name.label("item_name"),
            status_actor_name.label("actor_name"),
            previous_status.label("old_status_key"),
            CheckLine.status.label("new_status_key"),
            cast(literal(None), Integer).label("old_raw_count"),
            cast(literal(None), Integer).label("new_raw_count"),
        )
        .select_from(Check)
        .join(Venue, Venue.id == Check.venue_id)
        .join(CheckLine, CheckLine.check_id == Check.id)
        .join(Item, Item.id == CheckLine.item_id)
        .outerjoin(User, User.id == Check.user_id)
        .where(Check.venue_id == venue_id)
        .subquery()
    )
    status_events = select(status_inner).where(
        or_(
            and_(
                status_inner.c.old_status_key.is_(None),
                status_inner.c.new_status_key != "not_checked",
            ),
            status_inner.c.old_status_key != status_inner.c.new_status_key,
        )
    )

    count_actor_name = activity_actor_name_expr(User.display_name, User.email)
    previous_raw_count = func.lag(CountLine.raw_count).over(
        partition_by=(CountSession.venue_id, CountLine.item_id),
        order_by=(CountSession.created_at.asc(), CountSession.id.asc()),
    )
    count_inner = (
        select(
            literal("raw_count").label("type_key"),
            CountSession.id.label("event_id"),
            CountSession.created_at.label("changed_at"),
            Venue.name.label("venue_name"),
            Item.name.label("item_name"),
            count_actor_name.label("actor_name"),
            cast(literal(None), String).label("old_status_key"),
            cast(literal(None), String).label("new_status_key"),
            previous_raw_count.label("old_raw_count"),
            CountLine.raw_count.label("new_raw_count"),
        )
        .select_from(CountSession)
        .join(Venue, Venue.id == CountSession.venue_id)
        .join(CountLine, CountLine.count_session_id == CountSession.id)
        .join(Item, Item.id == CountLine.item_id)
        .outerjoin(User, User.id == CountSession.user_id)
        .where(CountSession.venue_id == venue_id)
        .subquery()
    )
    count_events = select(count_inner).where(
        or_(
            count_inner.c.old_raw_count.is_(None),
            count_inner.c.old_raw_count != count_inner.c.new_raw_count,
        )
    )

    activity_events = union_all(status_events, count_events).subquery()
    rows = db.session.execute(
        select(activity_events)
        .order_by(activity_events.c.changed_at.desc(), activity_events.c.event_id.desc())
        .limit(max_rows)
    ).mappings().all()
    return [serialize_activity_row(row) for row in rows]


def build_venue_rows(include_inactive=False):
    global_stale_threshold_days = get_default_stale_threshold_days()
    q = Venue.query
    if not include_inactive:
        q = q.filter(Venue.active == True)
    venues = q.order_by(Venue.name.asc()).all()
    if not venues:
        return []

    venue_ids = [v.id for v in venues]
    tracked_item_rows = (
        db.session.query(
            VenueItem.venue_id.label("venue_id"),
            VenueItem.item_id.label("item_id"),
            Item.tracking_mode.label("tracking_mode"),
        )
        .join(Item, Item.id == VenueItem.item_id)
        .filter(
            VenueItem.venue_id.in_(venue_ids),
            VenueItem.active == True,
            Item.active == True,
            Item.is_group_parent == False,
        )
        .all()
    )
    tracked_totals = {}
    tracked_item_ids = set()
    for row in tracked_item_rows:
        tracked_totals[row.venue_id] = int(tracked_totals.get(row.venue_id, 0) or 0) + 1
        tracked_item_ids.add(row.item_id)

    notes_count_map = {
        row.venue_id: row.notes_count
        for row in (
            db.session.query(
                VenueNote.venue_id.label("venue_id"),
                func.count(VenueNote.id).label("notes_count"),
            )
            .filter(VenueNote.venue_id.in_(venue_ids))
            .group_by(VenueNote.venue_id)
            .all()
        )
    }

    latest_status_counts = {}
    latest_status_detail_counts = {}
    latest_singleton_current_totals = {}
    latest_quantity_count_totals = {}
    latest_status_by_pair = {}
    latest_count_by_pair = {}
    if tracked_item_ids:
        tracked_item_id_list = sorted(tracked_item_ids)
        latest_status_by_pair = build_latest_status_signal_map(
            venue_ids=venue_ids,
            item_ids=tracked_item_id_list,
        )
        latest_count_by_pair = build_latest_count_signal_map(
            venue_ids=venue_ids,
            item_ids=tracked_item_id_list,
        )

    for row in tracked_item_rows:
        counts = latest_status_counts.setdefault(
            row.venue_id,
            {"good": 0, "ok": 0, "low": 0, "out": 0, "not_checked": 0},
        )
        status_signal = latest_status_by_pair.get((row.venue_id, row.item_id))
        if row.tracking_mode == "singleton_asset":
            normalized_status = (
                normalize_singleton_status(status_signal["status"])
                if status_signal
                else "not_checked"
            )
        else:
            normalized_status = normalize_status(status_signal["status"]) if status_signal else "not_checked"

        counts[normalized_status] += 1

        if normalized_status in {"low", "out"}:
            detail_counts = latest_status_detail_counts.setdefault(row.venue_id, build_status_detail_counts())
            suffix = "singleton" if row.tracking_mode == "singleton_asset" else "quantity"
            detail_counts[f"{normalized_status}_{suffix}"] += 1

        if row.tracking_mode == "singleton_asset":
            if normalized_status in {"good", "low"}:
                latest_singleton_current_totals[row.venue_id] = (
                    int(latest_singleton_current_totals.get(row.venue_id, 0) or 0) + 1
                )
            continue

        count_signal = latest_count_by_pair.get((row.venue_id, row.item_id))
        if count_signal and count_signal["raw_count"] is not None:
            latest_quantity_count_totals[row.venue_id] = (
                int(latest_quantity_count_totals.get(row.venue_id, 0) or 0)
                + int(count_signal["raw_count"] or 0)
            )

    par_value_expr = func.coalesce(VenueItem.expected_qty, Item.default_par_level)
    par_totals_map = {
        row.venue_id: {
            "total_par": int(row.total_par or 0),
            "par_item_count": int(row.par_item_count or 0),
        }
        for row in (
            db.session.query(
                VenueItem.venue_id.label("venue_id"),
                func.coalesce(
                    func.sum(
                        case(
                            (par_value_expr.is_(None), 0),
                            else_=par_value_expr,
                        )
                    ),
                    0,
                ).label("total_par"),
                func.sum(
                    case(
                        (par_value_expr.is_(None), 0),
                        else_=1,
                    )
                ).label("par_item_count"),
            )
            .join(Item, Item.id == VenueItem.item_id)
            .filter(
                VenueItem.venue_id.in_(venue_ids),
                VenueItem.active == True,
                Item.active == True,
                Item.is_group_parent == False,
            )
            .group_by(VenueItem.venue_id)
            .all()
        )
    }

    last_updated_map = build_venue_last_updated_map(venue_ids)
    venue_rows = []

    for v in venues:
        total_tracked = tracked_totals.get(v.id, 0)
        counts = {"good": 0, "ok": 0, "low": 0, "out": 0, "not_checked": 0}
        detail_counts = latest_status_detail_counts.get(v.id, build_status_detail_counts()).copy()
        notes_count = int(notes_count_map.get(v.id, 0) or 0)
        last_updated_at = last_updated_map.get(v.id)
        venue_stale_threshold = resolve_effective_stale_threshold_days(
            venue_stale_threshold_days=v.stale_threshold_days,
            global_stale_threshold_days=global_stale_threshold_days,
        )
        freshness = format_updated_label(
            last_updated_at,
            stale_threshold=venue_stale_threshold.value,
        )

        if total_tracked == 0:
            badge = {
                "key": "not_checked",
                "text": "Not Checked",
                "icon_class": "bi-dash-circle",
            }
            tooltip = "No items tracked."
            venue_rows.append(
                {
                    "venue": v,
                    "badge": badge,
                    "tooltip": tooltip,
                    "counts": counts.copy(),
                    "notes_count": notes_count,
                    "total_tracked": 0,
                    "current_total_count": 0,
                    "total_par_count": None,
                    "attention": None,
                    "last_updated_at": last_updated_at,
                    "last_updated_text": (
                        format_activity_timestamp(last_updated_at) if last_updated_at else "No updates yet"
                    ),
                    "freshness": freshness,
                }
            )
            continue

        venue_status_counts = latest_status_counts.get(v.id)
        if venue_status_counts:
            counts = venue_status_counts.copy()
        else:
            counts["not_checked"] = total_tracked

        badge = build_overall_status_badge(total_tracked, counts, detail_counts)
        quantity_current_total = latest_quantity_count_totals.get(v.id, 0)
        singleton_current_total = latest_singleton_current_totals.get(v.id, 0)
        current_total_count = int(quantity_current_total + singleton_current_total)
        par_meta = par_totals_map.get(v.id, {"total_par": 0, "par_item_count": 0})
        total_par_count = par_meta["total_par"] if par_meta["par_item_count"] > 0 else None

        operational_issue_count = int(counts["low"] + counts["out"])
        stale_follow_up = bool(freshness["is_stale"])
        attention_count = operational_issue_count + (1 if stale_follow_up else 0)
        attention = None
        if attention_count > 0:
            if counts["out"] > 0:
                tone = "danger"
                icon_class = "bi-exclamation-octagon-fill"
            elif operational_issue_count > 0:
                tone = "warning"
                icon_class = "bi-exclamation-triangle-fill"
            else:
                tone = "warning"
                icon_class = "bi-clock-history"

            if operational_issue_count > 0 and stale_follow_up:
                label = (
                    f"{attention_count} alert"
                    if attention_count == 1
                    else f"{attention_count} alerts"
                )
            elif operational_issue_count > 0:
                label = (
                    f"{operational_issue_count} needs attention"
                    if operational_issue_count == 1
                    else f"{operational_issue_count} need attention"
                )
            else:
                label = "Stale updates"

            attention = {
                "count": attention_count,
                "label": label,
                "tone": tone,
                "icon_class": icon_class,
            }

        tooltip = (
            f"Total tracked: {total_tracked} | "
            f"Good: {counts['good']} | OK: {counts['ok']} | "
            f"Low: {counts['low']} | Out: {counts['out']} | "
            f"Not checked: {counts['not_checked']}"
        )

        venue_rows.append(
            {
                "venue": v,
                "badge": badge,
                "tooltip": tooltip,
                "counts": counts.copy(),
                "notes_count": notes_count,
                "total_tracked": total_tracked,
                "current_total_count": current_total_count,
                "total_par_count": total_par_count,
                "attention": attention,
                "last_updated_at": last_updated_at,
                "last_updated_text": (
                    format_activity_timestamp(last_updated_at) if last_updated_at else "No updates yet"
                ),
                "freshness": freshness,
            }
        )

    return venue_rows


def parse_selected_ids(raw_values):
    selected_ids = []
    for raw_value in raw_values or []:
        value = (raw_value or "").strip()
        if not value or not value.isdigit():
            continue
        parsed = int(value)
        if parsed not in selected_ids:
            selected_ids.append(parsed)
    return selected_ids


def fetch_trackable_items_for_setup():
    return (
        Item.query.options(selectinload(Item.parent_item))
        .filter(
            Item.active == True,
            Item.is_group_parent == False,
        )
        .order_by(Item.name.asc(), Item.id.asc())
        .all()
    )


def build_venue_create_form_values(source=None):
    source = source or {}
    return {
        "name": (source.get("name") or "").strip(),
        "is_core": "true" if (source.get("is_core") or "false") == "true" else "false",
        "stale_threshold_days": (source.get("stale_threshold_days") or "").strip(),
        "setup_mode": (source.get("setup_mode") or "empty").strip().lower(),
        "copy_source_venue_id": (source.get("copy_source_venue_id") or "").strip(),
        "selected_item_ids": parse_selected_ids(source.getlist("item_ids")) if hasattr(source, "getlist") else [],
    }


def build_venue_setup_rows(trackable_items, selected_item_ids=None):
    selected_item_id_set = set(selected_item_ids or [])
    rows = []
    for item in trackable_items:
        rows.append(
            {
                "id": item.id,
                "name": item.name,
                "parent_name": item.parent_item.name if item.parent_item else None,
                "tracking_mode": item.tracking_mode,
                "item_category": item.item_category or item.item_type,
                "default_par_level": item.default_par_level,
                "is_selected": item.id in selected_item_id_set,
            }
        )
    return rows


def serialize_restock_row(row, next_path):
    latest_check_at = row["latest_check_at"]
    return {
        "venue_id": row["venue_id"],
        "venue_name": row["venue_name"],
        "item_id": row["item_id"],
        "item_name": row["item_name"],
        "parent_name": row.get("parent_name"),
        "tracking_mode": row.get("tracking_mode", "quantity"),
        "item_category": row.get("item_category"),
        "setup_group_code": row.get("setup_group_code"),
        "setup_group_label": row.get("setup_group_label"),
        "setup_group_display": row.get("setup_group_display"),
        "latest_check_ts": latest_check_at.timestamp() if latest_check_at else None,
        "latest_check_text": (
            latest_check_at.strftime("%Y-%m-%d %I:%M %p") if latest_check_at else "No check yet"
        ),
        "latest_check_missing": latest_check_at is None,
        "raw_count": row.get("raw_count"),
        "par_value": row.get("par_value"),
        "status": row["status"],
        "count_state": row.get("count_state"),
        "quick_check_mode": row.get("quick_check_mode", "status"),
        "quick_check_url": url_for(
            "venue_items.quick_check",
            venue_id=row["venue_id"],
            focus_item_id=row["item_id"],
            mode=row.get("quick_check_mode", "status"),
            next=next_path,
        ),
    }


@main_bp.route("/")
def home():
    return redirect(url_for("main.dashboard"))


@main_bp.route("/help")
@roles_required("viewer", "staff", "admin")
def help_page():
    return render_template("help.html")


@main_bp.route("/dashboard")
@roles_required("viewer", "staff", "admin")
def dashboard():
    requested_tab = request.args.get("tab")
    active_tab = requested_tab or "venues"
    if active_tab not in {"venues", "restocking", "activity"}:
        active_tab = "venues"

    activity_filters = parse_activity_request_args(request.args)

    restock_status_submitted = "restock_status_submitted" in request.args
    restock_item_submitted = "restock_item_submitted" in request.args
    restock_venue_submitted = "restock_venue_submitted" in request.args

    requested_statuses = [normalize_status(s) for s in request.args.getlist("restock_status")]
    restock_statuses = []
    for status_key in requested_statuses:
        if status_key in RESTOCK_STATUS_META and status_key not in restock_statuses:
            restock_statuses.append(status_key)
    if not restock_status_submitted:
        restock_statuses = list(RESTOCK_STATUS_META.keys())

    restock_item_ids = []
    for raw_item_id in request.args.getlist("restock_item_id"):
        if raw_item_id.isdigit():
            item_id = int(raw_item_id)
            if item_id not in restock_item_ids:
                restock_item_ids.append(item_id)

    restock_venue_ids = []
    for raw_venue_id in request.args.getlist("restock_venue_id"):
        if raw_venue_id.isdigit():
            venue_id = int(raw_venue_id)
            if venue_id not in restock_venue_ids:
                restock_venue_ids.append(venue_id)

    restock_search = (request.args.get("restock_search", "") or "").strip()
    restock_sort = normalize_restock_sort(request.args.get("restock_sort", "status_priority"))
    restock_mode = normalize_restock_mode(request.args.get("restock_mode"), "status")

    restock_params_seen = any(
        k in request.args
        for k in (
            "restock_status_submitted",
            "restock_item_submitted",
            "restock_venue_submitted",
            "restock_status",
            "restock_item_id",
            "restock_venue_id",
            "restock_search",
            "restock_sort",
            "restock_mode",
        )
    )
    if restock_params_seen and requested_tab is None:
        active_tab = "restocking"

    activity_params_seen = any(
        k in request.args
        for k in (
            "activity_q",
            "activity_actor_user_id",
            "activity_type",
            "activity_sort",
            "activity_start",
            "activity_end",
            "activity_page",
        )
    )
    if activity_params_seen and requested_tab is None:
        active_tab = "activity"

    should_load_activity = active_tab == "activity" or activity_params_seen
    if should_load_activity:
        activity_page_data = build_activity_page(
            search=activity_filters["search"],
            activity_type=activity_filters["type"],
            actor_user_id=activity_filters["actor_user_id"],
            start_date=activity_filters["start_date"],
            end_date=activity_filters["end_date"],
            sort=activity_filters["sort"],
            page=activity_filters["page"],
        )
        activity_rows = activity_page_data["rows"]
        activity_pagination = build_activity_pagination(activity_page_data, activity_filters)
    else:
        activity_rows = []
        activity_pagination = {
            "page_size": ACTIVITY_PAGE_SIZE,
            "current_page": 1,
            "total_pages": 1,
            "total_count": 0,
            "showing_from": 0,
            "showing_to": 0,
            "has_prev": False,
            "has_next": False,
            "prev_url": None,
            "next_url": None,
        }

    initial_restock_page = build_restock_rows(
        statuses=restock_statuses if restock_status_submitted else None,
        item_ids=restock_item_ids if restock_item_submitted else None,
        venue_ids=restock_venue_ids if restock_venue_submitted else None,
        search=restock_search,
        sort=restock_sort,
        mode=restock_mode,
        limit=RESTOCK_PAGE_SIZE,
        offset=0,
    )
    restock_rows = initial_restock_page["rows"]
    restock_total_count = initial_restock_page["total_count"]
    restock_has_more = initial_restock_page["has_more"]

    restock_item_rows = (
        db.session.query(Item.id, Item.name)
        .join(VenueItem, VenueItem.item_id == Item.id)
        .join(Venue, Venue.id == VenueItem.venue_id)
        .filter(
            Item.active == True,
            VenueItem.active == True,
            Venue.active == True,
            Item.is_group_parent == False,
        )
        .group_by(Item.id, Item.name)
        .order_by(Item.name.asc())
        .all()
    )
    restock_items = [{"id": row[0], "name": row[1]} for row in restock_item_rows]

    restock_venue_rows = (
        db.session.query(Venue.id, Venue.name)
        .join(VenueItem, VenueItem.venue_id == Venue.id)
        .join(Item, Item.id == VenueItem.item_id)
        .filter(
            Venue.active == True,
            VenueItem.active == True,
            Item.active == True,
            Item.is_group_parent == False,
        )
        .group_by(Venue.id, Venue.name)
        .order_by(Venue.name.asc())
        .all()
    )
    restock_venues = [{"id": row[0], "name": row[1]} for row in restock_venue_rows]
    restock_active_filter_count = 0
    if restock_status_submitted and set(restock_statuses) != set(RESTOCK_STATUS_META.keys()):
        restock_active_filter_count += 1
    if restock_item_submitted and len(restock_item_ids) != len(restock_items):
        restock_active_filter_count += 1
    if restock_venue_submitted and len(restock_venue_ids) != len(restock_venues):
        restock_active_filter_count += 1
    restock_filter_counts = {
        "statuses": {
            "selected": len(restock_statuses),
            "total": len(RESTOCK_STATUS_META),
        },
        "items": {
            "selected": len(restock_item_ids) if restock_item_submitted else len(restock_items),
            "total": len(restock_items),
        },
        "venues": {
            "selected": len(restock_venue_ids) if restock_venue_submitted else len(restock_venues),
            "total": len(restock_venues),
        },
    }

    return render_template(
        "dashboard.html",
        venue_rows=build_venue_rows(),
        active_tab=active_tab,
        restock_rows=restock_rows,
        restock_items=restock_items,
        restock_venues=restock_venues,
        restock_status_options=RESTOCK_STATUS_META,
        restock_status_submitted=restock_status_submitted,
        restock_item_submitted=restock_item_submitted,
        restock_venue_submitted=restock_venue_submitted,
        restock_active_filter_count=restock_active_filter_count,
        restock_filter_counts=restock_filter_counts,
        restock_page_size=RESTOCK_PAGE_SIZE,
        restock_total_count=restock_total_count,
        restock_has_more=restock_has_more,
        activity_rows=activity_rows,
        activity_loaded=should_load_activity,
        activity_filters=serialize_activity_filters(activity_filters),
        activity_actor_filter_user=(
            db.session.get(User, activity_filters["actor_user_id"])
            if activity_filters["actor_user_id"] is not None
            else None
        ),
        activity_pagination=activity_pagination,
        restock_filters={
            "statuses": restock_statuses,
            "item_ids": restock_item_ids,
            "venue_ids": restock_venue_ids,
            "search": restock_search,
            "sort": restock_sort,
            "mode": restock_mode,
        },
    )


@main_bp.route("/dashboard/activity_rows")
@roles_required("viewer", "staff", "admin")
def dashboard_activity_rows():
    activity_filters = parse_activity_request_args(request.args)
    activity_page_data = build_activity_page(
        search=activity_filters["search"],
        activity_type=activity_filters["type"],
        actor_user_id=activity_filters["actor_user_id"],
        start_date=activity_filters["start_date"],
        end_date=activity_filters["end_date"],
        sort=activity_filters["sort"],
        page=activity_filters["page"],
    )
    return jsonify(
        {
            "rows": activity_page_data["rows"],
            "filters": serialize_activity_filters(activity_filters),
            "pagination": build_activity_pagination(activity_page_data, activity_filters),
        }
    )


@main_bp.route("/dashboard/restocking_rows")
@roles_required("viewer", "staff", "admin")
def dashboard_restocking_rows():
    restock_status_submitted = "restock_status_submitted" in request.args
    restock_item_submitted = "restock_item_submitted" in request.args
    restock_venue_submitted = "restock_venue_submitted" in request.args

    requested_statuses = [normalize_status(s) for s in request.args.getlist("restock_status")]
    restock_statuses = []
    for status_key in requested_statuses:
        if status_key in RESTOCK_STATUS_META and status_key not in restock_statuses:
            restock_statuses.append(status_key)
    if not restock_status_submitted:
        restock_statuses = list(RESTOCK_STATUS_META.keys())

    restock_item_ids = []
    for raw_item_id in request.args.getlist("restock_item_id"):
        if raw_item_id.isdigit():
            item_id = int(raw_item_id)
            if item_id not in restock_item_ids:
                restock_item_ids.append(item_id)

    restock_venue_ids = []
    for raw_venue_id in request.args.getlist("restock_venue_id"):
        if raw_venue_id.isdigit():
            venue_id = int(raw_venue_id)
            if venue_id not in restock_venue_ids:
                restock_venue_ids.append(venue_id)

    restock_search = (request.args.get("restock_search", "") or "").strip()
    restock_sort = normalize_restock_sort(request.args.get("restock_sort", "status_priority"))
    restock_mode = normalize_restock_mode(request.args.get("restock_mode"), "status")

    raw_limit = request.args.get("limit", str(RESTOCK_PAGE_SIZE))
    raw_offset = request.args.get("offset", "0")
    try:
        limit = max(1, min(int(raw_limit), 200))
    except ValueError:
        limit = RESTOCK_PAGE_SIZE
    try:
        offset = max(int(raw_offset), 0)
    except ValueError:
        offset = 0

    page = build_restock_rows(
        statuses=restock_statuses if restock_status_submitted else None,
        item_ids=restock_item_ids if restock_item_submitted else None,
        venue_ids=restock_venue_ids if restock_venue_submitted else None,
        search=restock_search,
        sort=restock_sort,
        mode=restock_mode,
        limit=limit,
        offset=offset,
    )
    next_path = request.args.get("next") or url_for("main.dashboard", tab="restocking")
    return jsonify(
        {
            "rows": [serialize_restock_row(row, next_path) for row in page["rows"]],
            "total_count": page["total_count"],
            "offset": offset,
            "limit": limit,
            "has_more": page["has_more"],
        }
    )


@main_bp.route("/venues/create", methods=["GET", "POST"])
@roles_required("admin")
def create_venue():
    trackable_items = fetch_trackable_items_for_setup()
    copy_source_venues = Venue.query.order_by(Venue.name.asc(), Venue.id.asc()).all()
    form_values = build_venue_create_form_values()
    tracking_rows = build_venue_setup_rows(trackable_items, form_values["selected_item_ids"])

    if request.method == "POST":
        form_values = build_venue_create_form_values(request.form)
        tracking_rows = build_venue_setup_rows(trackable_items, form_values["selected_item_ids"])
        setup_mode = form_values["setup_mode"]
        if setup_mode not in {"empty", "copy", "manual"}:
            setup_mode = "empty"
            form_values["setup_mode"] = setup_mode

        try:
            stale_threshold_days = normalize_optional_threshold_days(
                request.form.get("stale_threshold_days"),
                field_label="Venue stale threshold",
            )
        except InventoryRuleError as exc:
            flash(str(exc), "error")
        else:
            name = form_values["name"]
            if not name:
                flash("Venue name is required.", "error")
            elif Venue.query.filter_by(name=name).first():
                flash("That venue already exists.", "error")
            elif setup_mode == "copy" and not form_values["copy_source_venue_id"].isdigit():
                flash("Choose a source venue to copy from.", "error")
            else:
                venue = Venue(
                    name=name,
                    is_core=form_values["is_core"] == "true",
                    active=True,
                    stale_threshold_days=stale_threshold_days,
                )
                db.session.add(venue)
                db.session.flush()

                log_inventory_admin_event(
                    "venue_created",
                    actor=current_user,
                    subject_type="venue",
                    subject_id=venue.id,
                    subject_label=venue.name,
                    details={"setup_mode": setup_mode},
                )

                if setup_mode == "copy":
                    source_venue = Venue.query.get_or_404(int(form_values["copy_source_venue_id"]))
                    summary = copy_venue_tracking_setup(
                        source_venue=source_venue,
                        target_venue=venue,
                    )
                    log_inventory_admin_event(
                        "venue_tracking_copied",
                        actor=current_user,
                        subject_type="venue",
                        subject_id=venue.id,
                        subject_label=venue.name,
                        details=summary,
                    )
                elif setup_mode == "manual":
                    summary = sync_venue_tracked_items(
                        venue=venue,
                        selected_item_ids=form_values["selected_item_ids"],
                        par_overrides={},
                    )
                    if any(summary.values()):
                        log_inventory_admin_event(
                            "venue_tracking_updated",
                            actor=current_user,
                            subject_type="venue",
                            subject_id=venue.id,
                            subject_label=venue.name,
                            details=summary,
                        )

                db.session.commit()
                flash("Venue created.", "success")
                return redirect(
                    url_for(
                        "venue_settings.settings",
                        venue_id=venue.id,
                        next=url_for("main.venues"),
                    )
                )

    return render_template(
        "venues/create.html",
        form_values=form_values,
        tracking_rows=tracking_rows,
        copy_source_venues=copy_source_venues,
        global_stale_threshold_days=get_default_stale_threshold_days(),
    )


@main_bp.route("/venues", methods=["GET", "POST"])
@roles_required("viewer", "staff", "admin")
def venues():
    if request.method == "POST":
        if not current_user.has_role("admin"):
            flash("Only admins can create venues.", "error")
            return redirect(url_for("main.venues"))

        name = request.form.get("name", "").strip()

        if not name:
            flash("Venue name is required.", "error")
            return redirect(url_for("main.venues"))

        exists = Venue.query.filter_by(name=name).first()
        if exists:
            flash("That venue already exists.", "error")
            return redirect(url_for("main.venues"))

        is_core = (request.form.get("is_core") == "true")
        v = Venue(name=name, is_core=is_core, active=True)
        db.session.add(v)
        db.session.commit()

        flash("Venue added!", "success")
        return redirect(url_for("main.venues"))

    return render_template("venues/list.html", venue_rows=build_venue_rows(include_inactive=True))


@main_bp.route("/venues/<int:venue_id>", methods=["GET", "POST"])
@roles_required("viewer", "staff", "admin")
def venue_detail(venue_id):
    venue = Venue.query.get_or_404(venue_id)
    active_profile_tab = (request.args.get("profile_tab") or request.form.get("profile_tab") or "overview").strip().lower()
    if active_profile_tab == "details":
        active_profile_tab = "overview"
    if active_profile_tab not in {"overview", "notes", "activity", "files"}:
        active_profile_tab = "overview"
    next_path = normalize_next_path(
        request.args.get("next") or request.form.get("next"),
        url_for("main.venues"),
    )
    valid_note_item_ids = load_valid_venue_note_item_ids(venue.id)
    active_note_item_id = normalize_venue_note_item_id(
        request.args.get("note_item_id") or request.form.get("note_filter_item_id"),
        valid_note_item_ids,
    )
    note_search_query = normalize_note_search_query(
        request.args.get("note_q") or request.form.get("note_q")
    )
    note_kind_filter = normalize_note_kind_filter(
        request.args.get("note_kind") or request.form.get("note_kind")
    )
    note_page = normalize_note_page(
        request.args.get("note_page") or request.form.get("note_page")
    )
    note_focus = normalize_note_focus(
        request.args.get("note_focus") or request.form.get("note_focus")
    )
    submit_profile_tab = (request.form.get("profile_tab") or active_profile_tab).strip().lower()
    if submit_profile_tab == "details":
        submit_profile_tab = "overview"
    if submit_profile_tab not in {"overview", "notes", "activity", "files"}:
        submit_profile_tab = "overview"

    if request.method == "POST":
        action = (request.form.get("action") or "").strip().lower()
        if action in {"create_note", "edit_note", "delete_note"} and not current_user.is_staff:
            flash("Only staff and admins can manage notes.", "error")
            return redirect_to_venue_detail(
                venue.id,
                next_path=next_path,
                profile_tab=submit_profile_tab,
                note_item_id=active_note_item_id,
                note_q=note_search_query,
                note_kind=note_kind_filter,
                note_page=note_page,
            )

        if action == "create_note":
            title = (request.form.get("title") or "").strip()
            body = (request.form.get("body") or "").strip()
            note_item_raw = request.form.get("item_id", "")
            note_item_id = normalize_venue_note_item_id(note_item_raw, valid_note_item_ids)
            if note_item_raw.strip() and note_item_id is None:
                flash("Select a tracked venue item or leave the note as a general venue note.", "error")
                return redirect_to_venue_detail(
                    venue.id,
                    next_path=next_path,
                    profile_tab=submit_profile_tab,
                    note_item_id=active_note_item_id,
                    note_focus="compose",
                    note_q=note_search_query,
                    note_kind=note_kind_filter,
                    note_page=note_page,
                )
            validation_error = validate_note_fields(title, body)
            if validation_error:
                flash(validation_error, "error")
                return redirect_to_venue_detail(
                    venue.id,
                    next_path=next_path,
                    profile_tab=submit_profile_tab,
                    note_item_id=note_item_id if note_item_id is not None else active_note_item_id,
                    note_focus="compose",
                    note_q=note_search_query,
                    note_kind=note_kind_filter,
                    note_page=note_page,
                )

            new_note = VenueNote(
                venue_id=venue.id,
                author_user_id=current_user.id,
                item_id=note_item_id,
                title=title,
                body=body,
            )
            db.session.add(new_note)
            db.session.commit()
            flash("Note added.", "success")
            return redirect_to_venue_detail(
                venue.id,
                next_path=next_path,
                profile_tab=submit_profile_tab,
                note_item_id=note_item_id,
                note_focus="list",
                note_q=note_search_query,
                note_kind=note_kind_filter,
                note_page=1,
            )

        if action in {"edit_note", "delete_note"}:
            note_id_raw = request.form.get("note_id", "")
            note = None
            if note_id_raw.isdigit():
                note = VenueNote.query.filter_by(id=int(note_id_raw), venue_id=venue.id).first()
            if note is None:
                flash("Note not found for this venue.", "error")
                return redirect_to_venue_detail(
                    venue.id,
                    next_path=next_path,
                    profile_tab=submit_profile_tab,
                    note_item_id=active_note_item_id,
                    note_q=note_search_query,
                    note_kind=note_kind_filter,
                    note_page=note_page,
                )

            can_manage = current_user.is_admin or (
                current_user.role == "staff" and note.author_user_id == current_user.id
            )
            if not can_manage:
                flash("You can only edit or delete your own notes.", "error")
                return redirect_to_venue_detail(
                    venue.id,
                    next_path=next_path,
                    profile_tab=submit_profile_tab,
                    note_item_id=active_note_item_id,
                    note_q=note_search_query,
                    note_kind=note_kind_filter,
                    note_page=note_page,
                )

            if action == "edit_note":
                title = (request.form.get("title") or "").strip()
                body = (request.form.get("body") or "").strip()
                note_item_raw = request.form.get("item_id", "")
                note_item_id = normalize_venue_note_item_id(note_item_raw, valid_note_item_ids)
                if note_item_raw.strip() and note_item_id is None:
                    flash("Select a tracked venue item or leave the note as a general venue note.", "error")
                    return redirect_to_venue_detail(
                        venue.id,
                        next_path=next_path,
                        profile_tab=submit_profile_tab,
                        note_item_id=active_note_item_id,
                        note_focus="list",
                        note_q=note_search_query,
                        note_kind=note_kind_filter,
                        note_page=note_page,
                    )
                validation_error = validate_note_fields(title, body)
                if validation_error:
                    flash(validation_error, "error")
                    return redirect_to_venue_detail(
                        venue.id,
                        next_path=next_path,
                        profile_tab=submit_profile_tab,
                        note_item_id=note_item_id if note_item_id is not None else active_note_item_id,
                        note_focus="list",
                        note_q=note_search_query,
                        note_kind=note_kind_filter,
                        note_page=note_page,
                    )

                note.item_id = note_item_id
                note.title = title
                note.body = body
                db.session.commit()
                flash("Note updated.", "success")
                return redirect_to_venue_detail(
                    venue.id,
                    next_path=next_path,
                    profile_tab=submit_profile_tab,
                    note_item_id=note_item_id,
                    note_focus="list",
                    note_q=note_search_query,
                    note_kind=note_kind_filter,
                    note_page=1,
                )

            redirect_note_item_id = (
                active_note_item_id if active_note_item_id is not None else note.item_id
            )
            db.session.delete(note)
            db.session.commit()
            flash("Note deleted.", "success")
            return redirect_to_venue_detail(
                venue.id,
                next_path=next_path,
                profile_tab=submit_profile_tab,
                note_item_id=redirect_note_item_id,
                note_focus="list" if redirect_note_item_id is not None else None,
                note_q=note_search_query,
                note_kind=note_kind_filter,
                note_page=note_page,
            )

    venue_profile = build_venue_profile_view_model(venue.id)
    recent_activity_rows = build_recent_venue_activity_rows(venue.id, limit=20)
    note_item_options = venue_profile["note_item_options"]
    note_item_option_map = {option["id"]: option for option in note_item_options}
    if active_note_item_id not in note_item_option_map:
        active_note_item_id = None
    note_query = (
        db.session.query(
            VenueNote,
            User.display_name.label("author_display_name"),
            User.email.label("author_email"),
            Item.name.label("item_name"),
        )
        .outerjoin(User, User.id == VenueNote.author_user_id)
        .outerjoin(Item, Item.id == VenueNote.item_id)
        .filter(VenueNote.venue_id == venue.id)
    )
    if active_note_item_id is not None:
        note_query = note_query.filter(VenueNote.item_id == active_note_item_id)
    if note_kind_filter == "general":
        note_query = note_query.filter(VenueNote.item_id.is_(None))
    elif note_kind_filter == "tagged":
        note_query = note_query.filter(VenueNote.item_id.is_not(None))
    if note_search_query:
        note_search_text = note_search_query.lower()
        note_query = note_query.filter(
            or_(
                func.lower(VenueNote.title).contains(note_search_text),
                func.lower(VenueNote.body).contains(note_search_text),
                func.lower(func.coalesce(User.display_name, "")).contains(note_search_text),
                func.lower(func.coalesce(User.email, "")).contains(note_search_text),
                func.lower(func.coalesce(Item.name, "")).contains(note_search_text),
            )
        )

    note_total_count = note_query.order_by(None).count()
    note_pagination = build_pagination(note_total_count, note_page, VENUE_NOTES_PAGE_SIZE)
    note_rows = []
    note_query_rows = (
        note_query.order_by(VenueNote.updated_at.desc(), VenueNote.id.desc())
        .offset(note_pagination["offset"])
        .limit(note_pagination["page_size"])
        .all()
    )
    for note, author_display_name, author_email, item_name in note_query_rows:
        created_at = ensure_utc(note.created_at)
        updated_at = ensure_utc(note.updated_at)
        is_edited = bool(
            created_at
            and updated_at
            and (updated_at - created_at) > timedelta(seconds=1)
        )
        effective_at = updated_at if is_edited else created_at
        can_manage = current_user.is_admin or (
            current_user.role == "staff" and note.author_user_id == current_user.id
        )
        author_name = (author_display_name or "").strip() or (author_email or "Unknown user")
        is_item_note = note.item_id is not None
        note_rows.append(
            {
                "id": note.id,
                "item_id": note.item_id,
                "item_name": (item_name or "").strip() or "Tracked item",
                "is_item_note": is_item_note,
                "note_kind_label": "Item note" if is_item_note else "Venue note",
                "title": note.title,
                "body": note.body,
                "author_name": author_name,
                "created_at_text": format_activity_timestamp(created_at) if created_at else "Unknown time",
                "display_time_text": format_activity_timestamp(effective_at) if effective_at else "Unknown time",
                "display_time_label": "Edited" if is_edited else "Created",
                "can_manage": can_manage,
            }
        )

    return render_template(
        "venues/detail.html",
        venue=venue,
        venue_profile=venue_profile,
        back_url=next_path,
        back_label=describe_back_destination(next_path, venue.id),
        recent_activity_rows=recent_activity_rows,
        note_rows=note_rows,
        active_note_item_id=active_note_item_id,
        active_note_item_option=note_item_option_map.get(active_note_item_id),
        note_search_query=note_search_query,
        note_kind_filter=note_kind_filter,
        note_pagination=note_pagination,
        note_focus=note_focus,
        note_title_max_length=NOTE_TITLE_MAX_LENGTH,
        note_body_max_length=NOTE_BODY_MAX_LENGTH,
        active_profile_tab=active_profile_tab,
        venue_inventory_export_base_url=url_for("main.export_venue_inventory", venue_id=venue.id),
        restock_status_options=RESTOCK_STATUS_META,
        update_status_url=url_for("venue_items.quick_check", venue_id=venue.id, next=request.full_path),
    )


@main_bp.post("/venues/<int:venue_id>/notes/inline")
@roles_required("staff", "admin")
def create_venue_note_inline(venue_id):
    venue = Venue.query.get_or_404(venue_id)
    valid_note_items = load_valid_venue_note_items(venue.id)
    note_item_id = normalize_venue_note_item_id(
        request.form.get("item_id"),
        set(valid_note_items),
    )
    if note_item_id is None:
        return (
            jsonify(
                {
                    "error": "Select a tracked venue item before adding a note.",
                    "code": "invalid_item",
                }
            ),
            400,
        )

    title = (request.form.get("title") or "").strip()
    body = (request.form.get("body") or "").strip()
    validation_error = validate_note_fields(title, body)
    if validation_error:
        return jsonify({"error": validation_error, "code": "validation_error"}), 400

    db.session.add(
        VenueNote(
            venue_id=venue.id,
            author_user_id=current_user.id,
            item_id=note_item_id,
            title=title,
            body=body,
        )
    )
    db.session.commit()

    note_count = (
        db.session.query(func.count(VenueNote.id))
        .filter(
            VenueNote.venue_id == venue.id,
            VenueNote.item_id == note_item_id,
        )
        .scalar()
        or 0
    )

    return jsonify(
        {
            "status": "success",
            "message": "Note added.",
            "item_id": note_item_id,
            "item_name": valid_note_items.get(note_item_id, "Tracked item"),
            "note_count": int(note_count),
        }
    )


@main_bp.get("/venues/<int:venue_id>/inventory/export.csv")
@roles_required("viewer", "staff", "admin")
def export_venue_inventory(venue_id):
    venue_profile = build_venue_profile_view_model(venue_id)
    venue = venue_profile["venue"]
    scope = normalize_export_scope(request.args.get("scope"), default=EXPORT_SCOPE_FILTERED)
    requested_filters = normalize_venue_inventory_filters(request.args)
    filters = {
        "q": "",
        "segment": "all",
        "filter": "all",
        "sort": requested_filters["sort"],
    }
    if scope == EXPORT_SCOPE_FILTERED:
        filters = requested_filters

    rows = filter_venue_inventory_rows(venue_profile["item_rows"], filters)
    csv_rows = build_venue_inventory_csv_rows(venue, rows)
    filename = build_venue_inventory_export_filename(venue, scope=scope)
    return build_csv_response(VENUE_INVENTORY_EXPORT_HEADERS, csv_rows, filename)
