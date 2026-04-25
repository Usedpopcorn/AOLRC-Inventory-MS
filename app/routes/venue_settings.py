from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy.orm import selectinload

from app import db
from app.authz import roles_required
from app.security import normalize_safe_redirect_path
from app.models import Item, Venue
from app.services.inventory_rules import (
    InventoryRuleError,
    copy_venue_tracking_setup,
    get_default_stale_threshold_days,
    log_inventory_admin_event,
    normalize_optional_threshold_days,
    normalize_optional_tracking_value,
    resolve_effective_par_level,
    resolve_effective_stale_threshold_days,
    sync_venue_tracked_items,
)

venue_settings_bp = Blueprint("venue_settings", __name__, url_prefix="/venues")


def normalize_next_path(next_candidate, fallback_path):
    return normalize_safe_redirect_path(next_candidate, fallback_path)


def describe_back_destination(next_path, venue_id):
    target_path = next_path.split("?", 1)[0]

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
    return "Previous Page"


def parse_selected_ids(raw_values):
    selected_ids = []
    for raw_value in raw_values or []:
        value = (raw_value or "").strip()
        if not value or not value.isdigit():
            continue
        parsed = int(value)
        if parsed not in selected_ids:
            selected_ids.append(parsed)
    return selected_ids


def fetch_trackable_items():
    return (
        Item.query.options(selectinload(Item.parent_item))
        .filter(
            Item.active == True,
            Item.is_group_parent == False,
        )
        .order_by(Item.name.asc(), Item.id.asc())
        .all()
    )


def fetch_copy_source_venues(current_venue_id):
    return (
        Venue.query.filter(Venue.id != current_venue_id)
        .order_by(Venue.name.asc(), Venue.id.asc())
        .all()
    )


def build_details_form_values(venue, source=None):
    if source is not None:
        return {
            "name": (source.get("name") or "").strip(),
            "active": "true" if (source.get("active") or "true") == "true" else "false",
            "stale_threshold_days": (source.get("stale_threshold_days") or "").strip(),
        }
    return {
        "name": venue.name,
        "active": "true" if venue.active else "false",
        "stale_threshold_days": "" if venue.stale_threshold_days is None else str(venue.stale_threshold_days),
    }


def parse_tracking_form(trackable_items):
    selected_item_ids = parse_selected_ids(request.form.getlist("item_ids"))
    par_overrides = {}
    errors = []
    for item in trackable_items:
        try:
            par_overrides[item.id] = normalize_optional_tracking_value(
                request.form.get(f"par_override_{item.id}"),
                field_label=f"Par override for {item.name}",
            )
        except InventoryRuleError as exc:
            errors.append(str(exc))
    return selected_item_ids, par_overrides, errors


def build_tracking_rows(*, venue, trackable_items, selected_item_ids=None, par_overrides=None):
    existing_links = {}
    if venue is not None:
        existing_links = {
            link.item_id: link
            for link in venue_item_query(venue.id)
        }

    if selected_item_ids is None:
        selected_item_ids = [
            item_id
            for item_id, link in existing_links.items()
            if link.active
        ]

    selected_item_id_set = set(selected_item_ids or [])
    rows = []
    for item in trackable_items:
        existing_link = existing_links.get(item.id)
        existing_override = (
            existing_link.expected_qty
            if existing_link is not None and existing_link.active
            else None
        )
        override_value = (
            par_overrides.get(item.id)
            if par_overrides is not None
            else existing_override
        )
        effective_par = resolve_effective_par_level(
            item_default_par_level=item.default_par_level,
            venue_par_override=override_value,
        )
        rows.append(
            {
                "id": item.id,
                "name": item.name,
                "parent_name": item.parent_item.name if item.parent_item else None,
                "tracking_mode": item.tracking_mode,
                "item_category": item.item_category or item.item_type,
                "default_par_level": item.default_par_level,
                "is_selected": item.id in selected_item_id_set,
                "par_override": "" if override_value is None else str(override_value),
                "effective_par": effective_par.value,
                "effective_par_source": effective_par.source,
            }
        )
    return rows


def venue_item_query(venue_id):
    from app.models import VenueItem

    return (
        VenueItem.query.filter(VenueItem.venue_id == venue_id)
        .order_by(VenueItem.item_id.asc(), VenueItem.id.asc())
        .all()
    )


@venue_settings_bp.route("/<int:venue_id>/settings", methods=["GET", "POST"])
@roles_required("admin")
def settings(venue_id):
    venue = Venue.query.get_or_404(venue_id)
    next_path = normalize_next_path(request.values.get("next"), url_for("main.venues"))
    trackable_items = fetch_trackable_items()
    copy_source_venues = fetch_copy_source_venues(venue.id)
    details_form_values = build_details_form_values(venue)
    tracking_rows = build_tracking_rows(venue=venue, trackable_items=trackable_items)

    def settings_self_url():
        return url_for("venue_settings.settings", venue_id=venue.id, next=next_path)

    if request.method == "POST":
        action = request.form.get("action")

        if action == "save":
            details_form_values = build_details_form_values(venue, source=request.form)
            new_name = details_form_values["name"]
            is_active = details_form_values["active"] == "true"

            try:
                stale_threshold_days = normalize_optional_threshold_days(
                    request.form.get("stale_threshold_days"),
                    field_label="Venue stale threshold",
                )
            except InventoryRuleError as exc:
                flash(str(exc), "error")
            else:
                if not new_name:
                    flash("Venue name cannot be blank.", "error")
                else:
                    exists = Venue.query.filter(Venue.name == new_name, Venue.id != venue.id).first()
                    if exists:
                        flash("Another venue already has that name.", "error")
                    else:
                        changed_fields = []
                        if venue.name != new_name:
                            changed_fields.append("name")
                        if bool(venue.active) != bool(is_active):
                            changed_fields.append("visibility")
                        if venue.stale_threshold_days != stale_threshold_days:
                            changed_fields.append("stale threshold")

                        venue.name = new_name
                        venue.active = is_active
                        venue.stale_threshold_days = stale_threshold_days
                        if changed_fields:
                            log_inventory_admin_event(
                                "venue_updated",
                                actor=current_user,
                                subject_type="venue",
                                subject_id=venue.id,
                                subject_label=venue.name,
                                details={"changed_fields": changed_fields},
                            )
                        db.session.commit()
                        flash("Venue settings saved.", "success")
                        return redirect(settings_self_url())

        if action == "save_tracking":
            selected_item_ids, par_overrides, errors = parse_tracking_form(trackable_items)
            tracking_rows = build_tracking_rows(
                venue=venue,
                trackable_items=trackable_items,
                selected_item_ids=selected_item_ids,
                par_overrides=par_overrides,
            )
            if errors:
                for error in errors:
                    flash(error, "error")
            else:
                summary = sync_venue_tracked_items(
                    venue=venue,
                    selected_item_ids=selected_item_ids,
                    par_overrides={item_id: par_overrides.get(item_id) for item_id in selected_item_ids},
                )
                if any(summary.values()):
                    log_inventory_admin_event(
                        "venue_tracking_updated",
                        actor=current_user,
                        subject_type="venue",
                        subject_id=venue.id,
                        subject_label=venue.name,
                        details=summary,
                    )
                    db.session.commit()
                    flash("Tracked items saved.", "success")
                else:
                    db.session.rollback()
                    flash("No tracked-item changes were needed.", "success")
                return redirect(settings_self_url())

        if action == "copy_tracking":
            source_venue_id_raw = request.form.get("source_venue_id", "")
            if not source_venue_id_raw.isdigit():
                flash("Choose a source venue first.", "error")
            else:
                source_venue_id = int(source_venue_id_raw)
                source_venue = Venue.query.filter(Venue.id == source_venue_id, Venue.id != venue.id).first()
                if source_venue is None:
                    flash("Source venue not found.", "error")
                else:
                    summary = copy_venue_tracking_setup(
                        source_venue=source_venue,
                        target_venue=venue,
                    )
                    log_inventory_admin_event(
                        "venue_tracking_copied",
                        actor=current_user,
                        subject_type="venue",
                        subject_id=venue.id,
                        subject_label=venue.name,
                        details=summary,
                    )
                    db.session.commit()
                    flash(f"Tracked setup copied from {source_venue.name}.", "success")
                    return redirect(settings_self_url())

        if action == "delete":
            if venue.is_core:
                flash("Primary venues cannot be deleted. Deactivate instead.", "error")
                return redirect(settings_self_url())

            db.session.delete(venue)
            db.session.commit()
            flash("Venue deleted.", "success")
            return redirect(url_for("main.venues"))

    effective_stale_threshold = resolve_effective_stale_threshold_days(
        venue_stale_threshold_days=venue.stale_threshold_days,
        global_stale_threshold_days=get_default_stale_threshold_days(),
    )

    return render_template(
        "venues/settings.html",
        venue=venue,
        back_url=next_path,
        back_label=describe_back_destination(next_path, venue.id),
        details_form_values=details_form_values,
        tracking_rows=tracking_rows,
        copy_source_venues=copy_source_venues,
        global_stale_threshold_days=get_default_stale_threshold_days(),
        effective_stale_threshold=effective_stale_threshold,
    )
