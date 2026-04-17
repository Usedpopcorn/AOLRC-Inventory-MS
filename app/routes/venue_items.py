from flask import Blueprint, render_template, request, redirect, url_for, flash
from urllib.parse import urljoin, urlparse
from flask_login import current_user
from sqlalchemy.orm import selectinload
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


def normalize_singleton_status(status):
    normalized = (status or "not_checked").strip().lower()
    aliases = {
        "present": "good",
        "damaged": "low",
        "missing": "out",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized == "ok":
        normalized = "good"
    if normalized not in {"good", "low", "out", "not_checked"}:
        return "not_checked"
    return normalized


def derive_singleton_count(status):
    normalized = normalize_singleton_status(status)
    if normalized in {"good", "low"}:
        return 1
    if normalized == "out":
        return 0
    return None


def infer_singleton_status_from_count(raw_count):
    if raw_count is None:
        return "not_checked"
    return "good" if raw_count > 0 else "out"


def sync_singleton_compat_count(existing_counts, venue_id, item_id, status):
    derived_count = derive_singleton_count(status)
    current = existing_counts.get(item_id)

    if derived_count is None:
        if current:
            db.session.delete(current)
            existing_counts.pop(item_id, None)
        return

    if current:
        current.raw_count = derived_count
        return

    new_count = VenueItemCount(
        venue_id=venue_id,
        item_id=item_id,
        raw_count=derived_count,
    )
    db.session.add(new_count)
    existing_counts[item_id] = new_count


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


def build_overall_status(total_tracked, counts):
    if total_tracked <= 0:
        return {"key": "not_checked", "text": "Not Checked", "icon_class": "bi-dash-circle"}

    checked_count = total_tracked - counts["not_checked"]

    if counts["out"] > 0:
        out_count = counts["out"]
        out_label = "Item" if out_count == 1 else "Items"
        return {
            "key": "out",
            "text": f"{out_count} {out_label} out of stock",
            "icon_class": "bi-x-circle-fill",
        }
    if counts["low"] > 0:
        low_count = counts["low"]
        low_label = "Item" if low_count == 1 else "Items"
        return {
            "key": "low",
            "text": f"{low_count} {low_label} Low",
            "icon_class": "bi-exclamation-triangle-fill",
        }
    if checked_count > 0 and counts["ok"] > 0 and (counts["ok"] * 2 >= checked_count):
        return {"key": "ok", "text": "OK", "icon_class": "bi-check-circle-fill"}
    if counts["good"] > 0:
        return {"key": "good", "text": "Good", "icon_class": "bi-check-circle-fill"}
    if checked_count > 0 and counts["ok"] > 0:
        return {"key": "ok", "text": "OK", "icon_class": "bi-check-circle-fill"}
    return {"key": "not_checked", "text": "Not Checked", "icon_class": "bi-dash-circle"}


def resolve_status_key_from_counts(total_tracked, counts):
    if total_tracked <= 0:
        return "not_checked"

    checked_count = total_tracked - counts["not_checked"]

    if counts["out"] > 0:
        return "out"
    if counts["low"] > 0:
        return "low"
    if checked_count > 0 and counts["ok"] > 0 and (counts["ok"] * 2 >= checked_count):
        return "ok"
    if counts["good"] > 0:
        return "good"
    if checked_count > 0 and counts["ok"] > 0:
        return "ok"
    return "not_checked"


def operational_item_sort_key(item):
    parent_item = item.parent_item
    family_name = (parent_item.name if parent_item else item.name or "").lower()
    family_order = parent_item.sort_order if parent_item else item.sort_order
    return (
        family_order or 0,
        family_name,
        1 if parent_item else 0,
        item.sort_order or 0,
        item.name.lower(),
        item.id,
    )


def quick_check_status_meta(item, status_key):
    normalized = (status_key or "not_checked").strip().lower()
    if item.tracking_mode == "singleton_asset":
        labels = {
            "good": "Present",
            "low": "Damaged",
            "out": "Missing",
            "not_checked": "Not checked",
        }
    else:
        labels = {
            "good": "Good",
            "ok": "OK",
            "low": "Low",
            "out": "Out",
            "not_checked": "Not checked",
        }
    severity = {
        "out": 4,
        "low": 3,
        "not_checked": 2,
        "ok": 1,
        "good": 0,
    }.get(normalized, 2)
    return {
        "key": normalized,
        "label": labels.get(normalized, "Not checked"),
        "severity": severity,
    }


def build_quick_check_groups(items, latest_status, latest_counts):
    ordered_groups = []
    families_by_id = {}

    for item in items:
        parent_item = item.parent_item
        if parent_item:
            group = families_by_id.get(parent_item.id)
            if group is None:
                group = {
                    "kind": "family",
                    "id": f"family-{parent_item.id}",
                    "family_id": parent_item.id,
                    "family_name": parent_item.name,
                    "sort_name": parent_item.name.lower(),
                    "children": [],
                    "child_count": 0,
                    "checked_count": 0,
                    "counted_count": 0,
                    "worst_status_key": "not_checked",
                    "worst_status_label": "Not checked",
                    "worst_status_severity": 2,
                    "status_counts": {"good": 0, "ok": 0, "low": 0, "out": 0, "not_checked": 0},
                    "has_singleton_children": False,
                    "has_quantity_children": False,
                    "search_text": parent_item.name.lower(),
                }
                families_by_id[parent_item.id] = group
                ordered_groups.append(group)

            status_meta = quick_check_status_meta(item, latest_status.get(item.id))
            if status_meta["key"] in group["status_counts"]:
                group["status_counts"][status_meta["key"]] += 1
            if status_meta["key"] != "not_checked":
                group["checked_count"] += 1
            has_counted_value = latest_counts.get(item.id) is not None
            if item.tracking_mode == "singleton_asset":
                has_counted_value = status_meta["key"] != "not_checked"
            if has_counted_value:
                group["counted_count"] += 1
            if status_meta["severity"] > group["worst_status_severity"]:
                group["worst_status_key"] = status_meta["key"]
                group["worst_status_label"] = status_meta["label"]
                group["worst_status_severity"] = status_meta["severity"]
            group["has_singleton_children"] = group["has_singleton_children"] or item.tracking_mode == "singleton_asset"
            group["has_quantity_children"] = group["has_quantity_children"] or item.tracking_mode != "singleton_asset"
            group["children"].append(item)
            group["search_text"] = f'{group["search_text"]} {item.name.lower()}'
            continue

        ordered_groups.append(
            {
                "kind": "item",
                "id": f"item-{item.id}",
                "sort_name": item.name.lower(),
                "item": item,
                "search_text": item.name.lower(),
            }
        )

    for group in ordered_groups:
        if group["kind"] != "family":
            continue
        group["child_count"] = len(group["children"])
        resolved_key = resolve_status_key_from_counts(group["child_count"], group["status_counts"])
        group["worst_status_key"] = resolved_key
        group["worst_status_severity"] = quick_check_status_meta(
            group["children"][0],
            resolved_key,
        )["severity"]
        if group["has_singleton_children"] and not group["has_quantity_children"]:
            group["worst_status_label"] = {
                "good": "Present",
                "low": "Damaged",
                "out": "Missing",
                "not_checked": "Not checked",
            }.get(resolved_key, "Not checked")
        else:
            group["worst_status_label"] = {
                "good": "Good",
                "ok": "OK",
                "low": "Low",
                "out": "Out",
                "not_checked": "Not checked",
            }.get(resolved_key, "Not checked")
        group["checked_summary"] = f'{group["checked_count"]} of {group["child_count"]} checked'
        if group["has_singleton_children"] and not group["has_quantity_children"]:
            group["counted_summary"] = f'{group["counted_count"]} of {group["child_count"]} checked'
            group["tracking_summary"] = "Asset family"
        elif group["has_quantity_children"] and not group["has_singleton_children"]:
            group["counted_summary"] = f'{group["counted_count"]} of {group["child_count"]} counted'
            group["tracking_summary"] = "Quantity family"
        else:
            group["counted_summary"] = f'{group["counted_count"]} of {group["child_count"]} updated'
            group["tracking_summary"] = "Mixed tracking family"

    return ordered_groups


@venue_items_bp.route("/<int:venue_id>/supplies", methods=["GET", "POST"])
@roles_required("staff", "admin")
def supplies(venue_id):
    venue = Venue.query.get_or_404(venue_id)
    next_url = normalize_next_path(
        request.values.get("next"),
        url_for("venue_settings.settings", venue_id=venue.id),
    )

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

    items = (
        Item.query.options(selectinload(Item.parent_item))
        .filter(
            Item.active == True,
            Item.is_group_parent == False,
        )
        .all()
    )
    items = sorted(items, key=operational_item_sort_key)
    active_item_ids = {
        m.item_id for m in VenueItem.query.filter_by(venue_id=venue.id, active=True).all()
    }

    return render_template(
        "venues/supplies.html",
        venue=venue,
        items=items,
        active_item_ids=active_item_ids,
        next_url=next_url,
        back_label=describe_back_destination(next_url, venue.id),
    )

@venue_items_bp.route("/<int:venue_id>/check", methods=["GET", "POST"])
@roles_required("viewer", "staff", "admin")
def quick_check(venue_id):
    venue = Venue.query.get_or_404(venue_id)

    next_url = normalize_next_path(request.values.get("next"), url_for("main.venues"))
    entered_from_profile = urlparse(next_url).path == url_for("main.venue_detail", venue_id=venue.id)

    # Items tracked in this venue (active mappings, active items)
    tracked = (
        db.session.query(Item)
        .options(selectinload(Item.parent_item))
        .join(VenueItem, VenueItem.item_id == Item.id)
        .filter(
            VenueItem.venue_id == venue.id,
            VenueItem.active == True,
            Item.active == True,
            Item.is_group_parent == False,
        )
        .all()
    )
    tracked = sorted(tracked, key=operational_item_sort_key)

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
            existing_counts = {
                row.item_id: row
                for row in VenueItemCount.query.filter_by(venue_id=venue.id).all()
            }
            quantity_items = [it for it in tracked if it.tracking_mode != "singleton_asset"]
            singleton_items = [it for it in tracked if it.tracking_mode == "singleton_asset"]

            count_session = None
            if quantity_items:
                count_session = CountSession(venue_id=venue.id, user_id=current_user.id)
                db.session.add(count_session)
                db.session.flush()

            for it in quantity_items:
                raw_value = (request.form.get(f"count_{it.id}") or "").strip()
                if raw_value == "":
                    # Keep blank counts as "Not Counted" (no CountLine and no VenueItemCount row).
                    continue

                try:
                    raw_count = int(raw_value)
                except ValueError:
                    continue

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

            if singleton_items:
                chk = Check(venue_id=venue.id, user_id=current_user.id)
                db.session.add(chk)
                db.session.flush()

                for it in singleton_items:
                    status = normalize_singleton_status(request.form.get(f"status_{it.id}"))
                    db.session.add(CheckLine(check_id=chk.id, item_id=it.id, status=status))
                    sync_singleton_compat_count(existing_counts, venue.id, it.id, status)

            db.session.commit()
            flash("Saved updates.", "success")
            return redirect(
                url_for(
                    "venue_items.quick_check",
                    venue_id=venue.id,
                    next=next_url,
                    mode="raw_counts",
                )
            )

        # Create a new status check (existing behavior)
        chk = Check(venue_id=venue.id, user_id=current_user.id)
        db.session.add(chk)
        db.session.flush()  # assign chk.id

        # For each tracked item, read status from form
        for it in tracked:
            status = (request.form.get(f"status_{it.id}") or "not_checked").strip().lower()
            if it.tracking_mode == "singleton_asset":
                status = normalize_singleton_status(status)
            elif status not in ("good", "ok", "low", "out", "not_checked"):
                status = "not_checked"

            db.session.add(CheckLine(check_id=chk.id, item_id=it.id, status=status))

        existing_counts = {
            row.item_id: row
            for row in VenueItemCount.query.filter_by(venue_id=venue.id).all()
        }
        for it in tracked:
            if it.tracking_mode != "singleton_asset":
                continue
            sync_singleton_compat_count(
                existing_counts,
                venue.id,
                it.id,
                request.form.get(f"status_{it.id}"),
            )

        db.session.commit()
        flash("Saved check.", "success")
        return redirect(
            url_for("venue_items.quick_check", venue_id=venue.id, next=next_url, mode="status")
        )

    latest_counts = {}
    for it in tracked:
        row = (
            db.session.query(VenueItemCount.raw_count)
            .filter(VenueItemCount.venue_id == venue.id, VenueItemCount.item_id == it.id)
            .first()
        )
        latest_counts[it.id] = row[0] if row else None

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
        resolved_status = row[0] if row else "not_checked"
        if it.tracking_mode == "singleton_asset":
            if row is None:
                resolved_status = infer_singleton_status_from_count(latest_counts.get(it.id))
            else:
                resolved_status = normalize_singleton_status(resolved_status)
            latest_counts[it.id] = derive_singleton_count(resolved_status)
        latest_status[it.id] = resolved_status

    overall_counts = {"good": 0, "ok": 0, "low": 0, "out": 0, "not_checked": 0}
    for status in latest_status.values():
        normalized = (status or "not_checked").strip().lower()
        if normalized not in overall_counts:
            normalized = "not_checked"
        overall_counts[normalized] += 1
    overall_status = build_overall_status(len(tracked), overall_counts)
    quick_check_groups = build_quick_check_groups(tracked, latest_status, latest_counts)

    return render_template(
        "venues/quick_check.html",
        venue=venue,
        items=tracked,
        quick_check_groups=quick_check_groups,
        latest_status=latest_status,
        latest_counts=latest_counts,
        selected_mode=selected_mode,
        next_url=next_url,
        show_profile_link=not entered_from_profile,
        back_label=describe_back_destination(next_url, venue.id),
        overall_status=overall_status,
    )
