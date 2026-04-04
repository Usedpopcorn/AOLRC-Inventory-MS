from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import current_user
from app import db
from app.authz import roles_required
from app.models import (
    Venue,
    Item,
    VenueItem,
    Check,
    CheckLine,
    CountSession,
    CountLine,
    VenueItemCount,
)

venue_items_bp = Blueprint("venue_items", __name__, url_prefix="/venues")

@venue_items_bp.route("/<int:venue_id>/supplies", methods=["GET", "POST"])
@roles_required("staff", "admin")
def supplies(venue_id):
    venue = Venue.query.get_or_404(venue_id)
    next_url = request.values.get("next") or url_for("venue_settings.settings", venue_id=venue.id)

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
        return redirect(url_for("venue_items.supplies", venue_id=venue.id, next=next_url))

    items = Item.query.filter_by(active=True).order_by(Item.name.asc()).all()
    active_item_ids = {
        m.item_id for m in VenueItem.query.filter_by(venue_id=venue.id, active=True).all()
    }

    return render_template(
        "venues/supplies.html",
        venue=venue,
        items=items,
        active_item_ids=active_item_ids,
        next_url=next_url,
    )

@venue_items_bp.route("/<int:venue_id>/check", methods=["GET", "POST"])
@roles_required("viewer", "staff", "admin")
def quick_check(venue_id):
    venue = Venue.query.get_or_404(venue_id)

    next_url = request.values.get("next") or url_for("main.venues")

    # Items tracked in this venue (active mappings, active items)
    tracked = (
        db.session.query(Item)
        .join(VenueItem, VenueItem.item_id == Item.id)
        .filter(VenueItem.venue_id == venue.id, VenueItem.active == True, Item.active == True)
        .order_by(Item.name.asc())
        .all()
    )

    selected_mode = (request.values.get("mode") or "status").strip().lower()
    if selected_mode not in ("status", "raw_counts"):
        selected_mode = "status"

    if request.method == "POST":
        if not current_user.has_role("staff", "admin"):
            flash("You have view-only access.", "error")
            return redirect(url_for("venue_items.quick_check", venue_id=venue.id, next=next_url, mode=selected_mode))

        selected_mode = (request.form.get("check_mode") or "status").strip().lower()
        if selected_mode not in ("status", "raw_counts"):
            selected_mode = "status"

        if selected_mode == "raw_counts":
            count_session = CountSession(venue_id=venue.id)
            db.session.add(count_session)
            db.session.flush()

            existing_counts = {
                row.item_id: row
                for row in VenueItemCount.query.filter_by(venue_id=venue.id).all()
            }

            for it in tracked:
                raw_value = (request.form.get(f"count_{it.id}") or "0").strip()
                try:
                    raw_count = int(raw_value)
                except ValueError:
                    raw_count = 0

                if raw_count < 0:
                    raw_count = 0

                db.session.add(
                    CountLine(
                        count_session_id=count_session.id,
                        item_id=it.id,
                        raw_count=raw_count,
                    )
                )

                current = existing_counts.get(it.id)
                if current:
                    current.raw_count = raw_count
                else:
                    db.session.add(
                        VenueItemCount(
                            venue_id=venue.id,
                            item_id=it.id,
                            raw_count=raw_count,
                        )
                    )

            db.session.commit()
            flash("Saved raw counts.", "success")
            return redirect(
                url_for(
                    "venue_items.quick_check",
                    venue_id=venue.id,
                    next=next_url,
                    mode="raw_counts",
                )
            )

        # Create a new status check (existing behavior)
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
        return redirect(
            url_for("venue_items.quick_check", venue_id=venue.id, next=next_url, mode="status")
        )

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

    latest_counts = {}
    for it in tracked:
        row = (
            db.session.query(VenueItemCount.raw_count)
            .filter(VenueItemCount.venue_id == venue.id, VenueItemCount.item_id == it.id)
            .first()
        )
        latest_counts[it.id] = row[0] if row else 0

    return render_template(
        "venues/quick_check.html",
        venue=venue,
        items=tracked,
        latest_status=latest_status,
        latest_counts=latest_counts,
        selected_mode=selected_mode,
        next_url=next_url,
    )