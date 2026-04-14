from datetime import timezone
from datetime import datetime, timedelta, timezone
from math import ceil

from flask import Blueprint, render_template, request
from sqlalchemy import and_

from app import db
from app.authz import roles_required
from app.models import Item, Venue, VenueItem, VenueItemCount

supplies_bp = Blueprint("supplies", __name__)

SUPPLY_SORT_OPTIONS = {"par_ratio_asc", "missing_venues_desc", "last_updated", "item_name"}
SUPPLY_QUICK_FILTER_OPTIONS = {
    "missing_counts",
    "below_par",
    "complete_coverage",
    "consumable",
    "durable",
}


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
        "is_stale": delta >= timedelta(days=2),
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


def build_par_ratio(total_raw_count, total_par_count):
    if total_par_count is None or total_par_count <= 0:
        return None
    return total_raw_count / total_par_count


def build_issue_summary(row):
    if row["tracked_venue_count"] == 0:
        return "No active venue assignments"
    if row["missing_count_venue_count"] > 0:
        label = "venue" if row["missing_count_venue_count"] == 1 else "venues"
        return f'{row["missing_count_venue_count"]} missing {label}'
    if row["zero_count_venue_count"] > 0:
        label = "venue" if row["zero_count_venue_count"] == 1 else "venues"
        return f'{row["zero_count_venue_count"]} zero-count {label}'
    return "All tracked venues counted"


def build_desktop_issue_summary(row, updated_label):
    issues = []

    if row["tracked_venue_count"] == 0:
        issues.append("Not tracked yet")
    elif row["counted_venue_count"] == 0:
        issues.append("No counts recorded")
    elif row["missing_count_venue_count"] > 0:
        label = "venue" if row["missing_count_venue_count"] == 1 else "venues"
        issues.append(f'{row["missing_count_venue_count"]} missing {label}')

    if row["is_below_par"]:
        issues.append("Below par")

    if updated_label["is_missing"] and row["tracked_venue_count"] > 0:
        issues.append("Never updated")
    elif updated_label["is_stale"]:
        issues.append("Stale update")

    if not issues and row["zero_count_venue_count"] > 0:
        label = "venue" if row["zero_count_venue_count"] == 1 else "venues"
        issues.append(f'{row["zero_count_venue_count"]} zero-count {label}')

    return " · ".join(issues[:3]) if issues else "Fully counted"


def build_attention_level(row, updated_label):
    if row["tracked_venue_count"] == 0 or row["counted_venue_count"] == 0 or row["missing_count_venue_count"] > 0:
        return "critical"
    if row["is_below_par"] or updated_label["is_stale"]:
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
    active_items = (
        db.session.query(Item.id, Item.name, Item.item_type)
        .filter(Item.active == True)
        .order_by(Item.name.asc())
        .all()
    )

    items_by_id = {
        item.id: {
            "id": item.id,
            "name": item.name,
            "item_type": item.item_type,
            "total_raw_count": 0,
            "tracked_venue_count": 0,
            "counted_venue_count": 0,
            "missing_count_venue_count": 0,
            "zero_count_venue_count": 0,
            "total_par_count": 0,
            "par_venue_count": 0,
            "last_count_updated_at": None,
            "venue_rows": [],
        }
        for item in active_items
    }

    query_rows = (
        db.session.query(
            Item.id.label("item_id"),
            Item.name.label("item_name"),
            Item.item_type.label("item_type"),
            Venue.id.label("venue_id"),
            Venue.name.label("venue_name"),
            VenueItem.expected_qty.label("par_count"),
            VenueItemCount.raw_count.label("raw_count"),
            VenueItemCount.updated_at.label("updated_at"),
        )
        .select_from(VenueItem)
        .join(Item, Item.id == VenueItem.item_id)
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
            Venue.active == True,
        )
        .order_by(Item.name.asc(), Venue.name.asc())
        .all()
    )

    for row in query_rows:
        item_row = items_by_id[row.item_id]

        item_row["tracked_venue_count"] += 1

        if row.par_count is not None:
            item_row["total_par_count"] += row.par_count
            item_row["par_venue_count"] += 1

        if row.raw_count is None:
            item_row["missing_count_venue_count"] += 1
        else:
            item_row["counted_venue_count"] += 1
            item_row["total_raw_count"] += row.raw_count
            if row.raw_count == 0:
                item_row["zero_count_venue_count"] += 1

            normalized_updated_at = ensure_utc(row.updated_at)
            if normalized_updated_at and (
                item_row["last_count_updated_at"] is None
                or normalized_updated_at > item_row["last_count_updated_at"]
            ):
                item_row["last_count_updated_at"] = normalized_updated_at

        item_row["venue_rows"].append(
            {
                "venue_id": row.venue_id,
                "venue_name": row.venue_name,
                "raw_count": row.raw_count,
                "raw_count_text": "Not Counted" if row.raw_count is None else str(row.raw_count),
                "is_missing": row.raw_count is None,
                "par_count": row.par_count,
                "par_count_text": "Not Set" if row.par_count is None else str(row.par_count),
                "updated_at_text": format_supply_timestamp(row.updated_at),
            }
        )

    supply_rows = []
    for item_row in items_by_id.values():
        coverage_meta = coverage_meta_for_row(item_row)
        total_par_count = item_row["total_par_count"] if item_row["par_venue_count"] > 0 else None
        item_row["par_ratio"] = build_par_ratio(item_row["total_raw_count"], total_par_count)
        item_row["is_below_par"] = bool(
            total_par_count is not None and total_par_count > 0 and item_row["total_raw_count"] < total_par_count
        )
        updated_label = build_supply_updated_label(item_row["last_count_updated_at"])
        item_row["coverage_key"] = coverage_meta["key"]
        item_row["coverage_label"] = coverage_meta["label"]
        item_row["coverage_summary"] = coverage_meta["summary"]
        item_row["coverage_compact_summary"] = coverage_meta["compact_summary"]
        item_row["total_par_count"] = total_par_count
        item_row["total_par_count_text"] = str(total_par_count) if total_par_count is not None else "Not Set"
        item_row["last_count_updated_at_text"] = format_supply_timestamp(item_row["last_count_updated_at"])
        item_row["last_count_updated_parts"] = build_supply_timestamp_parts(item_row["last_count_updated_at"])
        item_row["last_count_updated_label"] = updated_label
        item_row["last_updated_timestamp"] = (
            item_row["last_count_updated_at"].timestamp() if item_row["last_count_updated_at"] else None
        )
        item_row["par_progress"] = build_par_progress(item_row["total_raw_count"], total_par_count)
        item_row["issue_summary"] = build_issue_summary(item_row)
        item_row["desktop_issue_summary"] = build_desktop_issue_summary(item_row, updated_label)
        item_row["attention_level"] = build_attention_level(item_row, updated_label)
        supply_rows.append(item_row)

    return supply_rows


def build_supply_summary(rows):
    return {
        "total_units_on_hand": sum(row["total_raw_count"] for row in rows),
        "total_active_supply_items": len(rows),
        "items_with_complete_count_coverage": sum(
            1 for row in rows if row["coverage_key"] == "complete"
        ),
        "items_with_missing_counts": sum(
            1 for row in rows if row["missing_count_venue_count"] > 0
        ),
    }


def filter_supply_rows(rows, search_query, item_type, coverage, quick_filters=None):
    normalized_query = (search_query or "").strip().lower()
    quick_filters = quick_filters or []
    filtered_rows = []
    for row in rows:
        if normalized_query and normalized_query not in row["name"].lower():
            continue
        if item_type != "all" and row["item_type"] != item_type:
            continue
        if coverage != "all" and row["coverage_key"] != coverage:
            continue

        type_filters = [value for value in quick_filters if value in {"durable", "consumable"}]
        if type_filters and row["item_type"] not in type_filters:
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
    if sort_key == "item_name":
        return (row["name"].lower(), row["item_type"], row["id"])
    if sort_key == "missing_venues_desc":
        return (
            -row["missing_count_venue_count"],
            -(row["tracked_venue_count"] - row["counted_venue_count"]),
            row["name"].lower(),
        )
    if sort_key == "last_updated":
        has_updated_at = row["last_count_updated_at"] is not None
        timestamp = row["last_count_updated_at"].timestamp() if has_updated_at else float("-inf")
        return (
            0 if has_updated_at else 1,
            -timestamp,
            row["name"].lower(),
        )
    if sort_key == "par_ratio_asc":
        has_ratio = row["par_ratio"] is not None
        ratio = row["par_ratio"] if has_ratio else float("inf")
        return (
            0 if has_ratio else 1,
            ratio,
            -row["missing_count_venue_count"],
            row["name"].lower(),
        )
    return (
        -row["total_raw_count"],
        row["name"].lower(),
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

    return render_template(
        "supplies/index.html",
        summary=summary,
        filters=filters,
        supply_rows=filtered_rows,
        total_supply_items=len(all_supply_rows),
        filtered_supply_items=len(filtered_rows),
    )
