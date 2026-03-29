from flask import Blueprint, render_template, request, redirect, url_for, flash
from sqlalchemy import func, and_
from app import db
from app.models import Venue, VenueItem, Item, Check, CheckLine

main_bp = Blueprint("main", __name__)

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


def build_restock_rows(statuses=None, item_ids=None, venue_ids=None, search="", sort="status_priority"):
    if statuses is None:
        selected_statuses = list(RESTOCK_STATUS_META.keys())
    else:
        selected_statuses = [s for s in statuses if s in RESTOCK_STATUS_META]
    if not selected_statuses:
        return []
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
            return []
        query = query.filter(VenueItem.item_id.in_(item_ids))
    if venue_ids is not None:
        if not venue_ids:
            return []
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

    if search_query:
        rows = [
            row
            for row in rows
            if (
                search_query in row["item_name"].lower()
                or search_query in row["venue_name"].lower()
                or search_query in row["status"]["text"].lower()
            )
        ]

    status_rank = {"out": 0, "low": 1, "ok": 2, "good": 3, "not_checked": 4}
    if sort_mode == "venue":
        rows.sort(key=lambda row: (row["venue_name"].lower(), row["item_name"].lower()))
    elif sort_mode == "status_priority":
        rows.sort(
            key=lambda row: (
                status_rank.get(row["status"]["key"], 99),
                row["venue_name"].lower(),
                row["item_name"].lower(),
            )
        )
    elif sort_mode == "last_checked":
        rows.sort(
            key=lambda row: (
                1 if row["latest_check_at"] is None else 0,
                -(row["latest_check_at"].timestamp()) if row["latest_check_at"] else 0,
                row["item_name"].lower(),
                row["venue_name"].lower(),
            )
        )
    else:
        rows.sort(key=lambda row: (row["item_name"].lower(), row["venue_name"].lower()))

    return rows


@main_bp.route("/")
def home():
    return redirect(url_for("main.dashboard"))


@main_bp.route("/dashboard")
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

    restock_rows = build_restock_rows(
        statuses=restock_statuses if restock_status_submitted else None,
        item_ids=restock_item_ids if restock_item_submitted else None,
        venue_ids=restock_venue_ids if restock_venue_submitted else None,
        search=restock_search,
        sort=restock_sort,
    )

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
        restock_filters={
            "statuses": restock_statuses,
            "item_ids": restock_item_ids,
            "venue_ids": restock_venue_ids,
            "search": restock_search,
            "sort": restock_sort,
        },
    )

@main_bp.route("/venues", methods=["GET", "POST"])
def venues():
    if request.method == "POST":
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