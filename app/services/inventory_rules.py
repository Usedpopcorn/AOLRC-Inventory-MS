from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher

from app import db
from app.models import (
    CheckLine,
    CountLine,
    InventoryAdminEvent,
    InventoryPolicy,
    Item,
    OrderLine,
    VenueItem,
    VenueItemCount,
)
from app.services.inventory_status import ensure_utc

GLOBAL_STALE_THRESHOLD_DEFAULT_DAYS = 2
ITEM_HARD_DELETE_WINDOW_DAYS = 30
MAX_TRACKING_VALUE = 2_147_483_647

INVENTORY_EVENT_META = {
    "inventory_policy_updated": {"label": "Inventory Rules", "icon_class": "bi-sliders"},
    "item_created": {"label": "Item Created", "icon_class": "bi-box-seam"},
    "item_updated": {"label": "Item Updated", "icon_class": "bi-pencil-square"},
    "item_tracking_updated": {"label": "Item Tracking", "icon_class": "bi-diagram-3"},
    "item_hard_deleted": {"label": "Item Deleted", "icon_class": "bi-trash"},
    "venue_created": {"label": "Venue Created", "icon_class": "bi-building-add"},
    "venue_updated": {"label": "Venue Updated", "icon_class": "bi-building-gear"},
    "venue_tracking_updated": {"label": "Venue Tracking", "icon_class": "bi-list-check"},
    "venue_tracking_copied": {"label": "Venue Copy", "icon_class": "bi-copy"},
    "bulk_tracking_updated": {"label": "Bulk Tracking", "icon_class": "bi-diagram-2"},
}


class InventoryRuleError(ValueError):
    """Raised when inventory admin/configuration input fails validation."""


@dataclass(frozen=True)
class EffectiveSetting:
    value: int | None
    source: str


def utcnow():
    return datetime.now(timezone.utc)


def serialize_event_details(details):
    if not details:
        return None
    return json.dumps(details, sort_keys=True)


def deserialize_event_details(details_json):
    if not details_json:
        return {}
    try:
        return json.loads(details_json)
    except json.JSONDecodeError:
        return {}


def format_inventory_timestamp(value, missing_text="No recorded time"):
    normalized = ensure_utc(value)
    if normalized is None:
        return missing_text
    return normalized.strftime("%Y-%m-%d %I:%M %p")


def ensure_inventory_policy():
    policy = InventoryPolicy.query.order_by(InventoryPolicy.id.asc()).first()
    if policy is None:
        policy = InventoryPolicy(default_stale_threshold_days=GLOBAL_STALE_THRESHOLD_DEFAULT_DAYS)
        db.session.add(policy)
        db.session.flush()
    return policy


def get_default_stale_threshold_days():
    policy = InventoryPolicy.query.order_by(InventoryPolicy.id.asc()).first()
    if policy is None:
        return GLOBAL_STALE_THRESHOLD_DEFAULT_DAYS
    return int(policy.default_stale_threshold_days or GLOBAL_STALE_THRESHOLD_DEFAULT_DAYS)


def normalize_optional_threshold_days(raw_value, *, field_label="Stale threshold"):
    value = (raw_value or "").strip()
    if not value:
        return None
    try:
        parsed = int(value)
    except ValueError as exc:
        raise InventoryRuleError(f"{field_label} must be a whole number of days.") from exc
    if parsed < 1:
        raise InventoryRuleError(f"{field_label} must be at least 1 day.")
    if parsed > 365:
        raise InventoryRuleError(f"{field_label} must be 365 days or fewer.")
    return parsed


def normalize_optional_tracking_value(raw_value, *, field_label):
    value = (raw_value or "").strip()
    if not value:
        return None
    try:
        parsed = int(value)
    except ValueError as exc:
        raise InventoryRuleError(f"{field_label} must be a whole number.") from exc
    if parsed < 0:
        raise InventoryRuleError(f"{field_label} cannot be negative.")
    if parsed > MAX_TRACKING_VALUE:
        raise InventoryRuleError(f"{field_label} is too large.")
    return parsed


def resolve_effective_stale_threshold_days(
    *,
    item_stale_threshold_days=None,
    venue_stale_threshold_days=None,
    global_stale_threshold_days=None,
):
    global_value = int(global_stale_threshold_days or get_default_stale_threshold_days())
    if item_stale_threshold_days is not None:
        return EffectiveSetting(int(item_stale_threshold_days), "item")
    if venue_stale_threshold_days is not None:
        return EffectiveSetting(int(venue_stale_threshold_days), "venue")
    return EffectiveSetting(global_value, "global")


def resolve_effective_par_level(*, item_default_par_level=None, venue_par_override=None):
    if venue_par_override is not None:
        return EffectiveSetting(int(venue_par_override), "venue")
    if item_default_par_level is not None:
        return EffectiveSetting(int(item_default_par_level), "item")
    return EffectiveSetting(None, "none")


def build_stale_threshold_timedelta(days):
    effective_days = int(days or GLOBAL_STALE_THRESHOLD_DEFAULT_DAYS)
    return timedelta(days=max(1, effective_days))


def exact_item_name_key(name):
    return " ".join((name or "").strip().lower().split())


def similarity_item_name_key(name):
    lowered = (name or "").strip().lower()
    lowered = re.sub(r"[^a-z0-9]+", " ", lowered)
    return " ".join(lowered.split())


def _item_name_tokens(name):
    return [token for token in similarity_item_name_key(name).split(" ") if token]


def find_similar_items(name, *, exclude_item_id=None, limit=5):
    normalized_exact = exact_item_name_key(name)
    normalized_similarity = similarity_item_name_key(name)
    if not normalized_similarity:
        return []

    rows = []
    query = Item.query.order_by(Item.name.asc(), Item.id.asc())
    if exclude_item_id is not None:
        query = query.filter(Item.id != exclude_item_id)

    new_tokens = set(_item_name_tokens(name))
    for item in query.all():
        item_exact = exact_item_name_key(item.name)
        if not item_exact or item_exact == normalized_exact:
            continue

        item_similarity = similarity_item_name_key(item.name)
        score = SequenceMatcher(None, normalized_similarity, item_similarity).ratio()
        item_tokens = set(_item_name_tokens(item.name))
        token_overlap = len(new_tokens & item_tokens)
        contains_match = normalized_similarity in item_similarity or item_similarity in normalized_similarity
        if score < 0.72 and not contains_match and token_overlap < 2:
            continue

        if contains_match and score < 0.84:
            score = 0.84
        elif token_overlap >= 2 and score < 0.76:
            score = 0.76

        rows.append(
            {
                "id": item.id,
                "name": item.name,
                "score": round(score, 2),
                "active": bool(item.active),
            }
        )

    rows.sort(key=lambda row: (-row["score"], row["name"].lower(), row["id"]))
    return rows[:limit]


def has_exact_item_name_duplicate(name, *, exclude_item_id=None):
    normalized_target = exact_item_name_key(name)
    if not normalized_target:
        return False
    query = Item.query
    if exclude_item_id is not None:
        query = query.filter(Item.id != exclude_item_id)
    for item in query.all():
        if exact_item_name_key(item.name) == normalized_target:
            return True
    return False


def log_inventory_admin_event(event_type, *, actor=None, subject_type, subject_id=None, subject_label=None, details=None):
    event = InventoryAdminEvent(
        event_type=event_type,
        actor_user_id=getattr(actor, "id", None),
        subject_type=subject_type,
        subject_id=subject_id,
        subject_label=subject_label,
        details_json=serialize_event_details(details),
    )
    db.session.add(event)
    return event


def describe_inventory_admin_event(event, *, actor_name=None):
    meta = INVENTORY_EVENT_META.get(
        event.event_type,
        {"label": "Inventory Event", "icon_class": "bi-clock-history"},
    )
    details = deserialize_event_details(event.details_json)
    subject_label = event.subject_label or details.get("subject_label") or "Inventory change"

    if event.event_type == "inventory_policy_updated":
        title = "Updated inventory rules"
        detail = (
            f"Global stale threshold {details.get('previous_threshold_days')}d -> "
            f"{details.get('new_threshold_days')}d"
        )
    elif event.event_type == "item_created":
        title = "Created item"
        detail = subject_label
    elif event.event_type == "item_updated":
        title = "Updated item defaults"
        changed_fields = details.get("changed_fields") or []
        detail = f"{subject_label} | Changed {', '.join(changed_fields)}" if changed_fields else subject_label
    elif event.event_type == "item_tracking_updated":
        title = "Adjusted item venue assignments"
        detail = _format_tracking_change_detail(subject_label, details)
    elif event.event_type == "item_hard_deleted":
        title = "Hard deleted item"
        detail = subject_label
    elif event.event_type == "venue_created":
        title = "Created venue"
        detail = subject_label
    elif event.event_type == "venue_updated":
        title = "Updated venue settings"
        changed_fields = details.get("changed_fields") or []
        detail = f"{subject_label} | Changed {', '.join(changed_fields)}" if changed_fields else subject_label
    elif event.event_type == "venue_tracking_updated":
        title = "Adjusted venue tracked items"
        detail = _format_tracking_change_detail(subject_label, details)
    elif event.event_type == "venue_tracking_copied":
        title = "Copied venue setup"
        detail = f"{subject_label} | From {details.get('source_venue_name', 'another venue')}"
    elif event.event_type == "bulk_tracking_updated":
        title = "Saved bulk tracking setup"
        detail = _format_tracking_change_detail(subject_label, details)
    else:
        title = meta["label"]
        detail = subject_label

    return {
        "title": title,
        "detail": detail,
        "kind_label": meta["label"],
        "icon_class": meta["icon_class"],
        "actor_name": actor_name or "System",
        "subject_label": subject_label,
        "changed_at": ensure_utc(event.created_at),
        "changed_at_text": format_inventory_timestamp(event.created_at),
    }


def _format_tracking_change_detail(subject_label, details):
    segments = []
    added = int(details.get("added_count") or 0)
    activated = int(details.get("activated_count") or 0)
    removed = int(details.get("removed_count") or 0)
    override_count = int(details.get("override_changed_count") or 0)
    if added:
        segments.append(f"Added {added}")
    if activated:
        segments.append(f"Reactivated {activated}")
    if removed:
        segments.append(f"Removed {removed}")
    if override_count:
        segments.append(f"Par overrides updated {override_count}")
    if not segments:
        return subject_label
    return f"{subject_label} | " + " | ".join(segments)


def build_item_delete_guard(item, *, now=None):
    reference_time = ensure_utc(now or utcnow())
    created_at = ensure_utc(item.created_at) or reference_time
    delete_deadline = created_at + timedelta(days=ITEM_HARD_DELETE_WINDOW_DAYS)
    remaining_days = max((delete_deadline - reference_time).days, 0)

    child_count = Item.query.filter(Item.parent_item_id == item.id).count()
    venue_link_count = VenueItem.query.filter(VenueItem.item_id == item.id).count()
    count_history_exists = db.session.query(CountLine.id).filter(CountLine.item_id == item.id).first() is not None
    status_history_exists = db.session.query(CheckLine.id).filter(CheckLine.item_id == item.id).first() is not None
    snapshot_history_exists = (
        db.session.query(VenueItemCount.id).filter(VenueItemCount.item_id == item.id).first() is not None
    )
    order_history_exists = db.session.query(OrderLine.id).filter(OrderLine.item_id == item.id).first() is not None

    blockers = []
    if reference_time > delete_deadline:
        blockers.append(
            f"Hard delete is only allowed within {ITEM_HARD_DELETE_WINDOW_DAYS} days of creation."
        )
    if child_count:
        blockers.append("Family organizers with child items cannot be hard deleted.")
    if venue_link_count:
        blockers.append("Tracked venue assignments exist for this item.")
    if count_history_exists or status_history_exists or snapshot_history_exists or order_history_exists:
        blockers.append("Inventory history exists for this item.")

    return {
        "eligible": not blockers,
        "delete_deadline_text": format_inventory_timestamp(delete_deadline),
        "remaining_days": remaining_days,
        "child_count": child_count,
        "venue_link_count": venue_link_count,
        "has_history": bool(
            count_history_exists or status_history_exists or snapshot_history_exists or order_history_exists
        ),
        "blockers": blockers,
    }


def sync_item_venue_assignments(*, item, selected_venue_ids, par_overrides=None):
    selected_ids = {int(venue_id) for venue_id in selected_venue_ids or []}
    # Item create/edit only manages which venues track the item. When an edit
    # does not submit per-venue override values, preserve any existing venue-
    # specific par overrides instead of clearing them.
    update_overrides = par_overrides is not None
    normalized_overrides = {
        int(venue_id): par_overrides.get(int(venue_id))
        for venue_id in selected_ids
    } if update_overrides else {}
    existing_links = VenueItem.query.filter(VenueItem.item_id == item.id).all()
    links_by_venue_id = {link.venue_id: link for link in existing_links}

    added_count = 0
    activated_count = 0
    removed_count = 0
    override_changed_count = 0

    for venue_id in selected_ids:
        override_value = normalized_overrides.get(venue_id)
        link = links_by_venue_id.get(venue_id)
        if link is None:
            db.session.add(
                VenueItem(
                    venue_id=venue_id,
                    item_id=item.id,
                    expected_qty=override_value,
                    active=True,
                )
            )
            added_count += 1
            if override_value is not None:
                override_changed_count += 1
            continue
        if not link.active:
            link.active = True
            activated_count += 1
        if update_overrides and link.expected_qty != override_value:
            link.expected_qty = override_value
            override_changed_count += 1

    for link in existing_links:
        if link.active and link.venue_id not in selected_ids:
            link.active = False
            removed_count += 1

    return {
        "added_count": added_count,
        "activated_count": activated_count,
        "removed_count": removed_count,
        "override_changed_count": override_changed_count,
    }


def sync_venue_tracked_items(*, venue, selected_item_ids, par_overrides):
    selected_ids = {int(item_id) for item_id in selected_item_ids or []}
    normalized_overrides = {
        int(item_id): par_overrides.get(int(item_id))
        for item_id in selected_ids
    }
    existing_links = VenueItem.query.filter(VenueItem.venue_id == venue.id).all()
    links_by_item_id = {link.item_id: link for link in existing_links}

    added_count = 0
    activated_count = 0
    removed_count = 0
    override_changed_count = 0

    for item_id in selected_ids:
        override_value = normalized_overrides.get(item_id)
        link = links_by_item_id.get(item_id)
        if link is None:
            db.session.add(
                VenueItem(
                    venue_id=venue.id,
                    item_id=item_id,
                    expected_qty=override_value,
                    active=True,
                )
            )
            added_count += 1
            if override_value is not None:
                override_changed_count += 1
            continue

        if not link.active:
            link.active = True
            activated_count += 1

        if link.expected_qty != override_value:
            link.expected_qty = override_value
            override_changed_count += 1

    for link in existing_links:
        if link.active and link.item_id not in selected_ids:
            link.active = False
            removed_count += 1

    return {
        "added_count": added_count,
        "activated_count": activated_count,
        "removed_count": removed_count,
        "override_changed_count": override_changed_count,
    }


def copy_venue_tracking_setup(*, source_venue, target_venue):
    source_links = (
        VenueItem.query.join(Item, Item.id == VenueItem.item_id)
        .filter(
            VenueItem.venue_id == source_venue.id,
            VenueItem.active == True,
            Item.active == True,
            Item.is_group_parent == False,
        )
        .all()
    )

    selected_item_ids = [link.item_id for link in source_links]
    par_overrides = {link.item_id: link.expected_qty for link in source_links}
    summary = sync_venue_tracked_items(
        venue=target_venue,
        selected_item_ids=selected_item_ids,
        par_overrides=par_overrides,
    )
    summary["source_venue_name"] = source_venue.name
    return summary
