from sqlalchemy import func
from sqlalchemy.orm import selectinload

from app import db
from app.models import (
    Check,
    CountSession,
    Item,
    Venue,
    VenueItem,
    VenueNote,
)
from app.services.csv_exports import build_dated_csv_filename, sanitize_csv_cell
from app.services.inventory_rules import (
    get_default_stale_threshold_days,
    resolve_effective_par_level,
    resolve_effective_stale_threshold_days,
)
from app.services.inventory_signals import (
    build_latest_count_signal_map,
    build_latest_status_signal_map,
)
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
from app.services.restocking import build_restock_count_state
from app.services.spreadsheet_compat import format_setup_group_display

VENUE_INVENTORY_SEGMENTS = {"all", "needs_action", "review", "assets"}
VENUE_INVENTORY_FILTERS = {
    "all",
    "count_attention",
    "status_attention",
    "singleton_asset",
    "quantity",
    "families",
}
VENUE_INVENTORY_SORTS = {
    "needs_action",
    "review_first",
    "stalest",
    "alphabetical",
    "lowest_count_coverage",
    "recent",
}
VENUE_INVENTORY_EXPORT_HEADERS = (
    "Venue Name",
    "Item Name",
    "Setup Group Code",
    "Setup Group Label",
    "Tracking Mode",
    "Category",
    "Current Count",
    "Effective Par",
    "Suggested Order Qty",
    "Over Par Qty",
    "Current Quick Status / Last Saved Status",
    "Last Updated",
    "Checked By",
    "Effective Stale Threshold",
    "Is Stale",
    "Note Count",
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


def normalize_venue_inventory_filters(source):
    segment = (source.get("inventory_segment") or "all").strip().lower()
    filter_value = (source.get("inventory_filter") or "all").strip().lower()
    sort = (source.get("inventory_sort") or "needs_action").strip().lower()
    if segment not in VENUE_INVENTORY_SEGMENTS:
        segment = "all"
    if filter_value not in VENUE_INVENTORY_FILTERS:
        filter_value = "all"
    if sort not in VENUE_INVENTORY_SORTS:
        sort = "needs_action"
    return {
        "q": (source.get("inventory_q") or "").strip(),
        "segment": segment,
        "filter": filter_value,
        "sort": sort,
    }


def build_venue_profile_view_model(venue_id):
    venue = Venue.query.get_or_404(venue_id)
    global_stale_threshold_days = get_default_stale_threshold_days()
    venue_stale_threshold = resolve_effective_stale_threshold_days(
        venue_stale_threshold_days=venue.stale_threshold_days,
        global_stale_threshold_days=global_stale_threshold_days,
    )
    tracked_rows = (
        db.session.query(Item, VenueItem.expected_qty.label("venue_par_override"))
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
    note_counts_by_item = _build_note_count_map(venue_id, item_ids)
    network_detail_by_item = _build_network_detail_map(
        item_ids=item_ids,
        tracked_items=[item for item, _ in tracked_rows],
    )
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

    for item, venue_par_override in tracked_rows:
        effective_par = resolve_effective_par_level(
            item_default_par_level=item.default_par_level,
            venue_par_override=venue_par_override,
        )
        effective_stale_threshold = resolve_effective_stale_threshold_days(
            item_stale_threshold_days=item.stale_threshold_days,
            venue_stale_threshold_days=venue.stale_threshold_days,
            global_stale_threshold_days=global_stale_threshold_days,
        )
        item_row = _build_item_row(
            item,
            venue_id=venue_id,
            par_setting=effective_par,
            stale_threshold_setting=effective_stale_threshold,
            latest_status=latest_status_by_item.get(item.id),
            latest_count=latest_count_by_item.get(item.id),
            notes_count=int(note_counts_by_item.get(item.id, 0) or 0),
            network_detail=network_detail_by_item.get(item.id),
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
        "item_rows": item_rows,
        "inventory_groups": inventory_groups,
        "note_item_options": build_note_item_options(item_rows),
        "latest_status_check_at": latest_status_check_at,
        "latest_raw_count_at": latest_raw_count_at,
        "last_updated_at": last_updated_at,
        "last_updated_text": format_timestamp(last_updated_at),
        "last_updated_freshness": build_signal_freshness(
            last_updated_at,
            stale_threshold=venue_stale_threshold.value,
        ),
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
        "item_rows": [],
        "inventory_groups": [],
        "note_item_options": [],
        "latest_status_check_at": None,
        "latest_raw_count_at": None,
        "last_updated_at": None,
        "last_updated_text": "No updates yet",
        "last_updated_freshness": build_signal_freshness(None),
    }


def _build_latest_status_map(venue_id, item_ids):
    latest_by_pair = build_latest_status_signal_map(
        venue_ids=[venue_id],
        item_ids=item_ids,
    )
    return {
        item_id: value
        for (resolved_venue_id, item_id), value in latest_by_pair.items()
        if resolved_venue_id == venue_id
    }


def _build_latest_count_map(venue_id, item_ids):
    latest_by_pair = build_latest_count_signal_map(
        venue_ids=[venue_id],
        item_ids=item_ids,
    )
    return {
        item_id: value
        for (resolved_venue_id, item_id), value in latest_by_pair.items()
        if resolved_venue_id == venue_id
    }


def _build_note_count_map(venue_id, item_ids):
    if not item_ids:
        return {}

    rows = (
        db.session.query(
            VenueNote.item_id.label("item_id"),
            func.count(VenueNote.id).label("note_count"),
        )
        .filter(
            VenueNote.venue_id == venue_id,
            VenueNote.item_id.is_not(None),
            VenueNote.item_id.in_(item_ids),
        )
        .group_by(VenueNote.item_id)
        .all()
    )
    return {
        row.item_id: int(row.note_count or 0)
        for row in rows
        if row.item_id is not None
    }


def _build_network_detail_map(*, item_ids, tracked_items):
    if not item_ids:
        return {}

    tracked_by_id = {
        item.id: item
        for item in tracked_items
        if item.id in item_ids
    }
    if not tracked_by_id:
        return {}

    assignment_rows = (
        db.session.query(
            VenueItem.item_id.label("item_id"),
            VenueItem.venue_id.label("venue_id"),
            VenueItem.expected_qty.label("venue_par_override"),
        )
        .join(Venue, Venue.id == VenueItem.venue_id)
        .filter(
            VenueItem.item_id.in_(item_ids),
            VenueItem.active == True,
            Venue.active == True,
        )
        .all()
    )
    if not assignment_rows:
        return {}

    active_venue_ids = sorted({int(row.venue_id) for row in assignment_rows if row.venue_id is not None})
    if not active_venue_ids:
        return {}

    latest_status_by_pair = build_latest_status_signal_map(
        venue_ids=active_venue_ids,
        item_ids=item_ids,
    )
    latest_count_by_pair = build_latest_count_signal_map(
        venue_ids=active_venue_ids,
        item_ids=item_ids,
    )

    rows_by_item = {}
    for assignment in assignment_rows:
        rows_by_item.setdefault(int(assignment.item_id), []).append(assignment)

    network = {}
    for item_id, item in tracked_by_id.items():
        assignments = rows_by_item.get(item_id, [])
        venue_count = len(assignments)
        if venue_count <= 0:
            continue

        tracking_mode = item.tracking_mode or "quantity"
        if tracking_mode == "singleton_asset":
            issue_count = 0
            checked_count = 0
            for assignment in assignments:
                pair = (int(assignment.venue_id), item_id)
                latest_status = latest_status_by_pair.get(pair)
                latest_count = latest_count_by_pair.get(pair)
                raw_count = latest_count["raw_count"] if latest_count else None
                status_key = (
                    normalize_singleton_status(latest_status["status"])
                    if latest_status
                    else infer_singleton_status_from_count(raw_count)
                )
                if status_key != "not_checked":
                    checked_count += 1
                if status_key in {"low", "out"}:
                    issue_count += 1

            if issue_count:
                summary_text = (
                    f'{issue_count} issue{"s" if issue_count != 1 else ""} across '
                    f'{venue_count} venue{"s" if venue_count != 1 else ""}'
                )
            elif checked_count:
                summary_text = (
                    f'{checked_count} checked across '
                    f'{venue_count} venue{"s" if venue_count != 1 else ""}'
                )
            else:
                summary_text = (
                    f'No checks across {venue_count} venue{"s" if venue_count != 1 else ""}'
                )

            network[item_id] = {
                "network_tracked_venues_count": venue_count,
                "network_summary_text": summary_text,
            }
            continue

        total_raw_count = 0
        counted_venues = 0
        total_par_count = 0
        par_venues = 0
        for assignment in assignments:
            pair = (int(assignment.venue_id), item_id)
            latest_count = latest_count_by_pair.get(pair)
            raw_count = latest_count["raw_count"] if latest_count else None
            if raw_count is not None:
                total_raw_count += int(raw_count)
                counted_venues += 1

            effective_par = resolve_effective_par_level(
                item_default_par_level=item.default_par_level,
                venue_par_override=assignment.venue_par_override,
            )
            if effective_par.value is not None:
                total_par_count += int(effective_par.value)
                par_venues += 1

        if par_venues > 0:
            if counted_venues > 0:
                summary_text = (
                    f"{total_raw_count} / {total_par_count} across "
                    f'{venue_count} venue{"s" if venue_count != 1 else ""}'
                )
            else:
                summary_text = (
                    f"No counts / {total_par_count} par across "
                    f'{venue_count} venue{"s" if venue_count != 1 else ""}'
                )
        elif counted_venues > 0:
            summary_text = (
                f"{total_raw_count} counted across "
                f'{venue_count} venue{"s" if venue_count != 1 else ""}'
            )
        else:
            summary_text = (
                f'No counts across {venue_count} venue{"s" if venue_count != 1 else ""}'
            )

        network[item_id] = {
            "network_tracked_venues_count": venue_count,
            "network_summary_text": summary_text,
        }

    return network


def _build_item_row(
    item,
    *,
    venue_id,
    par_setting,
    stale_threshold_setting,
    latest_status,
    latest_count,
    notes_count,
    network_detail,
):
    par_value = par_setting.value
    raw_count = latest_count["raw_count"] if latest_count else None
    count_updated_at = latest_count["updated_at"] if latest_count else None
    count_actor_label = (latest_count or {}).get("actor_label", "")
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
    status_actor_label = (latest_status or {}).get("actor_label", "")
    status_meta = restock_status_meta_for_item(status_key, item.tracking_mode)
    count_state = build_restock_count_state(
        tracking_mode=item.tracking_mode,
        raw_count=raw_count,
        par_value=par_value,
        status_key=status_key,
    )
    consistency = build_consistency_signal(
        tracking_mode=item.tracking_mode,
        status_key=status_key,
        raw_count=raw_count,
        par_value=par_value,
        status_updated_at=status_updated_at,
        count_updated_at=count_updated_at,
        stale_threshold=stale_threshold_setting.value,
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
    last_actor_label = _resolve_last_actor_label(
        tracking_mode=item.tracking_mode,
        status_actor_label=status_actor_label,
        status_updated_at=status_updated_at,
        count_actor_label=count_actor_label,
        count_updated_at=count_updated_at,
    )
    is_stale = (
        bool(status_freshness["is_stale"])
        or (
            item.tracking_mode != "singleton_asset"
            and bool(count_freshness["is_stale"])
        )
    )
    status_absolute_text = format_timestamp(status_updated_at, missing_text="No status yet") if status_updated_at else "No status yet"
    count_absolute_text = format_timestamp(count_updated_at, missing_text="No count yet") if count_updated_at else "No count yet"
    network_detail = network_detail or {}

    return {
        "id": item.id,
        "venue_id": venue_id,
        "name": item.name,
        "parent_item_id": item.parent_item_id,
        "parent_name": item.parent_item.name if item.parent_item else None,
        "family_name": item.parent_item.name if item.parent_item else item.name,
        "item_type": item.item_type,
        "item_category": item.item_category or item.item_type,
        "setup_group_code": item.setup_group_code,
        "setup_group_label": item.setup_group_label,
        "setup_group_display": format_setup_group_display(item.setup_group_code, item.setup_group_label),
        "tracking_mode": item.tracking_mode or "quantity",
        "par_value": par_value,
        "par_source": par_setting.source,
        "effective_stale_threshold_days": stale_threshold_setting.value,
        "stale_threshold_source": stale_threshold_setting.source,
        "notes_count": notes_count,
        "has_notes": notes_count > 0,
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
        "next_step_label": _next_step_label(item.tracking_mode, consistency["state"]),
        "detail_setup_text": _detail_setup_text(item.tracking_mode, format_setup_group_display(item.setup_group_code, item.setup_group_label)),
        "detail_threshold_text": _detail_threshold_text(stale_threshold_setting.value, stale_threshold_setting.source),
        "detail_status_by_text": _detail_provenance_text(status_absolute_text, status_actor_label),
        "detail_count_by_text": (
            None
            if item.tracking_mode == "singleton_asset"
            else _detail_provenance_text(count_absolute_text, count_actor_label)
        ),
        "detail_last_touched_text": (
            f"{format_timestamp(last_signal_at, missing_text='No updates yet')} by {last_actor_label}"
            if last_actor_label
            else format_timestamp(last_signal_at, missing_text="No updates yet")
        ),
        "network_tracked_venues_count": int(network_detail.get("network_tracked_venues_count") or 1),
        "network_summary_text": network_detail.get("network_summary_text")
        or "Tracked at current venue only",
        "suggested_order_qty": count_state.get("suggested_order_qty"),
        "over_par_qty": count_state.get("over_par_qty"),
        "count_state": count_state,
        "current_status_label": status_meta["text"],
        "status_actor_label": status_actor_label,
        "count_actor_label": count_actor_label,
        "last_actor_label": last_actor_label,
        "is_stale": is_stale,
        "last_updated_text": format_timestamp(last_signal_at, missing_text="No updates yet"),
        "attention_level": attention_level,
        "is_operational_issue": status_key in {"low", "out"},
        "has_review_issue": _has_review_issue(consistency["state"]),
        "has_count_gap": item.tracking_mode != "singleton_asset" and consistency["state"] in {"no_count", "count_stale"},
        "has_status_gap": consistency["state"] in {"no_status", "status_stale"},
        "is_missing_count": item.tracking_mode != "singleton_asset" and consistency["state"] == "no_count",
        "is_stale_count": item.tracking_mode != "singleton_asset" and consistency["state"] == "count_stale",
        "is_stale_status": consistency["state"] == "status_stale",
        "operational_summary": status_meta["text"],
        "count_absolute_text": count_absolute_text,
        "status_absolute_text": status_absolute_text,
        "last_signal_at": last_signal_at,
        "last_signal_timestamp": _timestamp_sort_value(last_signal_at),
        "count_coverage_score": _count_coverage_score(item.tracking_mode, consistency["state"]),
        "primary_action_label": _primary_action_label(item.tracking_mode, consistency["state"]),
        "primary_action_mode": "status" if item.tracking_mode == "singleton_asset" else "raw_counts",
        "secondary_action_label": None if item.tracking_mode == "singleton_asset" else "Update Status",
        "secondary_action_mode": None if item.tracking_mode == "singleton_asset" else "status",
    }


def _next_step_label(tracking_mode, consistency_state):
    if tracking_mode == "singleton_asset":
        if consistency_state in {"no_status", "status_stale"}:
            return "Run Update Status"
        if consistency_state == "needs_review":
            return "Reconcile asset status"
        return "No follow-up needed"

    if consistency_state == "no_count":
        return "Run Update Count"
    if consistency_state == "count_stale":
        return "Refresh count"
    if consistency_state == "no_status":
        return "Run Update Status"
    if consistency_state == "status_stale":
        return "Refresh status"
    if consistency_state == "needs_review":
        return "Reconcile status and count"
    return "No follow-up needed"


def _format_source_label(source):
    if not source:
        return "default"
    return str(source).replace("_", " ").strip().title()


def _detail_threshold_text(days, source):
    return f"{days}d threshold ({_format_source_label(source)})"


def _detail_setup_text(tracking_mode, setup_group_display):
    setup_label = setup_group_display or "No setup group"
    mode_label = "Asset" if tracking_mode == "singleton_asset" else "Quantity"
    return f"{mode_label} / {setup_label}"


def _detail_provenance_text(timestamp_text, actor_label):
    if actor_label:
        return f"{timestamp_text} by {actor_label}"
    return timestamp_text


def _resolve_last_actor_label(
    *,
    tracking_mode,
    status_actor_label,
    status_updated_at,
    count_actor_label,
    count_updated_at,
):
    if tracking_mode == "singleton_asset":
        return status_actor_label or count_actor_label or ""
    if count_updated_at and (not status_updated_at or count_updated_at >= status_updated_at):
        return count_actor_label or status_actor_label or ""
    if status_updated_at:
        return status_actor_label or count_actor_label or ""
    return count_actor_label or status_actor_label or ""


def _word_boundary_prefix(text, query):
    if not query:
        return False
    import re

    return bool(re.search(rf"(^|\W){re.escape(query)}", text))


def _venue_inventory_search_rank(row, search_query):
    if not search_query:
        return 0

    item_text = (row.get("name") or "").lower()
    family_text = (row.get("family_name") or "").lower()
    search_text = " ".join(
        [
            item_text,
            family_text,
            (row.get("setup_group_display") or "").lower(),
            (row.get("current_status_label") or "").lower(),
        ]
    )
    if item_text.startswith(search_query):
        return 0
    if _word_boundary_prefix(item_text, search_query):
        return 1
    if search_query in item_text:
        return 2
    if family_text.startswith(search_query):
        return 3
    if _word_boundary_prefix(family_text, search_query):
        return 4
    if search_query in family_text:
        return 5
    if search_query in search_text:
        return 6
    return None


def _venue_inventory_attention_rank(row):
    return {
        "healthy": 0,
        "warning": 1,
        "critical": 2,
    }.get((row.get("attention_level") or "healthy").strip().lower(), 0)


def _venue_inventory_matches_segment(row, segment):
    if segment == "all":
        return True
    if segment == "needs_action":
        return bool(row.get("is_operational_issue"))
    if segment == "review":
        return bool(row.get("has_review_issue"))
    if segment == "assets":
        return row.get("tracking_mode") == "singleton_asset"
    return True


def _venue_inventory_matches_filter(row, filter_value):
    if filter_value == "all":
        return True
    if filter_value == "count_attention":
        return bool(row.get("has_count_gap"))
    if filter_value == "status_attention":
        return bool(row.get("has_status_gap"))
    if filter_value == "singleton_asset":
        return row.get("tracking_mode") == "singleton_asset"
    if filter_value == "quantity":
        return row.get("tracking_mode") == "quantity"
    if filter_value == "families":
        return bool(row.get("parent_item_id"))
    return True


def _sort_venue_inventory_rows(rows, *, search_query, sort_key):
    def sort_key_for_row(row):
        name_key = (row.get("name") or "").lower()
        search_rank = _venue_inventory_search_rank(row, search_query)
        normalized_search_rank = search_rank if search_rank is not None else 999

        if sort_key == "alphabetical":
            return (normalized_search_rank, name_key, row.get("id") or 0)

        if sort_key == "review_first":
            return (
                normalized_search_rank,
                0 if row.get("has_review_issue") else 1,
                0 if row.get("is_operational_issue") else 1,
                name_key,
                row.get("id") or 0,
            )

        if sort_key == "stalest":
            return (
                normalized_search_rank,
                row.get("last_signal_timestamp") if row.get("last_signal_timestamp") is not None else float("-inf"),
                name_key,
                row.get("id") or 0,
            )

        if sort_key == "lowest_count_coverage":
            return (
                normalized_search_rank,
                row.get("count_coverage_score", 999),
                name_key,
                row.get("id") or 0,
            )

        if sort_key == "recent":
            timestamp = row.get("last_signal_timestamp")
            recent_rank = -(timestamp or 0)
            return (
                normalized_search_rank,
                recent_rank,
                name_key,
                row.get("id") or 0,
            )

        timestamp = row.get("last_signal_timestamp")
        stale_rank = timestamp if timestamp is not None else float("-inf")
        return (
            normalized_search_rank,
            0 if row.get("is_operational_issue") else 1,
            0 if row.get("has_review_issue") else 1,
            -_venue_inventory_attention_rank(row),
            stale_rank,
            name_key,
            row.get("id") or 0,
        )

    return sorted(rows, key=sort_key_for_row)


def filter_venue_inventory_rows(item_rows, filters):
    search_query = (filters.get("q") or "").strip().lower()
    filtered_rows = []
    for row in item_rows:
        if search_query and _venue_inventory_search_rank(row, search_query) is None:
            continue
        if not _venue_inventory_matches_segment(row, filters.get("segment", "all")):
            continue
        if not _venue_inventory_matches_filter(row, filters.get("filter", "all")):
            continue
        filtered_rows.append(row)
    return _sort_venue_inventory_rows(
        filtered_rows,
        search_query=search_query,
        sort_key=filters.get("sort", "needs_action"),
    )


def build_venue_inventory_csv_rows(venue, item_rows):
    rows = []
    for row in item_rows:
        rows.append(
            {
                "Venue Name": sanitize_csv_cell(venue.name),
                "Item Name": sanitize_csv_cell(row.get("name") or ""),
                "Setup Group Code": sanitize_csv_cell(row.get("setup_group_code") or ""),
                "Setup Group Label": sanitize_csv_cell(row.get("setup_group_label") or ""),
                "Tracking Mode": sanitize_csv_cell("Singleton Asset" if row.get("tracking_mode") == "singleton_asset" else "Quantity"),
                "Category": sanitize_csv_cell(row.get("item_category") or ""),
                "Current Count": "" if row.get("raw_count") is None else row.get("raw_count"),
                "Effective Par": "" if row.get("par_value") is None else row.get("par_value"),
                "Suggested Order Qty": (
                    ""
                    if row.get("suggested_order_qty") is None
                    else int(row.get("suggested_order_qty") or 0)
                ),
                "Over Par Qty": (
                    ""
                    if row.get("over_par_qty") is None
                    else int(row.get("over_par_qty") or 0)
                ),
                "Current Quick Status / Last Saved Status": sanitize_csv_cell(
                    row.get("current_status_label") or ""
                ),
                "Last Updated": sanitize_csv_cell(row.get("last_updated_text") or ""),
                "Checked By": sanitize_csv_cell(row.get("last_actor_label") or ""),
                "Effective Stale Threshold": row.get("effective_stale_threshold_days") or "",
                "Is Stale": "Yes" if row.get("is_stale") else "No",
                "Note Count": int(row.get("notes_count") or 0),
            }
        )
    return rows


def build_venue_inventory_export_filename(venue, *, scope):
    tokens = [venue.name]
    if scope == "filtered":
        tokens.append("filtered")
    return build_dated_csv_filename("venue_inventory", *tokens)


def build_note_item_options(item_rows):
    options = []
    for row in item_rows:
        options.append(
            {
                "id": row["id"],
                "name": row["name"],
                "parent_name": row["parent_name"],
                "label": (
                    f'{row["name"]} ({row["parent_name"]})'
                    if row["parent_name"]
                    else row["name"]
                ),
            }
        )
    return options


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
    stale_threshold_days = None

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
        if stale_threshold_days is None:
            stale_threshold_days = child["effective_stale_threshold_days"]
        else:
            stale_threshold_days = min(stale_threshold_days, child["effective_stale_threshold_days"])

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
    group["latest_update_freshness"] = build_signal_freshness(
        latest_update_at,
        stale_threshold=stale_threshold_days,
    )
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
    return "Update Count"


def _timestamp_sort_value(value):
    normalized = ensure_utc(value)
    return int(normalized.timestamp()) if normalized else 0


def _max_timestamp(*timestamps):
    resolved = None
    for candidate in timestamps:
        if candidate and (resolved is None or candidate > resolved):
            resolved = candidate
    return resolved
