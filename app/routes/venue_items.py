from datetime import datetime, timezone
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
from app.services.inventory_status import (
    build_overall_status_badge as shared_build_overall_status_badge,
    derive_singleton_count_from_status as shared_derive_singleton_count,
    infer_singleton_status_from_count as shared_infer_singleton_status_from_count,
    normalize_singleton_status as shared_normalize_singleton_status,
)
from app.services.inventory_rules import resolve_effective_par_level

venue_items_bp = Blueprint("venue_items", __name__, url_prefix="/venues")
MAX_DB_INT = 2_147_483_647


def normalize_singleton_status(status):
    return shared_normalize_singleton_status(status)


def derive_singleton_count(status):
    return shared_derive_singleton_count(status)


def infer_singleton_status_from_count(raw_count):
    return shared_infer_singleton_status_from_count(raw_count)


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
    detail_counts = counts.get("_detail", {}) if isinstance(counts, dict) else {}
    return shared_build_overall_status_badge(total_tracked, counts, detail_counts)


def sanitize_raw_count(raw_value):
    try:
        parsed = int((raw_value or "0").strip())
    except ValueError:
        return 0, True

    if parsed < 0:
        return 0, True
    if parsed > MAX_DB_INT:
        return MAX_DB_INT, True
    return parsed, False


def normalize_quick_count_submission(raw_value):
    normalized = (raw_value or "").strip()
    if normalized == "":
        return None, False

    try:
        parsed = int(normalized)
    except ValueError:
        return None, False

    adjusted = False
    if parsed < 0:
        parsed = 0
        adjusted = True
    if parsed > MAX_DB_INT:
        parsed = MAX_DB_INT
        adjusted = True
    return parsed, adjusted


def quick_check_save_message(*, count_update_count=0, status_update_count=0):
    parts = []
    if count_update_count:
        parts.append(f"{count_update_count} count update{'s' if count_update_count != 1 else ''}")
    if status_update_count:
        parts.append(f"{status_update_count} status update{'s' if status_update_count != 1 else ''}")

    if not parts:
        return "Saved updates."
    if len(parts) == 2:
        return f"Saved {parts[0]} and {parts[1]}."
    return f"Saved {parts[0]}."


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


def build_family_status_label(total_tracked, counts, has_singleton_children=False, has_quantity_children=True):
    resolved_key = resolve_status_key_from_counts(total_tracked, counts)
    asset_only = has_singleton_children and not has_quantity_children

    if asset_only:
        if resolved_key == "out":
            count = counts["out"]
            return f"{count} missing" if count > 0 else "Missing"
        if resolved_key == "low":
            count = counts["low"]
            return f"{count} damaged" if count > 0 else "Damaged"
        return {
            "good": "Present",
            "ok": "OK",
            "not_checked": "Not checked",
        }.get(resolved_key, "Not checked")

    if resolved_key == "out":
        count = counts["out"]
        if counts["out"] > 0 and has_singleton_children and has_quantity_children:
            label = "item needs attention" if count == 1 else "items need attention"
            return f"{count} {label}"
        label = "item out" if count == 1 else "items out"
        return f"{count} {label}"
    if resolved_key == "low":
        count = counts["low"]
        if counts["low"] > 0 and has_singleton_children and has_quantity_children:
            label = "item needs attention" if count == 1 else "items need attention"
            return f"{count} {label}"
        label = "item low" if count == 1 else "items low"
        return f"{count} {label}"
    return {
        "good": "Good",
        "ok": "OK",
        "not_checked": "Not checked",
    }.get(resolved_key, "Not checked")


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


def normalize_quick_check_submission(item, raw_status):
    normalized = (raw_status or "").strip().lower()
    if normalized == "":
        return None

    if item.tracking_mode == "singleton_asset":
        return normalize_singleton_status(normalized)

    if normalized not in {"good", "ok", "low", "out", "not_checked"}:
        return None
    return normalized


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
        group["worst_status_label"] = build_family_status_label(
            group["child_count"],
            group["status_counts"],
            has_singleton_children=group["has_singleton_children"],
            has_quantity_children=group["has_quantity_children"],
        )
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
        flash("Tracked-item setup now lives on Venue Settings.", "info")
    return redirect(url_for("venue_settings.settings", venue_id=venue.id, next=next_url))

@venue_items_bp.route("/<int:venue_id>/check", methods=["GET", "POST"])
@roles_required("viewer", "staff", "admin")
def quick_check(venue_id):
    venue = Venue.query.get_or_404(venue_id)

    next_url = normalize_next_path(request.values.get("next"), url_for("main.venues"))
    entered_from_profile = urlparse(next_url).path == url_for("main.venue_detail", venue_id=venue.id)

    # Items tracked in this venue (active mappings, active items)
    tracked_rows = (
        db.session.query(Item, VenueItem.expected_qty.label("venue_par_override"))
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
    tracked_rows = sorted(tracked_rows, key=lambda row: operational_item_sort_key(row[0]))
    tracked = [item for item, _ in tracked_rows]
    effective_par_by_item = {
        item.id: resolve_effective_par_level(
            item_default_par_level=item.default_par_level,
            venue_par_override=venue_par_override,
        ).value
        for item, venue_par_override in tracked_rows
    }

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
            adjusted_inputs = 0
            quantity_items = [it for it in tracked if it.tracking_mode != "singleton_asset"]
            singleton_items = [it for it in tracked if it.tracking_mode == "singleton_asset"]
            quantity_count_updates = []
            singleton_status_updates = []

            for it in quantity_items:
                raw_count, adjusted = normalize_quick_count_submission(request.form.get(f"count_{it.id}"))
                if raw_count is None:
                    continue
                if adjusted:
                    adjusted_inputs += 1
                quantity_count_updates.append((it, raw_count))

            for it in singleton_items:
                status = normalize_quick_check_submission(
                    it,
                    request.form.get(f"status_{it.id}"),
                )
                if status is None:
                    continue
                singleton_status_updates.append((it, status))

            if not quantity_count_updates and not singleton_status_updates:
                flash("Add at least one count or status update before saving.", "warning")
                return redirect(
                    url_for(
                        "venue_items.quick_check",
                        venue_id=venue.id,
                        next=next_url,
                        mode="raw_counts",
                    )
                )

            if quantity_count_updates:
                count_session = CountSession(venue_id=venue.id, user_id=current_user.id)
                db.session.add(count_session)
                db.session.flush()

                refreshed_at = datetime.now(timezone.utc)
                for it, raw_count in quantity_count_updates:
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
                        current.updated_at = refreshed_at
                    else:
                        current = VenueItemCount(
                            venue_id=venue.id,
                            item_id=it.id,
                            raw_count=raw_count,
                            updated_at=refreshed_at,
                        )
                        db.session.add(current)
                        existing_counts[it.id] = current

            if singleton_status_updates:
                chk = Check(venue_id=venue.id, user_id=current_user.id)
                db.session.add(chk)
                db.session.flush()

                for it, status in singleton_status_updates:
                    db.session.add(CheckLine(check_id=chk.id, item_id=it.id, status=status))
                    sync_singleton_compat_count(existing_counts, venue.id, it.id, status)

            db.session.commit()
            if adjusted_inputs:
                flash(
                    f"{adjusted_inputs} raw count value(s) were out of range and adjusted to fit database limits.",
                    "warning",
                )
            flash(
                quick_check_save_message(
                    count_update_count=len(quantity_count_updates),
                    status_update_count=len(singleton_status_updates),
                ),
                "success",
            )
            return redirect(
                url_for(
                    "venue_items.quick_check",
                    venue_id=venue.id,
                    next=next_url,
                    mode="raw_counts",
                )
            )

        selected_status_updates = []
        for it in tracked:
            normalized_status = normalize_quick_check_submission(
                it,
                request.form.get(f"status_{it.id}"),
            )
            if normalized_status is None:
                continue
            selected_status_updates.append((it, normalized_status))

        if not selected_status_updates:
            flash("Select at least one status to record a fresh quick check.", "warning")
            return redirect(
                url_for("venue_items.quick_check", venue_id=venue.id, next=next_url, mode="status")
            )

        chk = Check(venue_id=venue.id, user_id=current_user.id)
        db.session.add(chk)
        db.session.flush()

        for it, status in selected_status_updates:
            db.session.add(CheckLine(check_id=chk.id, item_id=it.id, status=status))

        selected_singleton_updates = [
            (it, status) for it, status in selected_status_updates if it.tracking_mode == "singleton_asset"
        ]
        if selected_singleton_updates:
            existing_counts = {
                row.item_id: row
                for row in VenueItemCount.query.filter_by(venue_id=venue.id).all()
            }
            for it, status in selected_singleton_updates:
                sync_singleton_compat_count(
                    existing_counts,
                    venue.id,
                    it.id,
                    status,
                )

        db.session.commit()
        flash(
            quick_check_save_message(status_update_count=len(selected_status_updates)),
            "success",
        )
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
    overall_detail_counts = {
        "low_quantity": 0,
        "low_singleton": 0,
        "out_quantity": 0,
        "out_singleton": 0,
    }
    for it in tracked:
        status = latest_status.get(it.id)
        normalized = (status or "not_checked").strip().lower()
        if normalized not in overall_counts:
            normalized = "not_checked"
        overall_counts[normalized] += 1
        if normalized in {"low", "out"}:
            suffix = "singleton" if it.tracking_mode == "singleton_asset" else "quantity"
            overall_detail_counts[f"{normalized}_{suffix}"] += 1
    overall_counts["_detail"] = overall_detail_counts
    overall_status = build_overall_status(len(tracked), overall_counts)
    quick_check_groups = build_quick_check_groups(tracked, latest_status, latest_counts)

    return render_template(
        "venues/quick_check.html",
        venue=venue,
        items=tracked,
        quick_check_groups=quick_check_groups,
        latest_status=latest_status,
        latest_counts=latest_counts,
        effective_par_by_item=effective_par_by_item,
        selected_mode=selected_mode,
        next_url=next_url,
        show_profile_link=not entered_from_profile,
        back_label=describe_back_destination(next_url, venue.id),
        overall_status=overall_status,
    )
