from flask import Blueprint, render_template, request, redirect, url_for, flash
from urllib.parse import urljoin, urlparse
from app import db
from app.authz import roles_required
from app.models import Venue

venue_settings_bp = Blueprint("venue_settings", __name__, url_prefix="/venues")


def normalize_next_path(next_candidate, fallback_path):
    if not next_candidate:
        return fallback_path

    host_url = urlparse(request.host_url)
    target_url = urlparse(urljoin(request.host_url, next_candidate))
    if target_url.scheme not in {"http", "https"} or target_url.netloc != host_url.netloc:
        return fallback_path

    current_url = urlparse(urljoin(request.host_url, request.full_path))
    if target_url.path == current_url.path and target_url.query == current_url.query:
        return fallback_path

    return f"{target_url.path}?{target_url.query}" if target_url.query else target_url.path


def describe_back_destination(next_path, venue_id):
    target_path = urlparse(urljoin(request.host_url, next_path)).path

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


@venue_settings_bp.route("/<int:venue_id>/settings", methods=["GET", "POST"])
@roles_required("admin")
def settings(venue_id):
    venue = Venue.query.get_or_404(venue_id)
    next_path = normalize_next_path(request.values.get("next"), url_for("main.venues"))

    def settings_self_url():
        return url_for("venue_settings.settings", venue_id=venue.id, next=next_path)

    if request.method == "POST":
        action = request.form.get("action")

        # Update name + visibility
        if action == "save":
            new_name = (request.form.get("name") or "").strip()
            is_active = request.form.get("active") == "true"

            if not new_name:
                flash("Venue name cannot be blank.", "error")
                return redirect(settings_self_url())

            # Prevent duplicate names (excluding this venue)
            exists = Venue.query.filter(Venue.name == new_name, Venue.id != venue.id).first()
            if exists:
                flash("Another venue already has that name.", "error")
                return redirect(settings_self_url())

            venue.name = new_name
            venue.active = is_active
            db.session.commit()

            flash("Venue settings saved.", "success")
            return redirect(settings_self_url())

        # Delete venue (other only)
        if action == "delete":
            if venue.is_core:
                flash("Primary venues cannot be deleted. Deactivate instead.", "error")
                return redirect(settings_self_url())

            db.session.delete(venue)
            db.session.commit()
            flash("Venue deleted.", "success")
            return redirect(url_for("main.venues"))


    return render_template(
        "venues/settings.html",
        venue=venue,
        back_url=next_path,
        back_label=describe_back_destination(next_path, venue.id),
    )