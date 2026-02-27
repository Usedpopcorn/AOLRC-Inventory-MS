from flask import Blueprint, render_template, request, redirect, url_for, flash
from sqlalchemy import func
from app import db
from app.models import Venue, VenueItem, Item, Check, CheckLine

main_bp = Blueprint("main", __name__)

@main_bp.route("/")
def home():
    return redirect(url_for("main.venues"))

@main_bp.route("/venues", methods=["GET", "POST"])
def venues():
    if request.method == "POST":
        name = request.form.get("name", "").strip()

        if not name:
            flash("Venue name is required.", "error")
            return redirect(url_for("main.venues"))

        # Prevent duplicates
        exists = Venue.query.filter_by(name=name).first()
        if exists:
            flash("That venue already exists.", "error")
            return redirect(url_for("main.venues"))

        v = Venue(name=name, is_core=False, active=True)
        db.session.add(v)
        db.session.commit()

        flash("Venue added!", "success")
        return redirect(url_for("main.venues"))

    # GET request: show list of venues + computed status
    venues = Venue.query.order_by(Venue.name.asc()).all()

    venue_rows = []
    for v in venues:
        # total tracked = active venue_items joined to active items
        tracked_item_ids = [
            r[0] for r in db.session.query(VenueItem.item_id)
            .join(Item, Item.id == VenueItem.item_id)
            .filter(
                VenueItem.venue_id == v.id,
                VenueItem.active == True,
                Item.active == True
            ).all()
        ]

        total_tracked = len(tracked_item_ids)

        # default counts
        counts = {"good": 0, "ok": 0, "low": 0, "out": 0, "not_checked": 0}

        # If nothing tracked, treat as not_checked
        if total_tracked == 0:
            badge = {"key": "not_checked", "text": "Not Checked", "class": "bg-secondary", "icon": "—"}
            tooltip = "No items tracked."
            venue_rows.append({"venue": v, "badge": badge, "tooltip": tooltip})
            continue

        # Find latest check id for this venue
        latest_check_id = db.session.query(func.max(Check.id)).filter(Check.venue_id == v.id).scalar()

        if latest_check_id is None:
            # no check yet -> all not_checked
            counts["not_checked"] = total_tracked
        else:
            # count statuses in latest check for tracked items
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
                if status in counts:
                    counts[status] = c

            # Any tracked items missing from latest check lines => treat as not_checked
            counted = sum(counts.values())
            if counted < total_tracked:
                counts["not_checked"] += (total_tracked - counted)

        # Determine badge using your rules
        if counts["out"] > 0:
            text = f'{counts["out"]} item(s) out of stock'
            badge = {"key": "out", "text": text, "class": "bg-danger", "icon_class": "bi-x-circle-fill"}
        elif counts["low"] > 0:
            text = f'{counts["low"]} item(s) Low'
            badge = {"key": "low", "text": text, "class": "bg-warning text-dark", "icon_class": "bi-exclamation-triangle-fill"}
        elif counts["ok"] > 0:
            badge = {"key": "ok", "text": "OK", "class": "bg-warning text-dark", "icon_class": "bi-check-circle-fill"}
        elif counts["good"] == total_tracked:
            badge = {"key": "good", "text": "Good", "class": "bg-success", "icon_class": "bi-check-circle-fill"}
        else:
            # mix of good + not_checked, or all not_checked
            badge = {"key": "not_checked", "text": "Not Checked", "class": "bg-secondary", "icon_class": "bi-dash-circle"}

        tooltip = (
            f"Total tracked: {total_tracked} | "
            f"Good: {counts['good']} | OK: {counts['ok']} | "
            f"Low: {counts['low']} | Out: {counts['out']} | "
            f"Not checked: {counts['not_checked']}"
        )

        venue_rows.append({"venue": v, "badge": badge, "tooltip": tooltip})

    return render_template("venues/list.html", venue_rows=venue_rows)