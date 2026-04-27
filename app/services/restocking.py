import re

from sqlalchemy import and_, func
from sqlalchemy.orm import aliased

from app import db
from app.models import Check, CheckLine, Item, Venue, VenueItem, VenueItemCount
from app.services.inventory_rules import resolve_effective_par_level
from app.services.inventory_status import (
    STATUS_META,
    normalize_status,
    restock_status_meta_for_item,
)
from app.services.spreadsheet_compat import build_reorder_decision, format_setup_group_display

RESTOCK_STATUS_META = {key: value.copy() for key, value in STATUS_META.items()}
RESTOCK_COUNT_BUCKET_RANK = {
    "comparable": 0,
    "no_count": 1,
    "no_par": 2,
    "singleton": 3,
}
RESTOCK_COUNT_TONE_BY_KEY = {
    "good": "success",
    "ok": "secondary",
    "low": "warning",
    "out": "danger",
    "no_count": "muted",
    "no_par": "neutral",
    "singleton": "secondary",
}


def normalize_restock_mode(value, fallback="status"):
    normalized = (value or fallback).strip().lower()
    if normalized not in {"status", "counts"}:
        return fallback
    return normalized


def normalize_restock_sort(value):
    normalized = (value or "status_priority").strip().lower()
    if normalized in {"item", "item_asc", "item_desc"}:
        return "item"
    if normalized in {"venue", "venue_asc", "venue_desc"}:
        return "venue"
    if normalized == "last_checked":
        return "last_checked"
    if normalized != "status_priority":
        return "status_priority"
    return normalized


def build_restock_count_state(*, tracking_mode, raw_count, par_value, status_key):
    reorder_decision = build_reorder_decision(
        tracking_mode=tracking_mode,
        raw_count=raw_count,
        effective_par=par_value,
    )

    if reorder_decision.state == "singleton_asset":
        status_meta = restock_status_meta_for_item(status_key, tracking_mode)
        return {
            "key": "singleton",
            "text": status_meta["text"],
            "icon_class": status_meta["icon_class"],
            "tone": RESTOCK_COUNT_TONE_BY_KEY["singleton"],
            "bucket": "singleton",
            "priority_rank": 99,
            "suggested_order_qty": None,
            "over_par_qty": None,
            "raw_count": raw_count,
            "par_value": None,
            "is_comparable": False,
        }

    if reorder_decision.state == "no_count":
        return {
            "key": "no_count",
            "text": "No count",
            "icon_class": "bi-dash-circle",
            "tone": RESTOCK_COUNT_TONE_BY_KEY["no_count"],
            "bucket": "no_count",
            "priority_rank": 99,
            "suggested_order_qty": None,
            "over_par_qty": None,
            "raw_count": None,
            "par_value": par_value,
            "is_comparable": False,
        }

    if reorder_decision.state == "no_par":
        return {
            "key": "no_par",
            "text": "No par",
            "icon_class": "bi-sliders",
            "tone": RESTOCK_COUNT_TONE_BY_KEY["no_par"],
            "bucket": "no_par",
            "priority_rank": 99,
            "suggested_order_qty": None,
            "over_par_qty": None,
            "raw_count": raw_count,
            "par_value": par_value,
            "is_comparable": False,
        }

    severity_key = reorder_decision.severity_key or "good"
    severity_rank = {"out": 0, "low": 1, "ok": 2, "good": 3}
    return {
        "key": severity_key,
        "text": f"{raw_count} / {par_value}",
        "icon_class": RESTOCK_STATUS_META[severity_key]["icon_class"],
        "tone": RESTOCK_COUNT_TONE_BY_KEY[severity_key],
        "bucket": "comparable",
        "priority_rank": severity_rank[severity_key],
        "suggested_order_qty": reorder_decision.suggested_order_qty,
        "over_par_qty": reorder_decision.over_par_qty,
        "raw_count": raw_count,
        "par_value": par_value,
        "is_comparable": True,
    }


def _word_boundary_match(text, query):
    return bool(re.search(rf"(^|\W){re.escape(query)}", text))


def restock_search_rank(row, search_query):
    if not search_query:
        return 0

    item_text = (row.get("item_name") or "").lower()
    family_text = (row.get("parent_name") or "").lower()
    venue_text = (row.get("venue_name") or "").lower()
    status_text = (row.get("status", {}).get("text") or "").lower()
    count_text = (row.get("count_state", {}).get("text") or "").lower()
    tracking_text = "asset" if row.get("tracking_mode") == "singleton_asset" else "quantity"

    if item_text.startswith(search_query):
        return 0
    if _word_boundary_match(item_text, search_query):
        return 1
    if search_query in item_text:
        return 2
    if family_text.startswith(search_query):
        return 3
    if _word_boundary_match(family_text, search_query):
        return 4
    if search_query in family_text:
        return 5
    if venue_text.startswith(search_query):
        return 6
    if _word_boundary_match(venue_text, search_query):
        return 7
    if search_query in venue_text:
        return 8
    if tracking_text.startswith(search_query) or search_query in tracking_text:
        return 9
    if status_text.startswith(search_query):
        return 10
    if search_query in status_text:
        return 11
    if count_text.startswith(search_query):
        return 12
    if search_query in count_text:
        return 13
    return None


def build_restock_rows(
    statuses=None,
    item_ids=None,
    venue_ids=None,
    search="",
    sort="status_priority",
    mode="status",
    limit=None,
    offset=0,
):
    if statuses is None:
        selected_statuses = list(RESTOCK_STATUS_META.keys())
    else:
        selected_statuses = [status for status in statuses if status in RESTOCK_STATUS_META]
    if not selected_statuses:
        return {"rows": [], "total_count": 0, "has_more": False}
    selected_statuses_set = set(selected_statuses)
    search_query = (search or "").strip().lower()
    sort_mode = normalize_restock_sort(sort)
    restock_mode = normalize_restock_mode(mode, "status")

    latest_check_sq = (
        db.session.query(
            Check.venue_id.label("venue_id"),
            func.max(Check.id).label("latest_check_id"),
        )
        .group_by(Check.venue_id)
        .subquery()
    )
    parent_item = aliased(Item)

    query = (
        db.session.query(
            Venue.id.label("venue_id"),
            Venue.name.label("venue_name"),
            Item.id.label("item_id"),
            Item.name.label("item_name"),
            Item.tracking_mode.label("tracking_mode"),
            Item.item_category.label("item_category"),
            Item.setup_group_code.label("setup_group_code"),
            Item.setup_group_label.label("setup_group_label"),
            Item.default_par_level.label("default_par_level"),
            parent_item.name.label("parent_name"),
            VenueItem.expected_qty.label("venue_par_override"),
            VenueItemCount.raw_count.label("raw_count"),
            Check.created_at.label("latest_check_at"),
            CheckLine.status.label("line_status"),
        )
        .select_from(VenueItem)
        .join(Venue, Venue.id == VenueItem.venue_id)
        .join(Item, Item.id == VenueItem.item_id)
        .outerjoin(parent_item, parent_item.id == Item.parent_item_id)
        .outerjoin(latest_check_sq, latest_check_sq.c.venue_id == Venue.id)
        .outerjoin(Check, Check.id == latest_check_sq.c.latest_check_id)
        .outerjoin(
            CheckLine,
            and_(
                CheckLine.check_id == latest_check_sq.c.latest_check_id,
                CheckLine.item_id == VenueItem.item_id,
            ),
        )
        .outerjoin(
            VenueItemCount,
            and_(
                VenueItemCount.venue_id == VenueItem.venue_id,
                VenueItemCount.item_id == VenueItem.item_id,
            ),
        )
        .filter(
            VenueItem.active.is_(True),
            Venue.active.is_(True),
            Item.active.is_(True),
            Item.is_group_parent.is_(False),
        )
    )

    if item_ids is not None:
        if not item_ids:
            return {"rows": [], "total_count": 0, "has_more": False}
        query = query.filter(VenueItem.item_id.in_(item_ids))
    if venue_ids is not None:
        if not venue_ids:
            return {"rows": [], "total_count": 0, "has_more": False}
        query = query.filter(VenueItem.venue_id.in_(venue_ids))

    rows = []
    for row in query.order_by(Item.name.asc(), Venue.name.asc()).all():
        status_key = normalize_status(row.line_status)
        if status_key not in selected_statuses_set:
            continue

        tracking_mode = row.tracking_mode or "quantity"
        meta = restock_status_meta_for_item(status_key, tracking_mode)
        effective_par = resolve_effective_par_level(
            item_default_par_level=row.default_par_level,
            venue_par_override=row.venue_par_override,
        )
        count_state = build_restock_count_state(
            tracking_mode=tracking_mode,
            raw_count=row.raw_count,
            par_value=effective_par.value,
            status_key=status_key,
        )
        rows.append(
            {
                "venue_id": row.venue_id,
                "venue_name": row.venue_name,
                "item_id": row.item_id,
                "item_name": row.item_name,
                "parent_name": row.parent_name,
                "tracking_mode": tracking_mode,
                "item_category": row.item_category,
                "setup_group_code": row.setup_group_code,
                "setup_group_label": row.setup_group_label,
                "setup_group_display": format_setup_group_display(
                    row.setup_group_code,
                    row.setup_group_label,
                ),
                "latest_check_at": row.latest_check_at,
                "raw_count": row.raw_count,
                "par_value": effective_par.value,
                "status": {
                    "key": status_key,
                    "text": meta["text"],
                    "icon_class": meta["icon_class"],
                },
                "count_state": count_state,
                "quick_check_mode": (
                    "raw_counts"
                    if restock_mode == "counts" and tracking_mode != "singleton_asset"
                    else "status"
                ),
            }
        )

    status_rank = {"out": 0, "low": 1, "ok": 2, "not_checked": 3, "good": 4}

    def base_sort_key(row):
        family_name = (row.get("parent_name") or row["item_name"]).lower()
        if sort_mode == "venue":
            return (row["venue_name"].lower(), family_name, row["item_name"].lower())
        if sort_mode == "status_priority":
            if restock_mode == "counts":
                count_state = row["count_state"]
                bucket_rank = RESTOCK_COUNT_BUCKET_RANK.get(count_state["bucket"], 99)
                if count_state["bucket"] == "comparable":
                    return (
                        bucket_rank,
                        count_state["priority_rank"],
                        -(count_state["suggested_order_qty"] or 0),
                        row["venue_name"].lower(),
                        family_name,
                        row["item_name"].lower(),
                    )
                return (
                    bucket_rank,
                    row["venue_name"].lower(),
                    family_name,
                    row["item_name"].lower(),
                )
            return (
                status_rank.get(row["status"]["key"], 99),
                row["venue_name"].lower(),
                family_name,
                row["item_name"].lower(),
            )
        if sort_mode == "last_checked":
            return (
                1 if row["latest_check_at"] is None else 0,
                -(row["latest_check_at"].timestamp()) if row["latest_check_at"] else 0,
                family_name,
                row["item_name"].lower(),
                row["venue_name"].lower(),
            )
        return (family_name, row["item_name"].lower(), row["venue_name"].lower())

    if search_query:
        ranked_rows = []
        for row in rows:
            rank = restock_search_rank(row, search_query)
            if rank is None:
                continue
            ranked_rows.append((rank, row))
        ranked_rows.sort(key=lambda pair: (pair[0], base_sort_key(pair[1])))
        rows = [pair[1] for pair in ranked_rows]
    else:
        rows.sort(key=base_sort_key)

    total_count = len(rows)
    normalized_offset = max(int(offset or 0), 0)
    if limit is None:
        paged_rows = rows[normalized_offset:]
    else:
        normalized_limit = max(int(limit), 0)
        paged_rows = rows[normalized_offset : normalized_offset + normalized_limit]
    has_more = (normalized_offset + len(paged_rows)) < total_count
    return {"rows": paged_rows, "total_count": total_count, "has_more": has_more}
