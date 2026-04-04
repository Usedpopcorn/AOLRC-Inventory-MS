import re

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import current_user
from sqlalchemy import func, and_
from app import db
from app.authz import roles_required
from app.models import Venue, VenueItem, Item, Check, CheckLine

main_bp = Blueprint("main", __name__)
RESTOCK_PAGE_SIZE = 50

RESTOCK_STATUS_META = {
    "good": {"text": "Good", "icon_class": "bi-check-circle-fill"},
    "ok": {"text": "OK", "icon_class": "bi-check-circle-fill"},
    "low": {"text": "Low", "icon_class": "bi-exclamation-triangle-fill"},
    "out": {"text": "Out", "icon_class": "bi-x-circle-fill"},
    "not_checked": {"text": "Not Checked", "icon_class": "bi-dash-circle"},
}


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


def build_venue_rows(include_inactive=False):
    q = Venue.query
    if not include_inactive:
        q = q.filter(Venue.active == True)
    venues = q.order_by(Venue.name.asc()).all()
    venue_rows = []

    for v in venues:
        tracked_item_ids = [
            r[0] for r in (
                db.session.query(VenueItem.item_id)
                .join(Item, Item.id == VenueItem.item_id)
                .filter(
                    VenueItem.venue_id == v.id,
                    VenueItem.active == True,
                    Item.active == True
                )
                .all()
            )
        ]

        total_tracked = len(tracked_item_ids)
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

        latest_check_id = (
            db.session.query(func.max(Check.id))
            .filter(Check.venue_id == v.id)
            .scalar()
        )

        if latest_check_id is None:
            counts["not_checked"] = total_tracked
        else:
            rows = (
                db.session.query(CheckLine.status, func.count(CheckLine.id))
                .filter(
                    CheckLine.check_id == latest_check_id,
                    CheckLine.item_id.in_(tracked_item_ids)
                )
                .group_by(CheckLine.status)
                .all()
            )
            for status, c in rows:
                status = (status or "").strip().lower()
                if status == "-":
                    status = "not_checked"
                if status in counts:
                    counts[status] = c

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
        restock_filters={
            "statuses": restock_statuses,
            "item_ids": restock_item_ids,
            "venue_ids": restock_venue_ids,
            "search": restock_search,
            "sort": restock_sort,
        },
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