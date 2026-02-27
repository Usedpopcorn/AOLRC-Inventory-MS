from flask import Blueprint, render_template, request, redirect, url_for, flash
from app import db
from app.models import Venue, Item, VenueItem, Check, CheckLine
from sqlalchemy import desc

venue_items_bp = Blueprint("venue_items", __name__, url_prefix="/venues")

@venue_items_bp.route("/<int:venue_id>/supplies", methods=["GET", "POST"])
def supplies(venue_id):
    venue = Venue.query.get_or_404(venue_id)

    if request.method == "POST":
        # IDs of items that were checked in the form
        selected_ids = set(map(int, request.form.getlist("item_ids")))

        # Current mappings for this venue
        mappings = VenueItem.query.filter_by(venue_id=venue.id).all()
        mapping_by_item = {m.item_id: m for m in mappings}

        # Ensure selected items are active
        created = 0
        activated = 0
        deactivated = 0

        for item_id in selected_ids:
            if item_id in mapping_by_item:
                if not mapping_by_item[item_id].active:
                    mapping_by_item[item_id].active = True
                    activated += 1
            else:
                db.session.add(VenueItem(venue_id=venue.id, item_id=item_id, active=True))
                created += 1

        # Deactivate anything that is currently active but not selected
        for m in mappings:
            if m.active and (m.item_id not in selected_ids):
                m.active = False
                deactivated += 1

        db.session.commit()
        flash(f"Saved supplies. Added: {created}, Enabled: {activated}, Disabled: {deactivated}", "success")
        return redirect(url_for("venue_items.supplies", venue_id=venue.id))

    items = Item.query.filter_by(active=True).order_by(Item.name.asc()).all()
    active_item_ids = {
        m.item_id for m in VenueItem.query.filter_by(venue_id=venue.id, active=True).all()
    }

    return render_template(
        "venues/supplies.html",
        venue=venue,
        items=items,
        active_item_ids=active_item_ids,
    )

@venue_items_bp.route("/<int:venue_id>/check", methods=["GET", "POST"])
def quick_check(venue_id):
    venue = Venue.query.get_or_404(venue_id)

    # Items tracked in this venue (active mappings, active items)
    tracked = (
        db.session.query(Item)
        .join(VenueItem, VenueItem.item_id == Item.id)
        .filter(VenueItem.venue_id == venue.id, VenueItem.active == True, Item.active == True)
        .order_by(Item.name.asc())
        .all()
    )

    if request.method == "POST":
        # Create a new check
        chk = Check(venue_id=venue.id)
        db.session.add(chk)
        db.session.flush()  # assign chk.id

        # For each tracked item, read status from form
        for it in tracked:
            status = (request.form.get(f"status_{it.id}") or "not_checked").strip().lower()
            if status not in ("good", "ok", "low", "out", "not_checked"):
                status = "not_checked"

            db.session.add(CheckLine(check_id=chk.id, item_id=it.id, status=status))

        db.session.commit()
        flash("Saved check ✅", "success")
        return redirect(url_for("venue_items.quick_check", venue_id=venue.id))

    # GET: Prefill with most recent status per item (if exists)
    latest_status = {}
    for it in tracked:
        row = (
            db.session.query(CheckLine.status)
            .join(Check, Check.id == CheckLine.check_id)
            .filter(Check.venue_id == venue.id, CheckLine.item_id == it.id)
            .order_by(Check.created_at.desc())
            .first()
        )
        latest_status[it.id] = row[0] if row else "not_checked"

    return render_template(
        "venues/quick_check.html",
        venue=venue,
        items=tracked,
        latest_status=latest_status,
    )