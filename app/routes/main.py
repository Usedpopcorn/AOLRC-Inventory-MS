from flask import Blueprint, render_template, request, redirect, url_for, flash
from sqlalchemy import func
from app import db
from app.models import Venue, VenueItem, Item, Check, CheckLine

main_bp = Blueprint("main", __name__)


def build_venue_rows():
    venues = Venue.query.order_by(Venue.name.asc()).all()
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
        elif counts["good"] == total_tracked:
            badge = {"key": "good", "text": "Good", "icon_class": "bi-check-circle-fill"}
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


@main_bp.route("/")
def home():
    return redirect(url_for("main.dashboard"))


@main_bp.route("/dashboard")
def dashboard():
    return render_template("dashboard.html", venue_rows=build_venue_rows())


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

        v = Venue(name=name, is_core=False, active=True)
        db.session.add(v)
        db.session.commit()

        flash("Venue added!", "success")
        return redirect(url_for("main.venues"))

    return render_template("venues/list.html", venue_rows=build_venue_rows())