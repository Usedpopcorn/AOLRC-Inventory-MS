from flask import Blueprint, render_template, request, redirect, url_for, flash
from app import db
from app.authz import roles_required
from app.models import Venue

venue_settings_bp = Blueprint("venue_settings", __name__, url_prefix="/venues")


@venue_settings_bp.route("/<int:venue_id>/settings", methods=["GET", "POST"])
@roles_required("admin")
def settings(venue_id):
    venue = Venue.query.get_or_404(venue_id)

    if request.method == "POST":
        action = request.form.get("action")

        # Update name + visibility
        if action == "save":
            new_name = (request.form.get("name") or "").strip()
            is_active = request.form.get("active") == "true"

            if not new_name:
                flash("Venue name cannot be blank.", "error")
                return redirect(url_for("venue_settings.settings", venue_id=venue.id))

            # Prevent duplicate names (excluding this venue)
            exists = Venue.query.filter(Venue.name == new_name, Venue.id != venue.id).first()
            if exists:
                flash("Another venue already has that name.", "error")
                return redirect(url_for("venue_settings.settings", venue_id=venue.id))

            venue.name = new_name
            venue.active = is_active
            db.session.commit()

            flash("Venue settings saved.", "success")
            return redirect(url_for("venue_settings.settings", venue_id=venue.id))

        # Delete venue (other only)
        if action == "delete":
            if venue.is_core:
                flash("Primary venues cannot be deleted. Deactivate instead.", "error")
                return redirect(url_for("venue_settings.settings", venue_id=venue.id))

            db.session.delete(venue)
            db.session.commit()
            flash("Venue deleted.", "success")
            return redirect(url_for("main.venues"))


    return render_template("venues/settings.html", venue=venue)