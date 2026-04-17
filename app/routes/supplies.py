from datetime import datetime, timedelta, timezone
from math import ceil

from flask import Blueprint, render_template, request
from sqlalchemy import and_
from sqlalchemy.orm import aliased

from app import db
from app.authz import roles_required
from app.models import Check, CheckLine, Item, Venue, VenueItem, VenueItemCount

supplies_bp = Blueprint("supplies", __name__)

SUPPLY_SORT_OPTIONS = {"par_ratio_asc", "missing_venues_desc", "last_updated", "item_name"}
SUPPLY_QUICK_FILTER_OPTIONS = {
    "missing_counts",
    "below_par",
    "complete_coverage",
    "consumable",
    "durable",
    "singleton_asset",
}
STALE_UPDATE_THRESHOLD = timedelta(days=2)


def normalize_singleton_status_key(value):
    normalized = (value or "not_checked").strip().lower()
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


def derive_singleton_count_from_status(value):
    normalized = normalize_singleton_status_key(value)
    if normalized in {"good", "low"}:
        return 1
    if normalized == "out":
        return 0
    return None


def ensure_utc(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def format_supply_timestamp(value):
    normalized = ensure_utc(value)
    if normalized is None:
        return "No counts yet"
    return normalized.strftime("%Y-%m-%d %I:%M %p")


def build_supply_timestamp_parts(value):
    normalized = ensure_utc(value)
    if normalized is None:
        return {
            "date_text": "No counts yet",
            "time_text": "",
        }
    return {
        "date_text": normalized.strftime("%Y-%m-%d"),
        "time_text": normalized.strftime("%I:%M %p"),
    }


def build_supply_updated_label(value):
    updated_at = ensure_utc(value)
    if updated_at is None:
        return {
            "text": "No counts yet",
            "is_missing": True,
            "is_stale": True,
        }

    now = datetime.now(timezone.utc)
    delta = max(now - updated_at, timedelta(0))
    total_seconds = int(delta.total_seconds())

    if total_seconds < 60:
        relative = "Just now"
    elif total_seconds < 3600:
        relative = f"{total_seconds // 60}m ago"
    elif total_seconds < 86400:
        relative = f"{total_seconds // 3600}h ago"
    else:
        relative = f"{total_seconds // 86400}d ago"

    return {
        "text": relative,
        "is_missing": False,
        "is_stale": delta >= STALE_UPDATE_THRESHOLD,
    }


def is_supply_update_stale(value):
    updated_at = ensure_utc(value)
    if updated_at is None:
        return False
    now = datetime.now(timezone.utc)
    return max(now - updated_at, timedelta(0)) >= STALE_UPDATE_THRESHOLD


def build_family_progress(counted, total, attention_level):
    if total <= 0:
        return {
            "segments": [False] * 10,
            "tone": "none",
            "label": "No variants",
        }

    ratio = counted / total
    filled_segments = min(10, max(0, ceil(ratio * 10)))

    if counted <= 0:
        tone = "low"
    elif counted < total or attention_level == "critical":
        tone = "low" if attention_level == "critical" else "caution"
    elif attention_level == "warning":
        tone = "caution"
    else:
        tone = "healthy"

    return {
        "segments": [index < filled_segments for index in range(10)],
        "tone": tone,
        "label": f"{counted}/{total} counted",
    }


def normalize_item_type(value):
    normalized = (value or "all").strip().lower()
    if normalized not in {"all", "durable", "consumable"}:
        return "all"
    return normalized


def normalize_coverage(value):
    normalized = (value or "all").strip().lower()
    if normalized not in {"all", "complete", "partial", "no_counts", "not_tracked"}:
        return "all"
    return normalized


def normalize_sort(value):
    normalized = (value or "par_ratio_asc").strip().lower()
    if normalized not in SUPPLY_SORT_OPTIONS:
        return "par_ratio_asc"
    return normalized


def normalize_quick_filters(values):
    normalized = []
    for value in values or []:
        key = (value or "").strip().lower()
        if key in SUPPLY_QUICK_FILTER_OPTIONS and key not in normalized:
            normalized.append(key)
    return normalized


def build_par_progress(total_raw_count, total_par_count):
    segment_count = 10
    empty_segments = [False] * segment_count

    if total_par_count is None:
        return {
            "has_par": False,
            "segments": empty_segments,
            "tone": "none",
            "label": "No par set",
        }

    if total_par_count <= 0:
        return {
            "has_par": True,
            "segments": empty_segments,
            "tone": "none",
            "label": "Par is 0",
        }

    ratio = total_raw_count / total_par_count
    filled_segments = min(segment_count, max(1 if total_raw_count > 0 else 0, ceil(ratio * segment_count)))
    if ratio >= 1:
        tone = "full"
    elif ratio >= 0.75:
        tone = "healthy"
    elif ratio >= 0.4:
        tone = "caution"
    else:
        tone = "low"

    return {
        "has_par": True,
        "segments": [index < filled_segments for index in range(segment_count)],
        "tone": tone,
        "label": f"{round(ratio * 100)}% of par",
    }


def build_singleton_progress(present_count, tracked_count, missing_count):
    segment_count = 10
    empty_segments = [False] * segment_count
    if tracked_count <= 0:
        return {
            "has_par": False,
            "segments": empty_segments,
            "tone": "none",
            "label": "No tracked venues",
        }

    ratio = present_count / tracked_count
    filled_segments = min(segment_count, max(1 if present_count > 0 else 0, ceil(ratio * segment_count)))
    if ratio >= 1 and missing_count <= 0:
        tone = "full"
    elif ratio >= 0.75:
        tone = "healthy"
    elif ratio >= 0.4:
        tone = "caution"
    else:
        tone = "low"
    return {
        "has_par": False,
        "segments": [index < filled_segments for index in range(segment_count)],
        "tone": tone,
        "label": f"{present_count}/{tracked_count} venues present",
    }


def build_par_ratio(total_raw_count, total_par_count):
    if total_par_count is None or total_par_count <= 0:
        return None
    return total_raw_count / total_par_count


def build_singleton_primary_metric(row):
    tracked_count = row["tracked_venue_count"]
    present_count = row["singleton_present_venue_count"]
    if tracked_count <= 0:
        return {
            "value": "—",
            "note": "not tracked",
        }
    return {
        "value": f"{present_count}/{tracked_count}",
        "note": "present",
    }


def build_singleton_condition_summary(row):
    return " · ".join(
        [
            f'{row["singleton_damaged_venue_count"]} damaged',
            f'{row["singleton_missing_venue_count"]} missing',
            f'{row["missing_count_venue_count"]} unchecked',
        ]
    )


def build_issue_summary(row):
    if row["tracking_mode"] == "singleton_asset":
        if row["tracked_venue_count"] == 0:
            return "No active venue assignments"
        if row["missing_count_venue_count"] > 0:
            label = "venue" if row["missing_count_venue_count"] == 1 else "venues"
            return f'{row["missing_count_venue_count"]} not checked {label}'
        if row["singleton_missing_venue_count"] > 0 and row["singleton_damaged_venue_count"] > 0:
            missing_label = "venue" if row["singleton_missing_venue_count"] == 1 else "venues"
            damaged_label = "venue" if row["singleton_damaged_venue_count"] == 1 else "venues"
            return (
                f'{row["singleton_missing_venue_count"]} missing {missing_label}'
                f' · {row["singleton_damaged_venue_count"]} damaged {damaged_label}'
            )
        if row["singleton_missing_venue_count"] > 0:
            label = "venue" if row["singleton_missing_venue_count"] == 1 else "venues"
            return f'{row["singleton_missing_venue_count"]} missing {label}'
        if row["singleton_damaged_venue_count"] > 0:
            label = "venue" if row["singleton_damaged_venue_count"] == 1 else "venues"
            return f'{row["singleton_damaged_venue_count"]} damaged {label}'
        if row["stale_update_venue_count"] > 0:
            label = "update" if row["stale_update_venue_count"] == 1 else "updates"
            return f'{row["stale_update_venue_count"]} stale {label}'
        return "All tracked venues reported present"

    if row["tracked_venue_count"] == 0:
        return "No active venue assignments"
    if row["missing_count_venue_count"] > 0:
        label = "venue" if row["missing_count_venue_count"] == 1 else "venues"
        return f'{row["missing_count_venue_count"]} missing {label}'
    if row["stale_update_venue_count"] > 0:
        label = "update" if row["stale_update_venue_count"] == 1 else "updates"
        return f'{row["stale_update_venue_count"]} stale {label}'
    if row["zero_count_venue_count"] > 0:
        label = "venue" if row["zero_count_venue_count"] == 1 else "venues"
        return f'{row["zero_count_venue_count"]} zero-count {label}'
    return "All tracked venues counted"


def build_desktop_issue_summary(row, updated_label):
    issues = []

    if row["tracking_mode"] == "singleton_asset":
        if row["tracked_venue_count"] == 0:
            issues.append("Not tracked yet")
        elif row["counted_venue_count"] == 0:
            issues.append("No checks recorded")
        elif row["missing_count_venue_count"] > 0:
            label = "venue" if row["missing_count_venue_count"] == 1 else "venues"
            issues.append(f'{row["missing_count_venue_count"]} not checked {label}')
        if row["stale_update_venue_count"] > 0:
            label = "update" if row["stale_update_venue_count"] == 1 else "updates"
            issues.append(f'{row["stale_update_venue_count"]} stale {label}')
        if row["singleton_missing_venue_count"] > 0:
            label = "venue" if row["singleton_missing_venue_count"] == 1 else "venues"
            issues.append(f'{row["singleton_missing_venue_count"]} missing {label}')
        if row["singleton_damaged_venue_count"] > 0:
            label = "venue" if row["singleton_damaged_venue_count"] == 1 else "venues"
            issues.append(f'{row["singleton_damaged_venue_count"]} damaged {label}')
    else:
        if row["tracked_venue_count"] == 0:
            issues.append("Not tracked yet")
        elif row["counted_venue_count"] == 0:
            issues.append("No counts recorded")
        elif row["missing_count_venue_count"] > 0:
            label = "venue" if row["missing_count_venue_count"] == 1 else "venues"
            issues.append(f'{row["missing_count_venue_count"]} missing {label}')
        if row["stale_update_venue_count"] > 0:
            label = "update" if row["stale_update_venue_count"] == 1 else "updates"
            issues.append(f'{row["stale_update_venue_count"]} stale {label}')
        if row["is_below_par"]:
            issues.append("Below par")

    if updated_label["is_missing"] and row["tracked_venue_count"] > 0:
        issues.append("Never updated")

    if not issues and row["zero_count_venue_count"] > 0:
        label = "venue" if row["zero_count_venue_count"] == 1 else "venues"
        issues.append(f'{row["zero_count_venue_count"]} zero-count {label}')

    return " · ".join(issues[:3]) if issues else "Fully counted"


def build_attention_level(row, updated_label):
    if row["tracked_venue_count"] == 0 or row["counted_venue_count"] == 0 or row["missing_count_venue_count"] > 0:
        return "critical"
    if row["tracking_mode"] == "singleton_asset" and row["singleton_missing_venue_count"] > 0:
        return "critical"
    if row["tracking_mode"] == "singleton_asset" and row["singleton_damaged_venue_count"] > 0:
        return "warning"
    if row["is_below_par"] or row["stale_update_venue_count"] > 0:
        return "warning"
    return "healthy"


def coverage_meta_for_row(row):
    if row["tracked_venue_count"] == 0:
        return {
            "key": "not_tracked",
            "label": "Not Tracked",
            "summary": "No active venues are tracking this item yet",
            "compact_summary": "No venues assigned",
        }
    if row["counted_venue_count"] == 0:
        return {
            "key": "no_counts",
            "label": "No Counts",
            "summary": f'0 / {row["tracked_venue_count"]} venues counted',
            "compact_summary": f'0/{row["tracked_venue_count"]} counted',
        }
    if row["missing_count_venue_count"] > 0:
        return {
            "key": "partial",
            "label": "Partial",
            "summary": f'{row["counted_venue_count"]} / {row["tracked_venue_count"]} venues counted',
            "compact_summary": (
                f'{row["counted_venue_count"]}/{row["tracked_venue_count"]} counted'
                f' · {row["missing_count_venue_count"]} missing'
            ),
        }
    return {
        "key": "complete",
        "label": "Complete",
        "summary": f'{row["counted_venue_count"]} / {row["tracked_venue_count"]} venues counted',
        "compact_summary": f'{row["counted_venue_count"]}/{row["tracked_venue_count"]} counted',
    }


def build_supply_audit_rows():
    parent_alias = aliased(Item)
    active_items = (
        db.session.query(
            Item.id,
            Item.name,
            Item.item_type,
            Item.item_category,
            Item.tracking_mode,
            Item.parent_item_id,
            parent_alias.name.label("parent_name"),
        )
        .outerjoin(parent_alias, parent_alias.id == Item.parent_item_id)
        .filter(Item.active == True, Item.is_group_parent == False)
        .order_by(Item.name.asc())
        .all()
    )

    items_by_id = {
        item.id: {
            "id": item.id,
            "name": item.name,
            "item_type": item.item_type,
            "item_category": item.item_category or item.item_type,
            "tracking_mode": item.tracking_mode or "quantity",
            "parent_item_id": item.parent_item_id,
            "parent_name": item.parent_name,
            "total_raw_count": 0,
            "tracked_venue_count": 0,
            "counted_venue_count": 0,
            "missing_count_venue_count": 0,
            "zero_count_venue_count": 0,
            "stale_update_venue_count": 0,
            "singleton_present_venue_count": 0,
            "singleton_missing_venue_count": 0,
            "singleton_damaged_venue_count": 0,
            "total_par_count": 0,
            "par_venue_count": 0,
            "last_count_updated_at": None,
            "venue_rows": [],
        }
        for item in active_items
    }

    singleton_status_rows = (
        db.session.query(
            Check.venue_id.label("venue_id"),
            CheckLine.item_id.label("item_id"),
            CheckLine.status.label("status"),
            Check.created_at.label("updated_at"),
        )
        .select_from(Check)
        .join(CheckLine, CheckLine.check_id == Check.id)
        .join(Item, Item.id == CheckLine.item_id)
        .join(
            VenueItem,
            and_(
                VenueItem.venue_id == Check.venue_id,
                VenueItem.item_id == CheckLine.item_id,
            ),
        )
        .join(Venue, Venue.id == Check.venue_id)
        .filter(
            VenueItem.active == True,
            Item.active == True,
            Item.is_group_parent == False,
            Item.tracking_mode == "singleton_asset",
            Venue.active == True,
        )
        .order_by(Venue.id.asc(), Item.id.asc(), Check.created_at.desc())
        .all()
    )

    latest_singleton_status_by_venue_item = {}
    for status_row in singleton_status_rows:
        key = (status_row.venue_id, status_row.item_id)
        if key in latest_singleton_status_by_venue_item:
            continue
        latest_singleton_status_by_venue_item[key] = {
            "status": normalize_singleton_status_key(status_row.status),
            "updated_at": ensure_utc(status_row.updated_at),
        }

    query_rows = (
        db.session.query(
            Item.id.label("item_id"),
            Item.name.label("item_name"),
            Item.item_type.label("item_type"),
            Item.item_category.label("item_category"),
            Item.tracking_mode.label("tracking_mode"),
            Item.parent_item_id.label("parent_item_id"),
            parent_alias.name.label("parent_name"),
            Venue.id.label("venue_id"),
            Venue.name.label("venue_name"),
            VenueItem.expected_qty.label("par_count"),
            VenueItemCount.raw_count.label("raw_count"),
            VenueItemCount.updated_at.label("updated_at"),
        )
        .select_from(VenueItem)
        .join(Item, Item.id == VenueItem.item_id)
        .outerjoin(parent_alias, parent_alias.id == Item.parent_item_id)
        .join(Venue, Venue.id == VenueItem.venue_id)
        .outerjoin(
            VenueItemCount,
            and_(
                VenueItemCount.venue_id == VenueItem.venue_id,
                VenueItemCount.item_id == VenueItem.item_id,
            ),
        )
        .filter(
            VenueItem.active == True,
            Item.active == True,
            Item.is_group_parent == False,
            Venue.active == True,
        )
        .order_by(Item.name.asc(), Venue.name.asc())
        .all()
    )

    for row in query_rows:
        item_row = items_by_id[row.item_id]
        is_singleton = item_row["tracking_mode"] == "singleton_asset"
        singleton_status_meta = latest_singleton_status_by_venue_item.get((row.venue_id, row.item_id))
        singleton_status = "not_checked"
        updated_at = ensure_utc(row.updated_at)
        effective_raw_count = row.raw_count

        if is_singleton:
            if singleton_status_meta:
                singleton_status = singleton_status_meta["status"]
                updated_at = singleton_status_meta["updated_at"] or updated_at
            else:
                singleton_status = normalize_singleton_status_key(
                    "good" if row.raw_count and row.raw_count > 0 else ("out" if row.raw_count == 0 else "not_checked")
                )
            effective_raw_count = derive_singleton_count_from_status(singleton_status)

        item_row["tracked_venue_count"] += 1

        if row.par_count is not None:
            item_row["total_par_count"] += row.par_count
            item_row["par_venue_count"] += 1

        if effective_raw_count is None:
            item_row["missing_count_venue_count"] += 1
        else:
            item_row["counted_venue_count"] += 1
            item_row["total_raw_count"] += effective_raw_count
            venue_is_stale = is_supply_update_stale(updated_at)
            if venue_is_stale:
                item_row["stale_update_venue_count"] += 1
            if is_singleton:
                if singleton_status == "low":
                    item_row["singleton_damaged_venue_count"] += 1
                    item_row["singleton_present_venue_count"] += 1
                elif singleton_status == "good":
                    item_row["singleton_present_venue_count"] += 1
                elif singleton_status == "out":
                    item_row["singleton_missing_venue_count"] += 1
            elif effective_raw_count == 0:
                item_row["zero_count_venue_count"] += 1

            if updated_at and (
                item_row["last_count_updated_at"] is None
                or updated_at > item_row["last_count_updated_at"]
            ):
                item_row["last_count_updated_at"] = updated_at

        item_row["venue_rows"].append(
            {
                "venue_id": row.venue_id,
                "venue_name": row.venue_name,
                "raw_count": effective_raw_count,
                "status_key": singleton_status if is_singleton else None,
                "raw_count_text": "Not Counted" if effective_raw_count is None else str(effective_raw_count),
                "is_missing": effective_raw_count is None,
                "is_stale": is_supply_update_stale(updated_at) if effective_raw_count is not None else False,
                "par_count": row.par_count,
                "par_count_text": "Not Set" if row.par_count is None else str(row.par_count),
                "updated_at_text": (
                    "No checks yet"
                    if is_singleton and updated_at is None
                    else format_supply_timestamp(updated_at)
                ),
            }
        )

    supply_rows = []
    for item_row in items_by_id.values():
        coverage_meta = coverage_meta_for_row(item_row)
        is_singleton = item_row["tracking_mode"] == "singleton_asset"
        if is_singleton:
            if coverage_meta["key"] == "no_counts":
                coverage_meta["label"] = "No Checks"
                coverage_meta["summary"] = f'0 / {item_row["tracked_venue_count"]} venues checked'
                coverage_meta["compact_summary"] = f'0/{item_row["tracked_venue_count"]} checked'
            elif coverage_meta["key"] in {"partial", "complete"}:
                coverage_meta["summary"] = f'{item_row["counted_venue_count"]} / {item_row["tracked_venue_count"]} venues checked'
                compact_summary = f'{item_row["counted_venue_count"]}/{item_row["tracked_venue_count"]} checked'
                if coverage_meta["key"] == "partial" and item_row["missing_count_venue_count"] > 0:
                    compact_summary = f'{compact_summary} · {item_row["missing_count_venue_count"]} missing'
                coverage_meta["compact_summary"] = compact_summary
        total_par_count = item_row["total_par_count"] if item_row["par_venue_count"] > 0 and not is_singleton else None
        item_row["par_ratio"] = build_par_ratio(item_row["total_raw_count"], total_par_count)
        item_row["is_below_par"] = bool(
            not is_singleton
            and total_par_count is not None
            and total_par_count > 0
            and item_row["total_raw_count"] < total_par_count
        )
        updated_label = build_supply_updated_label(item_row["last_count_updated_at"])
        updated_parts = build_supply_timestamp_parts(item_row["last_count_updated_at"])
        updated_text = format_supply_timestamp(item_row["last_count_updated_at"])
        if is_singleton and updated_label["is_missing"]:
            updated_label["text"] = "No checks yet"
            updated_parts = {
                "date_text": "No checks yet",
                "time_text": "",
            }
            updated_text = "No checks yet"
        item_row["coverage_key"] = coverage_meta["key"]
        item_row["coverage_label"] = coverage_meta["label"]
        item_row["coverage_summary"] = coverage_meta["summary"]
        item_row["coverage_compact_summary"] = coverage_meta["compact_summary"]
        item_row["total_par_count"] = total_par_count
        item_row["total_par_count_text"] = str(total_par_count) if total_par_count is not None else "Not Set"
        item_row["last_count_updated_at_text"] = updated_text
        item_row["last_count_updated_parts"] = updated_parts
        item_row["last_count_updated_label"] = updated_label
        item_row["last_updated_timestamp"] = (
            item_row["last_count_updated_at"].timestamp() if item_row["last_count_updated_at"] else None
        )
        if is_singleton:
            item_row["par_progress"] = build_singleton_progress(
                item_row["singleton_present_venue_count"],
                item_row["tracked_venue_count"],
                item_row["missing_count_venue_count"],
            )
            singleton_primary_metric = build_singleton_primary_metric(item_row)
            item_row["singleton_primary_metric"] = singleton_primary_metric["value"]
            item_row["singleton_primary_note"] = singleton_primary_metric["note"]
            item_row["singleton_condition_summary"] = build_singleton_condition_summary(item_row)
        else:
            item_row["par_progress"] = build_par_progress(item_row["total_raw_count"], total_par_count)
        item_row["tracking_mode_label"] = "Singleton Asset" if is_singleton else "Quantity"
        item_row["family_name"] = item_row["parent_name"] or item_row["name"]
        item_row["is_child_item"] = bool(item_row["parent_item_id"])
        item_row["issue_summary"] = build_issue_summary(item_row)
        item_row["desktop_issue_summary"] = build_desktop_issue_summary(item_row, updated_label)
        item_row["attention_level"] = build_attention_level(item_row, updated_label)
        supply_rows.append(item_row)

    return supply_rows


def build_supply_summary(rows):
    quantity_rows = [row for row in rows if row["tracking_mode"] != "singleton_asset"]
    singleton_rows = [row for row in rows if row["tracking_mode"] == "singleton_asset"]
    return {
        "quantity_units_on_hand": sum(row["total_raw_count"] for row in quantity_rows),
        "singleton_present_total": sum(row["singleton_present_venue_count"] for row in singleton_rows),
        "total_active_supply_items": len(rows),
        "singleton_assets": len(singleton_rows),
        "items_with_complete_count_coverage": sum(
            1 for row in rows if row["coverage_key"] == "complete"
        ),
        "items_with_missing_counts": sum(
            1 for row in rows if row["missing_count_venue_count"] > 0
        ),
        "family_variants": sum(1 for row in rows if row.get("parent_name")),
    }


def supply_attention_severity(value):
    return {
        "healthy": 0,
        "warning": 1,
        "critical": 2,
    }.get((value or "healthy").strip().lower(), 0)


def build_supply_display_groups(rows):
    groups = []
    families = {}

    for row in rows:
        parent_name = row.get("parent_name")
        if not parent_name:
            groups.append(
                {
                    "kind": "item",
                    "id": f'item-{row["id"]}',
                    "row": row,
                    "family_name": row["name"],
                    "sort_name": row["family_name"].lower(),
                    "search_text": " ".join(
                        filter(None, [row["name"], row.get("parent_name"), row.get("tracking_mode_label")])
                    ).lower(),
                }
            )
            continue

        family_key = (row["parent_item_id"], parent_name)
        group = families.get(family_key)
        if group is None:
            group = {
                "kind": "family",
                "id": f'family-{row["parent_item_id"] or row["id"]}',
                "family_id": row["parent_item_id"],
                "family_name": parent_name,
                "sort_name": row["family_name"].lower(),
                "children": [],
                "search_text": parent_name.lower(),
                "item_type": row["item_type"],
                "worst_attention_level": "healthy",
                "worst_attention_severity": -1,
                "missing_count_venue_count": 0,
                "stale_update_venue_count": 0,
                "counted_child_count": 0,
                "tracked_child_count": 0,
                "has_singleton_children": False,
                "has_quantity_children": False,
                "last_count_updated_at": None,
                "par_ratio": None,
            }
            families[family_key] = group
            groups.append(group)

        group["children"].append(row)
        group["search_text"] = f'{group["search_text"]} {row["name"].lower()}'
        group["missing_count_venue_count"] += row["missing_count_venue_count"]
        group["stale_update_venue_count"] += row.get("stale_update_venue_count", 0)
        if row["counted_venue_count"] > 0:
            group["counted_child_count"] += 1
        if row["tracked_venue_count"] > 0:
            group["tracked_child_count"] += 1
        if row["tracking_mode"] == "singleton_asset":
            group["has_singleton_children"] = True
        else:
            group["has_quantity_children"] = True

        severity = supply_attention_severity(row["attention_level"])
        if severity > group["worst_attention_severity"]:
            group["worst_attention_severity"] = severity
            group["worst_attention_level"] = row["attention_level"]

        updated_at = row.get("last_count_updated_at")
        if updated_at and (
            group["last_count_updated_at"] is None or updated_at > group["last_count_updated_at"]
        ):
            group["last_count_updated_at"] = updated_at

        ratio = row.get("par_ratio")
        if ratio is not None and (group["par_ratio"] is None or ratio < group["par_ratio"]):
            group["par_ratio"] = ratio

    for group in groups:
        if group["kind"] != "family":
            continue
        child_count = len(group["children"])
        group["child_count"] = child_count
        updated_label = build_supply_updated_label(group["last_count_updated_at"])
        if group["has_singleton_children"] and not group["has_quantity_children"] and updated_label["is_missing"]:
            updated_label["text"] = "No checks yet"
        group["last_count_updated_label"] = updated_label
        group["last_count_updated_parts"] = build_supply_timestamp_parts(group["last_count_updated_at"])
        group["last_count_updated_at_text"] = format_supply_timestamp(group["last_count_updated_at"])
        if group["has_singleton_children"] and not group["has_quantity_children"] and updated_label["is_missing"]:
            group["last_count_updated_parts"] = {"date_text": "No checks yet", "time_text": ""}
            group["last_count_updated_at_text"] = "No checks yet"
        group["last_updated_timestamp"] = (
            group["last_count_updated_at"].timestamp() if group["last_count_updated_at"] else None
        )
        if group["has_singleton_children"] and not group["has_quantity_children"]:
            group["tracking_summary"] = "Asset family"
            group["tracking_badge_kind"] = "asset"
            group["counted_summary"] = f'{group["counted_child_count"]} of {child_count} variants checked'
        elif group["has_quantity_children"] and not group["has_singleton_children"]:
            group["tracking_summary"] = "Quantity family"
            group["tracking_badge_kind"] = "quantity"
            group["counted_summary"] = f'{group["counted_child_count"]} of {child_count} variants counted'
        else:
            group["tracking_summary"] = "Mixed tracking family"
            group["tracking_badge_kind"] = "family"
            group["counted_summary"] = f'{group["counted_child_count"]} of {child_count} variants updated'
        if group["counted_child_count"] == 0:
            group["issue_summary"] = (
                "No variants checked yet"
                if group["has_singleton_children"] and not group["has_quantity_children"]
                else "No variants counted yet"
            )
            group["issue_summary_short"] = (
                "No variant checks yet"
                if group["has_singleton_children"] and not group["has_quantity_children"]
                else "No variant counts yet"
            )
        elif group["missing_count_venue_count"] > 0:
            group["issue_summary"] = f'{group["missing_count_venue_count"]} missing venue counts'
            group["issue_summary_short"] = f'{group["missing_count_venue_count"]} missing venue counts'
        elif group["stale_update_venue_count"] > 0:
            label = "update" if group["stale_update_venue_count"] == 1 else "updates"
            group["issue_summary"] = f'{group["stale_update_venue_count"]} stale {label}'
            group["issue_summary_short"] = f'{group["stale_update_venue_count"]} stale {label}'
        elif group["worst_attention_level"] == "critical":
            group["issue_summary"] = "At least one variant needs attention"
            group["issue_summary_short"] = "Variant needs attention"
        elif group["worst_attention_level"] == "warning":
            group["issue_summary"] = "Some variants are below par or stale"
            group["issue_summary_short"] = "Below par or stale"
        else:
            group["issue_summary"] = "All visible variants look healthy"
            group["issue_summary_short"] = "Variants look healthy"

        if group["counted_child_count"] == 0:
            group["coverage_key"] = "no_counts"
            group["coverage_label"] = (
                "No Checks" if group["has_singleton_children"] and not group["has_quantity_children"] else "No Counts"
            )
        elif group["counted_child_count"] < child_count or group["missing_count_venue_count"] > 0:
            group["coverage_key"] = "partial"
            group["coverage_label"] = "Partial"
        else:
            group["coverage_key"] = "complete"
            group["coverage_label"] = "Complete"

        group["family_progress"] = build_family_progress(
            group["counted_child_count"],
            child_count,
            group["worst_attention_level"],
        )
        if child_count <= 0:
            group["family_progress_caption"] = "No variants"
        else:
            progress_percent = round((group["counted_child_count"] / child_count) * 100)
            group["family_progress_caption"] = f"{progress_percent}% variants checked"

    return groups


def filter_supply_rows(rows, search_query, item_type, coverage, quick_filters=None):
    normalized_query = (search_query or "").strip().lower()
    quick_filters = quick_filters or []
    filtered_rows = []
    for row in rows:
        haystack = " ".join(filter(None, [row["name"], row.get("parent_name"), row.get("tracking_mode_label")])).lower()
        if normalized_query and normalized_query not in haystack:
            continue
        if item_type != "all" and row["item_type"] != item_type:
            continue
        if coverage != "all" and row["coverage_key"] != coverage:
            continue

        type_filters = [value for value in quick_filters if value in {"durable", "consumable"}]
        if type_filters and row["item_type"] not in type_filters:
            continue
        if "singleton_asset" in quick_filters and row["tracking_mode"] != "singleton_asset":
            continue
        if "missing_counts" in quick_filters and row["missing_count_venue_count"] <= 0:
            continue
        if "below_par" in quick_filters and not row["is_below_par"]:
            continue
        if "complete_coverage" in quick_filters and row["coverage_key"] != "complete":
            continue
        filtered_rows.append(row)
    return filtered_rows


def supply_sort_key(row, sort_key):
    family_name = (row.get("parent_name") or row["name"]).lower()
    display_name = row["name"].lower()

    if sort_key == "item_name":
        return (
            family_name,
            1 if row.get("parent_name") else 0,
            display_name,
            row["item_type"],
            row["id"],
        )
    if sort_key == "missing_venues_desc":
        return (
            -row["missing_count_venue_count"],
            -(row["tracked_venue_count"] - row["counted_venue_count"]),
            family_name,
            display_name,
        )
    if sort_key == "last_updated":
        has_updated_at = row["last_count_updated_at"] is not None
        timestamp = row["last_count_updated_at"].timestamp() if has_updated_at else float("-inf")
        return (
            0 if has_updated_at else 1,
            -timestamp,
            family_name,
            display_name,
        )
    if sort_key == "par_ratio_asc":
        has_ratio = row["par_ratio"] is not None
        ratio = row["par_ratio"] if has_ratio else float("inf")
        return (
            0 if has_ratio else 1,
            ratio,
            -row["missing_count_venue_count"],
            family_name,
            display_name,
        )
    return (
        -row["total_raw_count"],
        family_name,
        display_name,
        row["id"],
    )


@supplies_bp.route("/supplies")
@roles_required("viewer", "staff", "admin")
def index():
    filters = {
        "q": (request.args.get("q") or "").strip(),
        "item_type": normalize_item_type(request.args.get("item_type")),
        "coverage": normalize_coverage(request.args.get("coverage")),
        "sort": normalize_sort(request.args.get("sort")),
        "quick_filters": normalize_quick_filters(request.args.getlist("quick_filter")),
    }

    all_supply_rows = build_supply_audit_rows()
    summary = build_supply_summary(all_supply_rows)
    filtered_rows = filter_supply_rows(
        all_supply_rows,
        filters["q"],
        filters["item_type"],
        filters["coverage"],
        filters["quick_filters"],
    )
    filtered_rows = sorted(filtered_rows, key=lambda row: supply_sort_key(row, filters["sort"]))
    supply_groups = build_supply_display_groups(filtered_rows)

    return render_template(
        "supplies/index.html",
        summary=summary,
        filters=filters,
        supply_rows=filtered_rows,
        supply_groups=supply_groups,
        total_supply_items=len(all_supply_rows),
        filtered_supply_items=len(filtered_rows),
    )
