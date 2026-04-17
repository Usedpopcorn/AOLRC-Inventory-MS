from sqlalchemy import func
from sqlalchemy.orm import selectinload

from app import db
from app.models import Check, CheckLine, CountSession, Item, Venue, VenueItem, VenueItemCount
from app.services.inventory_status import (
    build_consistency_signal,
    build_overall_status_badge,
    build_signal_freshness,
    build_status_detail_counts,
    derive_singleton_count_from_status,
    ensure_utc,
    format_timestamp,
    infer_singleton_status_from_count,
    normalize_singleton_status,
    normalize_status,
    restock_status_meta_for_item,
    status_sort_value,
)


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


def build_venue_profile_view_model(venue_id):
    venue = Venue.query.get_or_404(venue_id)
    tracked_rows = (
        db.session.query(Item, VenueItem.expected_qty.label("par_value"))
        .join(VenueItem, VenueItem.item_id == Item.id)
        .options(selectinload(Item.parent_item))
        .filter(
            VenueItem.venue_id == venue_id,
            VenueItem.active == True,
            Item.active == True,
            Item.is_group_parent == False,
        )
        .all()
    )

    if not tracked_rows:
        return _build_empty_view_model(venue)

    tracked_rows = sorted(tracked_rows, key=lambda row: operational_item_sort_key(row[0]))
    item_ids = [item.id for item, _ in tracked_rows]
    latest_status_by_item = _build_latest_status_map(venue_id, item_ids)
    latest_count_by_item = _build_latest_count_map(venue_id, item_ids)
    latest_status_check_at = (
        db.session.query(func.max(Check.created_at))
        .filter(Check.venue_id == venue_id)
        .scalar()
    )
    latest_raw_count_at = (
        db.session.query(func.max(CountSession.created_at))
        .filter(CountSession.venue_id == venue_id)
        .scalar()
    )
    last_updated_at = _max_timestamp(ensure_utc(latest_status_check_at), ensure_utc(latest_raw_count_at))

    item_rows = []
    overall_counts = {"good": 0, "ok": 0, "low": 0, "out": 0, "not_checked": 0}
    overall_detail_counts = build_status_detail_counts()

    for item, par_value in tracked_rows:
        item_row = _build_item_row(
            item,
            venue_id=venue_id,
            par_value=par_value,
            latest_status=latest_status_by_item.get(item.id),
            latest_count=latest_count_by_item.get(item.id),
        )
        item_rows.append(item_row)
        overall_counts[item_row["status_key"]] += 1
        if item_row["status_key"] in {"low", "out"}:
            suffix = "singleton" if item_row["tracking_mode"] == "singleton_asset" else "quantity"
            overall_detail_counts[f'{item_row["status_key"]}_{suffix}'] += 1

    inventory_groups = build_inventory_groups(item_rows)
    summary = build_venue_summary(item_rows, overall_counts, overall_detail_counts)
    return {
        "venue": venue,
        "summary": summary,
        "inventory_groups": inventory_groups,
        "latest_status_check_at": latest_status_check_at,
        "latest_raw_count_at": latest_raw_count_at,
        "last_updated_at": last_updated_at,
        "last_updated_text": format_timestamp(last_updated_at),
        "last_updated_freshness": build_signal_freshness(last_updated_at),
    }


def _build_empty_view_model(venue):
    summary = {
        "total_tracked_items": 0,
        "quantity_item_count": 0,
        "singleton_item_count": 0,
        "operational_issue_count": 0,
        "review_queue_count": 0,
        "needs_review_count": 0,
        "count_completion_pct": 0,
        "count_completion_text": "No quantity items tracked",
        "status_completion_pct": 0,
        "status_completion_text": "No tracked items",
        "low_or_out_count": 0,
        "stale_status_count": 0,
        "stale_count_count": 0,
        "missing_or_damaged_assets": 0,
        "overall_status": build_overall_status_badge(0, {"good": 0, "ok": 0, "low": 0, "out": 0, "not_checked": 0}),
        "cards": [],
        "callout": None,
    }
    return {
        "venue": venue,
        "summary": summary,
        "inventory_groups": [],
        "latest_status_check_at": None,
        "latest_raw_count_at": None,
        "last_updated_at": None,
        "last_updated_text": "No updates yet",
        "last_updated_freshness": build_signal_freshness(None),
    }


def _build_latest_status_map(venue_id, item_ids):
    rows = (
        db.session.query(
            CheckLine.item_id.label("item_id"),
            CheckLine.status.label("status"),
            Check.created_at.label("created_at"),
        )
        .join(Check, Check.id == CheckLine.check_id)
        .filter(Check.venue_id == venue_id, CheckLine.item_id.in_(item_ids))
        .order_by(CheckLine.item_id.asc(), Check.created_at.desc(), Check.id.desc())
        .all()
    )
    latest = {}
    for row in rows:
        if row.item_id not in latest:
            latest[row.item_id] = {
                "status": row.status,
                "updated_at": ensure_utc(row.created_at),
            }
    return latest


def _build_latest_count_map(venue_id, item_ids):
    rows = (
        db.session.query(
            VenueItemCount.item_id.label("item_id"),
            VenueItemCount.raw_count.label("raw_count"),
            VenueItemCount.updated_at.label("updated_at"),
        )
        .filter(VenueItemCount.venue_id == venue_id, VenueItemCount.item_id.in_(item_ids))
        .all()
    )
    return {
        row.item_id: {
            "raw_count": row.raw_count,
            "updated_at": ensure_utc(row.updated_at),
        }
        for row in rows
    }


def _build_item_row(item, *, venue_id, par_value, latest_status, latest_count):
    raw_count = latest_count["raw_count"] if latest_count else None
    count_updated_at = latest_count["updated_at"] if latest_count else None
    derived_count = False

    if item.tracking_mode == "singleton_asset":
        if latest_status:
            status_key = normalize_singleton_status(latest_status["status"])
        else:
            status_key = infer_singleton_status_from_count(raw_count)
        raw_count = derive_singleton_count_from_status(status_key)
        count_updated_at = None
        derived_count = True
        par_value = None
    else:
        status_key = normalize_status(latest_status["status"]) if latest_status else "not_checked"

    status_updated_at = latest_status["updated_at"] if latest_status else None
    status_meta = restock_status_meta_for_item(status_key, item.tracking_mode)
    consistency = build_consistency_signal(
        tracking_mode=item.tracking_mode,
        status_key=status_key,
        raw_count=raw_count,
        par_value=par_value,
        status_updated_at=status_updated_at,
        count_updated_at=count_updated_at,
    )

    status_freshness = consistency["status_freshness"]
    count_freshness = consistency["count_freshness"]
    if item.tracking_mode == "singleton_asset":
        count_freshness = build_signal_freshness(None, missing_text="Derived from status")

    if item.tracking_mode == "singleton_asset":
        count_context_text = "No quantity count"
        count_support_text = "Asset status only"
    else:
        if raw_count is None:
            count_context_text = "No count"
            count_support_text = "Add count"
        elif par_value is None:
            count_context_text = f"{raw_count} counted"
            count_support_text = "No par"
        else:
            count_context_text = f"{raw_count} / {par_value}"
            count_support_text = (
                consistency["suggested_status_label"]
                and f'Suggests {consistency["suggested_status_label"]}'
                or "Count recorded"
            )

    attention_level = _resolve_attention_level(item.tracking_mode, status_key, consistency["state"])
    last_signal_at = _max_timestamp(status_updated_at, count_updated_at)
    review_meta_text = _review_meta_text(consistency["state"])
    freshness_primary_text = (
        "No asset check"
        if item.tracking_mode == "singleton_asset" and status_freshness["is_missing"]
        else (
            status_freshness["text"].replace("Updated ", "Checked ")
            if item.tracking_mode == "singleton_asset"
            else _compact_freshness_text(status_freshness, "Status")
        )
    )
    freshness_secondary_text = (
        "Asset status update"
        if item.tracking_mode == "singleton_asset"
        else _compact_freshness_text(count_freshness, "Count")
    )

    return {
        "id": item.id,
        "venue_id": venue_id,
        "name": item.name,
        "parent_item_id": item.parent_item_id,
        "parent_name": item.parent_item.name if item.parent_item else None,
        "family_name": item.parent_item.name if item.parent_item else item.name,
        "item_type": item.item_type,
        "item_category": item.item_category or item.item_type,
        "tracking_mode": item.tracking_mode or "quantity",
        "par_value": par_value,
        "raw_count": raw_count,
        "count_is_derived": derived_count,
        "status_key": status_key,
        "status_meta": status_meta,
        "status_freshness": status_freshness,
        "count_freshness": count_freshness,
        "consistency": consistency,
        "count_context_text": count_context_text,
        "count_support_text": count_support_text,
        "review_meta_text": review_meta_text,
        "freshness_primary_text": freshness_primary_text,
        "freshness_secondary_text": freshness_secondary_text,
        "attention_level": attention_level,
        "is_operational_issue": status_key in {"low", "out"},
        "has_review_issue": _has_review_issue(consistency["state"]),
        "has_count_gap": item.tracking_mode != "singleton_asset" and consistency["state"] in {"no_count", "count_stale"},
        "has_status_gap": consistency["state"] in {"no_status", "status_stale"},
        "is_missing_count": item.tracking_mode != "singleton_asset" and consistency["state"] == "no_count",
        "is_stale_count": item.tracking_mode != "singleton_asset" and consistency["state"] == "count_stale",
        "is_stale_status": consistency["state"] == "status_stale",
        "operational_summary": status_meta["text"],
        "count_absolute_text": format_timestamp(count_updated_at, missing_text="No count yet") if count_updated_at else "No count yet",
        "status_absolute_text": format_timestamp(status_updated_at, missing_text="No status yet") if status_updated_at else "No status yet",
        "last_signal_at": last_signal_at,
        "last_signal_timestamp": _timestamp_sort_value(last_signal_at),
        "count_coverage_score": _count_coverage_score(item.tracking_mode, consistency["state"]),
        "primary_action_label": _primary_action_label(item.tracking_mode, consistency["state"]),
        "primary_action_mode": "status" if item.tracking_mode == "singleton_asset" else "raw_counts",
        "secondary_action_label": None if item.tracking_mode == "singleton_asset" else "Update Status",
        "secondary_action_mode": None if item.tracking_mode == "singleton_asset" else "status",
    }


def build_inventory_groups(item_rows):
    groups = []
    families = {}

    for row in item_rows:
        if not row["parent_item_id"]:
            groups.append(
                {
                    "kind": "item",
                    "id": f'item-{row["id"]}',
                    "row": row,
                    "sort_name": row["family_name"].lower(),
                }
            )
            continue

        key = (row["parent_item_id"], row["parent_name"])
        family = families.get(key)
        if family is None:
            family = {
                "kind": "family",
                "id": f'family-{row["parent_item_id"]}',
                "family_id": row["parent_item_id"],
                "family_name": row["parent_name"],
                "children": [],
            }
            families[key] = family
            groups.append(family)
        family["children"].append(row)

    for group in groups:
        if group["kind"] != "family":
            continue
        _hydrate_family_group(group)

    return groups


def build_venue_summary(item_rows, overall_counts, overall_detail_counts):
    total_tracked = len(item_rows)
    quantity_rows = [row for row in item_rows if row["tracking_mode"] != "singleton_asset"]
    singleton_rows = [row for row in item_rows if row["tracking_mode"] == "singleton_asset"]
    status_checked_count = sum(1 for row in item_rows if row["status_key"] != "not_checked")
    quantity_counted_count = sum(1 for row in quantity_rows if row["raw_count"] is not None)
    needs_review_count = sum(1 for row in item_rows if row["consistency"]["state"] == "needs_review")
    no_status_count = sum(1 for row in item_rows if row["consistency"]["state"] == "no_status")
    no_count_count = sum(1 for row in quantity_rows if row["consistency"]["state"] == "no_count")
    stale_status_count = sum(1 for row in item_rows if row["consistency"]["state"] == "status_stale")
    stale_count_count = sum(1 for row in quantity_rows if row["consistency"]["state"] == "count_stale")
    missing_or_damaged_assets = sum(1 for row in singleton_rows if row["status_key"] in {"low", "out"})
    low_or_out_count = sum(1 for row in quantity_rows if row["status_key"] in {"low", "out"})
    operational_issue_count = low_or_out_count + missing_or_damaged_assets
    review_queue_count = needs_review_count + no_status_count + no_count_count + stale_status_count + stale_count_count
    status_completion_pct = round((status_checked_count / total_tracked) * 100) if total_tracked else 0
    count_completion_pct = round((quantity_counted_count / len(quantity_rows)) * 100) if quantity_rows else 0
    count_coverage_value = f"{count_completion_pct}%" if quantity_rows else "N/A"
    overall_status = build_overall_status_badge(total_tracked, overall_counts, overall_detail_counts)

    callout = None
    if missing_or_damaged_assets:
        callout = {
            "tone": "warning",
            "title": "Asset follow-up",
            "body": f"{missing_or_damaged_assets} asset{'s' if missing_or_damaged_assets != 1 else ''} are missing or damaged.",
            "filter_preset": "asset_issues",
            "action_label": "Show asset issues",
        }
    elif needs_review_count:
        callout = {
            "tone": "warning",
            "title": "Review mismatches",
            "body": f"{needs_review_count} item{'s' if needs_review_count != 1 else ''} need status and count review.",
            "filter_preset": "review",
            "action_label": "Show review items",
        }
    elif review_queue_count:
        callout = {
            "tone": "info",
            "title": "Finish follow-up",
            "body": f"{review_queue_count} item{'s' if review_queue_count != 1 else ''} still need a fresh status or count.",
            "filter_preset": "review_queue",
            "action_label": "Show follow-up",
        }

    return {
        "total_tracked_items": total_tracked,
        "quantity_item_count": len(quantity_rows),
        "singleton_item_count": len(singleton_rows),
        "operational_issue_count": operational_issue_count,
        "review_queue_count": review_queue_count,
        "needs_review_count": needs_review_count,
        "count_completion_pct": count_completion_pct,
        "count_completion_text": (
            f"{quantity_counted_count} of {len(quantity_rows)} quantity items counted"
            if quantity_rows
            else "No quantity items tracked"
        ),
        "status_completion_pct": status_completion_pct,
        "status_completion_text": f"{status_checked_count} of {total_tracked} quick statuses recorded",
        "low_or_out_count": low_or_out_count,
        "stale_status_count": stale_status_count,
        "stale_count_count": stale_count_count,
        "missing_or_damaged_assets": missing_or_damaged_assets,
        "overall_status": overall_status,
        "cards": [
            {
                "title": "Venue Health",
                "value": overall_status["text"],
                "support": f"From latest quick status updates",
                "tone": overall_status["key"],
            },
            {
                "title": "Operational Issues",
                "value": str(operational_issue_count),
                "support": "Low, out, missing, or damaged",
                "tone": "warning" if operational_issue_count else "success",
            },
            {
                "title": "Review Queue",
                "value": str(review_queue_count),
                "support": "Mismatch, stale, or missing signals",
                "tone": "warning" if review_queue_count else "success",
            },
            {
                "title": "Count Coverage",
                "value": count_coverage_value,
                "support": (
                    f"{quantity_counted_count} of {len(quantity_rows)} quantity items counted"
                    if quantity_rows
                    else "No quantity items tracked"
                ),
                "tone": "success" if quantity_rows and count_completion_pct == 100 else ("secondary" if quantity_rows else "neutral"),
            },
        ],
        "callout": callout,
    }


def _hydrate_family_group(group):
    children = group["children"]
    status_counts = {"good": 0, "ok": 0, "low": 0, "out": 0, "not_checked": 0}
    worst_child = children[0]
    needs_review_count = 0
    missing_or_stale_counts = 0
    no_count_count = 0
    count_stale_count = 0
    no_status_count = 0
    status_stale_count = 0
    operational_issue_count = 0
    counted_children = 0
    checked_children = 0
    quantity_child_count = 0
    singleton_child_count = 0
    count_coverage_total = 0
    missing_asset_count = 0
    damaged_asset_count = 0
    has_singleton_children = False
    has_quantity_children = False
    latest_update_at = None

    for child in children:
        status_counts[child["status_key"]] += 1
        if status_sort_value(child["status_key"]) > status_sort_value(worst_child["status_key"]):
            worst_child = child
        if child["consistency"]["state"] == "needs_review":
            needs_review_count += 1
        if child["consistency"]["state"] in {"no_count", "count_stale", "status_stale", "no_status"}:
            missing_or_stale_counts += 1
        if child["consistency"]["state"] == "no_count":
            no_count_count += 1
        elif child["consistency"]["state"] == "count_stale":
            count_stale_count += 1
        elif child["consistency"]["state"] == "no_status":
            no_status_count += 1
        elif child["consistency"]["state"] == "status_stale":
            status_stale_count += 1
        if child["status_key"] != "not_checked":
            checked_children += 1
        if child["status_key"] in {"low", "out"}:
            operational_issue_count += 1
        if child["tracking_mode"] == "singleton_asset":
            has_singleton_children = True
            singleton_child_count += 1
            if child["status_key"] == "out":
                missing_asset_count += 1
            elif child["status_key"] == "low":
                damaged_asset_count += 1
        else:
            has_quantity_children = True
            quantity_child_count += 1
            if child["raw_count"] is not None:
                counted_children += 1
            count_coverage_total += child["count_coverage_score"]
        latest_update_at = _max_timestamp(latest_update_at, child["last_signal_at"])

    child_count = len(children)
    if has_singleton_children and not has_quantity_children:
        if missing_asset_count:
            status_summary = f"{missing_asset_count} missing"
        elif damaged_asset_count:
            status_summary = f"{damaged_asset_count} damaged"
        else:
            status_summary = worst_child["status_meta"]["text"]
        tracking_summary = "Asset family"
        count_summary = f"{checked_children} of {child_count} checked"
    elif has_quantity_children and not has_singleton_children:
        if status_counts["out"] > 0:
            status_summary = f'{status_counts["out"]} out'
        elif status_counts["low"] > 0:
            status_summary = f'{status_counts["low"]} low'
        else:
            status_summary = worst_child["status_meta"]["text"]
        tracking_summary = "Quantity family"
        count_summary = f"{counted_children} of {child_count} counted"
    else:
        issue_count = status_counts["out"] + status_counts["low"]
        status_summary = f"{issue_count} need attention" if issue_count else worst_child["status_meta"]["text"]
        tracking_summary = "Mixed family"
        count_summary = f"{counted_children} counted / {checked_children} checked"

    group["child_count"] = child_count
    group["status_summary"] = status_summary
    group["tracking_summary"] = tracking_summary
    group["count_summary"] = count_summary
    group["review_summary"] = (
        f"{needs_review_count} review"
        if needs_review_count
        else (
            f"{missing_or_stale_counts} stale or missing"
            if missing_or_stale_counts
            else "No review issues"
        )
    )
    group["search_text"] = " ".join(
        [group["family_name"], *[child["name"] for child in children]]
    ).lower()
    group["needs_review_count"] = needs_review_count
    group["missing_or_stale_counts"] = missing_or_stale_counts
    group["no_count_count"] = no_count_count
    group["count_stale_count"] = count_stale_count
    group["no_status_count"] = no_status_count
    group["status_stale_count"] = status_stale_count
    group["operational_issue_count"] = operational_issue_count
    group["quantity_child_count"] = quantity_child_count
    group["singleton_child_count"] = singleton_child_count
    group["has_singleton_children"] = has_singleton_children
    group["has_quantity_children"] = has_quantity_children
    group["missing_asset_count"] = missing_asset_count
    group["damaged_asset_count"] = damaged_asset_count
    group["has_review_issue"] = (needs_review_count + no_count_count + count_stale_count + no_status_count + status_stale_count) > 0
    group["has_count_gap"] = (no_count_count + count_stale_count) > 0
    group["has_status_gap"] = (no_status_count + status_stale_count) > 0
    group["count_coverage_score"] = (
        round(count_coverage_total / quantity_child_count, 3)
        if quantity_child_count
        else 2
    )
    group["latest_update_freshness"] = build_signal_freshness(latest_update_at)
    group["last_signal_timestamp"] = _timestamp_sort_value(latest_update_at)
    group["worst_status_key"] = worst_child["status_key"]
    group["worst_status_meta"] = worst_child["status_meta"]
    group["attention_level"] = _family_attention_level(children)


def _family_attention_level(children):
    severity = {"healthy": 0, "warning": 1, "critical": 2}
    resolved = "healthy"
    for child in children:
        if severity[child["attention_level"]] > severity[resolved]:
            resolved = child["attention_level"]
    return resolved


def _resolve_attention_level(tracking_mode, status_key, consistency_state):
    attention_level = "healthy"
    if tracking_mode == "singleton_asset":
        if status_key == "out":
            attention_level = "critical"
        elif status_key == "low":
            attention_level = "warning"
    else:
        if status_key == "out":
            attention_level = "critical"
        elif status_key == "low":
            attention_level = "warning"

    if consistency_state in {"needs_review", "status_stale", "count_stale", "no_status", "no_count"} and attention_level == "healthy":
        attention_level = "warning"
    return attention_level


def _review_meta_text(consistency_state):
    return {
        "aligned": None,
        "needs_review": "Status/count mismatch",
        "status_stale": "Refresh status",
        "count_stale": "Refresh count",
        "no_status": "Add status",
        "no_count": "Add count",
        "not_comparable": None,
    }.get(consistency_state)


def _compact_freshness_text(freshness, prefix):
    if freshness["is_missing"]:
        return f"No {prefix.lower()}"
    return freshness["text"].replace("Updated ", f"{prefix} ")


def _has_review_issue(consistency_state):
    return consistency_state in {"needs_review", "status_stale", "count_stale", "no_status", "no_count"}


def _count_coverage_score(tracking_mode, consistency_state):
    if tracking_mode == "singleton_asset":
        return 2
    if consistency_state == "no_count":
        return 0
    if consistency_state == "count_stale":
        return 0.25
    return 1


def _primary_action_label(tracking_mode, consistency_state):
    if tracking_mode == "singleton_asset":
        return "Update Asset Status"
    if consistency_state == "no_count":
        return "Add Count"
    if consistency_state == "count_stale":
        return "Refresh Count"
    return "Open Count"


def _timestamp_sort_value(value):
    normalized = ensure_utc(value)
    return int(normalized.timestamp()) if normalized else 0


def _max_timestamp(*timestamps):
    resolved = None
    for candidate in timestamps:
        if candidate and (resolved is None or candidate > resolved):
            resolved = candidate
    return resolved

