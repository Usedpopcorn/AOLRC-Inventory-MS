from flask import Blueprint, render_template, request, redirect, url_for, flash
from app import db
from app.models import Item, VenueItem, Venue

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


@admin_bp.route("/items", methods=["GET", "POST"])
def items():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        item_type = request.form.get("item_type", "").strip().lower()

        if not name:
            flash("Item name is required.", "error")
            return redirect(url_for("admin.items"))

        if item_type not in ["durable", "consumable"]:
            flash("Item type must be durable or consumable.", "error")
            return redirect(url_for("admin.items"))

        existing = Item.query.filter_by(name=name).first()
        if existing:
            existing.active = True
            existing.item_type = item_type
            db.session.commit()
            flash("Item already existed — reactivated/updated.", "success")
            return redirect(url_for("admin.items"))

        db.session.add(Item(name=name, item_type=item_type, active=True))
        db.session.commit()
        flash("Item added!", "success")
        return redirect(url_for("admin.items"))

    items = Item.query.order_by(Item.name.asc()).all()
    return render_template("admin/items.html", items=items)


@admin_bp.route("/items/<int:item_id>/deactivate", methods=["GET", "POST"])
def deactivate_item(item_id):
    it = Item.query.get_or_404(item_id)

    # Find venues where this item is currently tracked (active mapping)
    active_links = VenueItem.query.filter_by(item_id=item_id, active=True).all()
    venue_ids = [l.venue_id for l in active_links]
    venues = Venue.query.filter(Venue.id.in_(venue_ids)).order_by(Venue.name.asc()).all() if venue_ids else []

    # GET: show confirmation page if it’s in use
    if request.method == "GET":
        if len(venues) == 0:
            # Safe to deactivate immediately
            it.active = False
            db.session.commit()
            flash("Item deactivated.", "success")
            return redirect(url_for("admin.items"))

        # Needs confirmation
        return render_template("admin/confirm_deactivate_item.html", item=it, venues=venues)

    # POST: user confirmed “Deactivate anyway”
    it.active = False
    VenueItem.query.filter_by(item_id=item_id, active=True).update({"active": False})
    db.session.commit()

    flash(f"Item deactivated and removed from {len(venues)} venue(s).", "success")
    return redirect(url_for("admin.items"))


@admin_bp.post("/items/<int:item_id>/activate")
def activate_item(item_id):
    it = Item.query.get_or_404(item_id)
    it.active = True
    db.session.commit()
    flash("Item activated.", "success")
    return redirect(url_for("admin.items"))