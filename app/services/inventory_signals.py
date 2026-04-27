from __future__ import annotations

from app import db
from app.models import Check, CheckLine, CountLine, CountSession, User
from app.services.inventory_status import ensure_utc


def format_signal_actor_label(display_name=None, email=None):
    resolved_display_name = (display_name or "").strip()
    if resolved_display_name:
        return resolved_display_name
    resolved_email = (email or "").strip()
    if resolved_email:
        return resolved_email
    return ""


def build_latest_status_signal_map(*, venue_ids=None, item_ids=None):
    query = (
        db.session.query(
            Check.venue_id.label("venue_id"),
            CheckLine.item_id.label("item_id"),
            CheckLine.status.label("status"),
            Check.created_at.label("updated_at"),
            User.display_name.label("actor_display_name"),
            User.email.label("actor_email"),
        )
        .select_from(Check)
        .join(CheckLine, CheckLine.check_id == Check.id)
        .outerjoin(User, User.id == Check.user_id)
    )
    if venue_ids is not None:
        if not venue_ids:
            return {}
        query = query.filter(Check.venue_id.in_(venue_ids))
    if item_ids is not None:
        if not item_ids:
            return {}
        query = query.filter(CheckLine.item_id.in_(item_ids))

    latest_by_key = {}
    for row in query.order_by(
        Check.venue_id.asc(),
        CheckLine.item_id.asc(),
        Check.created_at.desc(),
        Check.id.desc(),
    ).all():
        key = (row.venue_id, row.item_id)
        if key in latest_by_key:
            continue
        latest_by_key[key] = {
            "status": row.status,
            "updated_at": ensure_utc(row.updated_at),
            "actor_label": format_signal_actor_label(
                row.actor_display_name,
                row.actor_email,
            ),
        }
    return latest_by_key


def build_latest_count_signal_map(*, venue_ids=None, item_ids=None):
    query = (
        db.session.query(
            CountSession.venue_id.label("venue_id"),
            CountLine.item_id.label("item_id"),
            CountLine.raw_count.label("raw_count"),
            CountSession.created_at.label("updated_at"),
            User.display_name.label("actor_display_name"),
            User.email.label("actor_email"),
        )
        .select_from(CountSession)
        .join(CountLine, CountLine.count_session_id == CountSession.id)
        .outerjoin(User, User.id == CountSession.user_id)
    )
    if venue_ids is not None:
        if not venue_ids:
            return {}
        query = query.filter(CountSession.venue_id.in_(venue_ids))
    if item_ids is not None:
        if not item_ids:
            return {}
        query = query.filter(CountLine.item_id.in_(item_ids))

    latest_by_key = {}
    for row in query.order_by(
        CountSession.venue_id.asc(),
        CountLine.item_id.asc(),
        CountSession.created_at.desc(),
        CountSession.id.desc(),
    ).all():
        key = (row.venue_id, row.item_id)
        if key in latest_by_key:
            continue
        latest_by_key[key] = {
            "raw_count": row.raw_count,
            "updated_at": ensure_utc(row.updated_at),
            "actor_label": format_signal_actor_label(
                row.actor_display_name,
                row.actor_email,
            ),
        }
    return latest_by_key
