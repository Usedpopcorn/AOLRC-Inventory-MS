import re
from datetime import datetime, time, timedelta, timezone

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import current_user
from sqlalchemy import func, and_, or_, select, union_all, literal, cast, Integer, String, case
from app import db
from app.authz import roles_required
from app.models import Venue, VenueItem, Item, Check, CheckLine, CountSession, CountLine, User

main_bp = Blueprint("main", __name__)
RESTOCK_PAGE_SIZE = 50
ACTIVITY_PAGE_SIZE = 50

RESTOCK_STATUS_META = {
    "good": {"text": "Good", "icon_class": "bi-check-circle-fill"},
    "ok": {"text": "OK", "icon_class": "bi-check-circle-fill"},
    "low": {"text": "Low", "icon_class": "bi-exclamation-triangle-fill"},
    "out": {"text": "Out", "icon_class": "bi-x-circle-fill"},
    "not_checked": {"text": "Not Checked", "icon_class": "bi-dash-circle"},
}

ACTIVITY_TYPE_META = {
    "status": {
        "text": "Status",
        "icon_class": "bi-clipboard-check",
        "badge_class": "activity-type-status",
    },
    "raw_count": {
        "text": "Raw Count",
        "icon_class": "bi-123",
        "badge_class": "activity-type-count",
    },
}

ACTIVITY_SORT_OPTIONS = {"newest", "oldest", "venue", "item", "actor", "type"}


def normalize_status(status):
    value = (status or "").strip().lower()
    if value == "-":
        value = "not_checked"
    if value not in RESTOCK_STATUS_META:
        return "not_checked"
    return value


def _word_boundary_match(text, query):
    return bool(re.search(rf"(^|\W){re.escape(query)}", text))


def restock_search_rank(row, search_query):
    if not search_query:
        return 0

    item_text = (row.get("item_name") or "").lower()
    venue_text = (row.get("venue_name") or "").lower()
    status_text = (row.get("status", {}).get("text") or "").lower()

    if item_text.startswith(search_query):
        return 0
    if _word_boundary_match(item_text, search_query):
        return 1
    if search_query in item_text:
        return 2
    if venue_text.startswith(search_query):
        return 3
    if _word_boundary_match(venue_text, search_query):
        return 4
    if search_query in venue_text:
        return 5
    if status_text.startswith(search_query):
        return 6
    if search_query in status_text:
        return 7
    return None


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
        detail_text = f"Initial raw count recorded as {new_raw_count}"
    else:
        old_value_text = str(previous_raw_count)
        detail_text = f"Raw count changed from {previous_raw_count} to {new_raw_count}"

    new_value_text = str(new_raw_count)
    search_text = " ".join(
        [
            "raw count",
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
        "start_date": activity_start_date,
        "end_date": activity_end_date,
        "page": activity_page,
    }


def build_activity_base_params(activity_filters):
    params = {"tab": "activity"}
    if activity_filters["search"]:
        params["activity_q"] = activity_filters["search"]
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
        "start_date": activity_filters["start_date"].isoformat() if activity_filters["start_date"] else "",
        "end_date": activity_filters["end_date"].isoformat() if activity_filters["end_date"] else "",
    }


def build_venue_rows(include_inactive=False):
    q = Venue.query
    if not include_inactive:
        q = q.filter(Venue.active == True)
    venues = q.order_by(Venue.name.asc()).all()
    if not venues:
        return []

    venue_ids = [v.id for v in venues]
    tracked_totals = {
        row.venue_id: row.total_tracked
        for row in (
            db.session.query(
                VenueItem.venue_id.label("venue_id"),
                func.count(VenueItem.item_id).label("total_tracked"),
            )
            .join(Item, Item.id == VenueItem.item_id)
            .filter(
                VenueItem.venue_id.in_(venue_ids),
                VenueItem.active == True,
                Item.active == True,
            )
            .group_by(VenueItem.venue_id)
            .all()
        )
    }

    latest_check_sq = (
        db.session.query(
            Check.venue_id.label("venue_id"),
            func.max(Check.id).label("latest_check_id"),
        )
        .filter(Check.venue_id.in_(venue_ids))
        .group_by(Check.venue_id)
        .subquery()
    )

    latest_status_counts = {}
    for row in (
        db.session.query(
            latest_check_sq.c.venue_id.label("venue_id"),
            CheckLine.status.label("status"),
            func.count(CheckLine.id).label("status_count"),
        )
        .join(CheckLine, CheckLine.check_id == latest_check_sq.c.latest_check_id)
        .join(
            VenueItem,
            and_(
                VenueItem.venue_id == latest_check_sq.c.venue_id,
                VenueItem.item_id == CheckLine.item_id,
                VenueItem.active == True,
            ),
        )
        .join(Item, Item.id == VenueItem.item_id)
        .filter(Item.active == True)
        .group_by(latest_check_sq.c.venue_id, CheckLine.status)
        .all()
    ):
        latest_status_counts.setdefault(row.venue_id, {})[normalize_status(row.status)] = row.status_count

    venue_rows = []

    for v in venues:
        total_tracked = tracked_totals.get(v.id, 0)
        counts = {"good": 0, "ok": 0, "low": 0, "out": 0, "not_checked": 0}

        if total_tracked == 0:
            badge = {
                "key": "not_checked",
                "text": "Not Checked",
                "icon_class": "bi-dash-circle",
            }
            tooltip = "No items tracked."
            venue_rows.append({"venue": v, "badge": badge, "tooltip": tooltip})
            continue

        venue_status_counts = latest_status_counts.get(v.id)
        if not venue_status_counts:
            counts["not_checked"] = total_tracked
        else:
            for status, count in venue_status_counts.items():
                if status in counts:
                    counts[status] = count

            counted = sum(counts.values())
            if counted < total_tracked:
                counts["not_checked"] += (total_tracked - counted)

        if counts["out"] > 0:
            text = f'{counts["out"]} item(s) out of stock'
            badge = {"key": "out", "text": text, "icon_class": "bi-x-circle-fill"}
        elif counts["low"] > 0:
            text = f'{counts["low"]} item(s) Low'
            badge = {"key": "low", "text": text, "icon_class": "bi-exclamation-triangle-fill"}
        elif counts["ok"] > 0:
            badge = {"key": "ok", "text": "OK", "icon_class": "bi-check-circle-fill"}
        elif counts["good"] > 0 and (counts["good"] + counts["not_checked"] == total_tracked):
            badge = {"key": "good", "text": "Good", "icon_class": "bi-check-circle-fill"}

        # all items explicitly good
        elif counts["good"] == total_tracked:
            badge = {"key": "good", "text": "Good", "icon_class": "bi-check-circle-fill"}

        # otherwise (typically all not_checked)
        else:
            badge = {"key": "not_checked", "text": "Not Checked", "icon_class": "bi-dash-circle"}

        tooltip = (
            f"Total tracked: {total_tracked} | "
            f"Good: {counts['good']} | OK: {counts['ok']} | "
            f"Low: {counts['low']} | Out: {counts['out']} | "
            f"Not checked: {counts['not_checked']}"
        )

        venue_rows.append({"venue": v, "badge": badge, "tooltip": tooltip})

    return venue_rows


def build_restock_rows(
    statuses=None,
    item_ids=None,
    venue_ids=None,
    search="",
    sort="status_priority",
    limit=None,
    offset=0,
):
    if statuses is None:
        selected_statuses = list(RESTOCK_STATUS_META.keys())
    else:
        selected_statuses = [s for s in statuses if s in RESTOCK_STATUS_META]
    if not selected_statuses:
        return {"rows": [], "total_count": 0, "has_more": False}
    selected_statuses_set = set(selected_statuses)
    search_query = (search or "").strip().lower()
    if sort in {"item", "item_asc", "item_desc"}:
        sort_mode = "item"
    elif sort in {"venue", "venue_asc", "venue_desc"}:
        sort_mode = "venue"
    elif sort == "status_priority":
        sort_mode = "status_priority"
    elif sort == "last_checked":
        sort_mode = "last_checked"
    else:
        sort_mode = "status_priority"

    latest_check_sq = (
        db.session.query(
            Check.venue_id.label("venue_id"),
            func.max(Check.id).label("latest_check_id"),
        )
        .group_by(Check.venue_id)
        .subquery()
    )

    query = (
        db.session.query(
            Venue.id.label("venue_id"),
            Venue.name.label("venue_name"),
            Item.id.label("item_id"),
            Item.name.label("item_name"),
            Check.created_at.label("latest_check_at"),
            CheckLine.status.label("line_status"),
        )
        .select_from(VenueItem)
        .join(Venue, Venue.id == VenueItem.venue_id)
        .join(Item, Item.id == VenueItem.item_id)
        .outerjoin(latest_check_sq, latest_check_sq.c.venue_id == Venue.id)
        .outerjoin(Check, Check.id == latest_check_sq.c.latest_check_id)
        .outerjoin(
            CheckLine,
            and_(
                CheckLine.check_id == latest_check_sq.c.latest_check_id,
                CheckLine.item_id == VenueItem.item_id,
            ),
        )
        .filter(
            VenueItem.active == True,
            Venue.active == True,
            Item.active == True,
        )
    )

    if item_ids is not None:
        if not item_ids:
            return {"rows": [], "total_count": 0, "has_more": False}
        query = query.filter(VenueItem.item_id.in_(item_ids))
    if venue_ids is not None:
        if not venue_ids:
            return {"rows": [], "total_count": 0, "has_more": False}
        query = query.filter(VenueItem.venue_id.in_(venue_ids))

    rows = []
    for row in query.order_by(Item.name.asc(), Venue.name.asc()).all():
        status_key = normalize_status(row.line_status)
        if status_key not in selected_statuses_set:
            continue

        meta = RESTOCK_STATUS_META[status_key]
        rows.append(
            {
                "venue_id": row.venue_id,
                "venue_name": row.venue_name,
                "item_id": row.item_id,
                "item_name": row.item_name,
                "latest_check_at": row.latest_check_at,
                "status": {
                    "key": status_key,
                    "text": meta["text"],
                    "icon_class": meta["icon_class"],
                },
            }
        )

    status_rank = {"out": 0, "low": 1, "ok": 2, "good": 3, "not_checked": 4}
    def base_sort_key(row):
        if sort_mode == "venue":
            return (row["venue_name"].lower(), row["item_name"].lower())
        if sort_mode == "status_priority":
            return (
                status_rank.get(row["status"]["key"], 99),
                row["venue_name"].lower(),
                row["item_name"].lower(),
            )
        if sort_mode == "last_checked":
            return (
                1 if row["latest_check_at"] is None else 0,
                -(row["latest_check_at"].timestamp()) if row["latest_check_at"] else 0,
                row["item_name"].lower(),
                row["venue_name"].lower(),
            )
        return (row["item_name"].lower(), row["venue_name"].lower())

    if search_query:
        ranked_rows = []
        for row in rows:
            rank = restock_search_rank(row, search_query)
            if rank is None:
                continue
            ranked_rows.append((rank, row))
        ranked_rows.sort(key=lambda pair: (pair[0], base_sort_key(pair[1])))
        rows = [pair[1] for pair in ranked_rows]
    else:
        rows.sort(key=base_sort_key)

    total_count = len(rows)
    normalized_offset = max(int(offset or 0), 0)
    if limit is None:
        paged_rows = rows[normalized_offset:]
    else:
        normalized_limit = max(int(limit), 0)
        paged_rows = rows[normalized_offset : normalized_offset + normalized_limit]
    has_more = (normalized_offset + len(paged_rows)) < total_count
    return {"rows": paged_rows, "total_count": total_count, "has_more": has_more}


def serialize_restock_row(row, next_path):
    latest_check_at = row["latest_check_at"]
    return {
        "venue_id": row["venue_id"],
        "venue_name": row["venue_name"],
        "item_id": row["item_id"],
        "item_name": row["item_name"],
        "latest_check_ts": latest_check_at.timestamp() if latest_check_at else None,
        "latest_check_text": (
            latest_check_at.strftime("%Y-%m-%d %I:%M %p") if latest_check_at else "No check yet"
        ),
        "latest_check_missing": latest_check_at is None,
        "status": row["status"],
        "quick_check_url": url_for(
            "venue_items.quick_check",
            venue_id=row["venue_id"],
            next=next_path,
        ),
    }


@main_bp.route("/")
def home():
    return redirect(url_for("main.dashboard"))


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
    restock_sort = request.args.get("restock_sort", "status_priority")
    if restock_sort in {"item_asc", "item_desc"}:
        restock_sort = "item"
    elif restock_sort in {"venue_asc", "venue_desc"}:
        restock_sort = "venue"
    elif restock_sort not in {"item", "venue", "status_priority", "last_checked"}:
        restock_sort = "status_priority"

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
        )
    )
    if restock_params_seen and requested_tab is None:
        active_tab = "restocking"

    activity_params_seen = any(
        k in request.args
        for k in (
            "activity_q",
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
        activity_pagination=activity_pagination,
        restock_filters={
            "statuses": restock_statuses,
            "item_ids": restock_item_ids,
            "venue_ids": restock_venue_ids,
            "search": restock_search,
            "sort": restock_sort,
        },
    )


@main_bp.route("/dashboard/activity_rows")
@roles_required("viewer", "staff", "admin")
def dashboard_activity_rows():
    activity_filters = parse_activity_request_args(request.args)
    activity_page_data = build_activity_page(
        search=activity_filters["search"],
        activity_type=activity_filters["type"],
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
    restock_sort = request.args.get("restock_sort", "status_priority")
    if restock_sort in {"item_asc", "item_desc"}:
        restock_sort = "item"
    elif restock_sort in {"venue_asc", "venue_desc"}:
        restock_sort = "venue"
    elif restock_sort not in {"item", "venue", "status_priority", "last_checked"}:
        restock_sort = "status_priority"

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
