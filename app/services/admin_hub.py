from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import Integer, String, and_, cast, func, literal, or_, select, union_all
from sqlalchemy.orm import aliased

from app import db
from app.models import (
    AccountAuditEvent,
    Check,
    CheckLine,
    CountLine,
    CountSession,
    Item,
    PasswordActionToken,
    User,
    Venue,
    VenueNote,
)
from app.services.account_security import describe_account_event
from app.services.inventory_status import ensure_utc, normalize_status

USER_ACTIVITY_WINDOW_DAYS = 30
RECENT_ACTIVITY_LIMIT = 8
RECENT_HISTORY_LIMIT = 12
FEED_PREVIEW_LIMIT = 2
RANKING_PREVIEW_LIMIT = 3
ATTENTION_PREVIEW_LIMIT = 2
GROUP_PREVIEW_LIMIT = 1

USER_ROLE_META = {
    "admin": {"label": "Admin", "tone": "danger", "icon_class": "bi-shield-lock"},
    "staff": {"label": "Staff", "tone": "primary", "icon_class": "bi-person-badge"},
    "viewer": {"label": "Viewer", "tone": "secondary", "icon_class": "bi-eye"},
}

CHANGE_TYPE_META = {
    "status": {
        "label": "Status",
        "icon_class": "bi-clipboard-check",
        "badge_class": "activity-type-status",
    },
    "raw_count": {
        "label": "Count",
        "icon_class": "bi-123",
        "badge_class": "activity-type-count",
    },
}


def format_admin_timestamp(value, missing_text="No recorded time"):
    normalized = ensure_utc(value)
    if normalized is None:
        return missing_text
    return normalized.strftime("%Y-%m-%d %I:%M %p")


def build_user_display_name(display_name, email):
    return (display_name or "").strip() or (email or "Unknown user")


def utcnow_naive():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def is_user_locked(user, now=None):
    locked_until = ensure_utc(getattr(user, "locked_until", None))
    reference_time = ensure_utc(now or utcnow_naive())
    return bool(locked_until and reference_time and locked_until > reference_time)


def build_admin_overview_view_model():
    user_summary = build_user_summary_counts()
    item_summary = build_item_summary_counts()
    locked_rows = build_locked_user_rows(limit=6)
    operational_rows = _build_recent_operational_activity_rows(limit=RECENT_ACTIVITY_LIMIT)
    system_change_rows = _build_recent_system_change_rows(limit=RECENT_ACTIVITY_LIMIT)
    history_rows = _build_inventory_change_rows(limit=RECENT_HISTORY_LIMIT)
    return {
        "summary": user_summary,
        "item_summary": item_summary,
        "locked_users": _build_preview_state(locked_rows, preview_limit=ATTENTION_PREVIEW_LIMIT),
        "recent_operational_activity": _build_preview_state(operational_rows, preview_limit=FEED_PREVIEW_LIMIT),
        "recent_system_changes": _build_preview_state(system_change_rows, preview_limit=FEED_PREVIEW_LIMIT),
        "module_summary": {
            "users": {
                "primary_value": user_summary["active_users"],
                "primary_label": "Active users",
                "secondary_value": user_summary["locked_users"],
                "secondary_label": "Locked now",
                "note": "Review roles, access, and lockouts.",
            },
            "items": {
                "primary_value": item_summary["active_items"],
                "primary_label": "Active items",
                "secondary_value": item_summary["inactive_items"],
                "secondary_label": "Inactive items",
                "note": "Open the catalog and item families.",
            },
            "audit": {
                "primary_value": user_summary["attention_users"],
                "primary_label": "Need attention",
                "secondary_value": operational_rows[0]["changed_at_text"] if operational_rows else "No activity",
                "secondary_label": "Latest activity",
                "note": operational_rows[0]["kind_label"] if operational_rows else "No retained activity yet.",
            },
            "history": {
                "primary_value": len(history_rows),
                "primary_label": "Retained changes",
                "secondary_value": system_change_rows[0]["changed_at_text"] if system_change_rows else "No updates",
                "secondary_label": "Latest update",
                "note": system_change_rows[0]["title"] if system_change_rows else "No retained system updates yet.",
            },
        },
    }


def build_admin_user_list_view_model(page=1, per_page=12, actor=None):
    role_counts = _build_role_counts()
    summary = build_user_summary_counts()
    query = User.query.order_by(User.created_at.desc(), User.email.asc())
    total_count = query.count()
    total_pages = max(1, (total_count + per_page - 1) // per_page)
    current_page = min(max(int(page or 1), 1), total_pages)
    users = query.offset((current_page - 1) * per_page).limit(per_page).all()
    pending_tokens = _build_pending_password_token_map([user.id for user in users])
    showing_from = (current_page - 1) * per_page + 1 if total_count else 0
    showing_to = min(current_page * per_page, total_count)
    return {
        "summary": summary,
        "role_counts": role_counts,
        "rows": [
            _serialize_user_row(
                user,
                actor=actor,
                pending_token=pending_tokens.get(user.id),
                include_password_state=True,
            )
            for user in users
        ],
        "pagination": {
            "current_page": current_page,
            "per_page": per_page,
            "total_count": total_count,
            "total_pages": total_pages,
            "has_prev": current_page > 1,
            "has_next": current_page < total_pages,
            "prev_page": current_page - 1 if current_page > 1 else None,
            "next_page": current_page + 1 if current_page < total_pages else None,
            "showing_from": showing_from,
            "showing_to": showing_to,
        },
    }


def build_admin_user_detail_view_model(user_id, actor=None):
    user = db.get_or_404(User, user_id)
    pending_token = _build_pending_password_token_map([user.id]).get(user.id)
    user_row = _serialize_user_row(
        user,
        actor=actor,
        pending_token=pending_token,
        include_password_state=True,
    )
    recent_events = _build_recent_account_event_rows(limit=12, target_user_id=user.id)
    return {
        "row": user_row,
        "recent_account_events": _build_preview_state(recent_events, preview_limit=FEED_PREVIEW_LIMIT),
    }


def build_admin_user_audit_view_model():
    role_counts = _build_role_counts()
    score_rows = _build_user_activity_score_rows(window_days=USER_ACTIVITY_WINDOW_DAYS)
    most_active_users = [row for row in score_rows if row["score"] > 0][:5]
    least_active_users = sorted(
        score_rows,
        key=lambda row: (row["score"], row["display_name"].lower(), row["email"]),
    )[:5]
    locked_rows = build_locked_user_rows(limit=6)
    inactive_rows = _build_inactive_user_rows(limit=6)
    recent_activity_rows = _build_recent_user_activity_rows(limit=RECENT_HISTORY_LIMIT)
    recent_account_rows = _build_recent_account_event_rows(limit=RECENT_HISTORY_LIMIT)
    return {
        "role_counts": role_counts,
        "summary": {
            "locked": len(build_locked_user_rows()),
            "inactive": len(_build_inactive_user_rows()),
            "window_days": USER_ACTIVITY_WINDOW_DAYS,
        },
        "most_active_users": _build_preview_state(most_active_users, preview_limit=RANKING_PREVIEW_LIMIT),
        "least_active_users": _build_preview_state(least_active_users, preview_limit=RANKING_PREVIEW_LIMIT),
        "locked_users": _build_preview_state(locked_rows, preview_limit=ATTENTION_PREVIEW_LIMIT),
        "inactive_users": _build_preview_state(inactive_rows, preview_limit=ATTENTION_PREVIEW_LIMIT),
        "recent_activity": _build_preview_state(recent_activity_rows, preview_limit=FEED_PREVIEW_LIMIT),
        "recent_account_activity": _build_preview_state(recent_account_rows, preview_limit=FEED_PREVIEW_LIMIT),
    }


def build_admin_history_view_model():
    inventory_rows = _build_inventory_change_rows(limit=RECENT_HISTORY_LIMIT)
    note_rows = _build_recent_note_update_rows(limit=8)
    account_event_rows = _build_recent_account_event_rows(limit=RECENT_HISTORY_LIMIT)
    recent_creation_groups = _build_recent_creation_groups(limit=5)
    archive_groups = {
        "users": _build_inactive_user_rows(limit=8),
        "venues": _build_inactive_venue_rows(limit=8),
        "items": _build_inactive_item_rows(limit=8),
    }
    return {
        "account_events": _build_preview_state(account_event_rows, preview_limit=FEED_PREVIEW_LIMIT),
        "inventory_changes": _build_preview_state(inventory_rows, preview_limit=FEED_PREVIEW_LIMIT),
        "note_updates": _build_preview_state(note_rows, preview_limit=FEED_PREVIEW_LIMIT),
        "recent_creations": {
            key: _build_preview_state(rows, preview_limit=GROUP_PREVIEW_LIMIT)
            for key, rows in recent_creation_groups.items()
        },
        "archive": {
            key: _build_preview_state(rows, preview_limit=GROUP_PREVIEW_LIMIT)
            for key, rows in archive_groups.items()
        },
    }


def build_user_summary_counts():
    now = utcnow_naive()
    locked_filter = and_(User.locked_until.is_not(None), User.locked_until > now)
    return {
        "total_users": User.query.count(),
        "active_users": User.query.filter(User.active == True).count(),
        "inactive_users": User.query.filter(User.active == False).count(),
        "locked_users": User.query.filter(locked_filter).count(),
        "attention_users": User.query.filter(or_(User.active == False, locked_filter)).count(),
    }


def build_item_summary_counts():
    return {
        "total_items": Item.query.count(),
        "active_items": Item.query.filter(Item.active == True).count(),
        "inactive_items": Item.query.filter(Item.active == False).count(),
    }


def build_locked_user_rows(limit=None):
    now = utcnow_naive()
    query = User.query.filter(User.locked_until.is_not(None), User.locked_until > now).order_by(
        User.locked_until.asc(),
        User.email.asc(),
    )
    if limit is not None:
        query = query.limit(limit)
    return [_serialize_user_row(user) for user in query.all()]


def _build_preview_state(rows, preview_limit=FEED_PREVIEW_LIMIT):
    preview_rows = list(rows[:preview_limit])
    remaining_rows = list(rows[preview_limit:])
    return {
        "rows": list(rows),
        "preview": preview_rows,
        "remaining": remaining_rows,
        "preview_count": len(preview_rows),
        "loaded_count": len(rows),
        "remaining_count": len(remaining_rows),
        "has_more": bool(remaining_rows),
        "latest": preview_rows[0] if preview_rows else None,
    }


def _build_inactive_user_rows(limit=None):
    query = User.query.filter(User.active == False).order_by(User.created_at.desc(), User.email.asc())
    if limit is not None:
        query = query.limit(limit)
    return [_serialize_user_row(user) for user in query.all()]


def _build_inactive_venue_rows(limit=None):
    query = Venue.query.filter(Venue.active == False).order_by(Venue.created_at.desc(), Venue.name.asc())
    if limit is not None:
        query = query.limit(limit)
    return [
        {
            "id": venue.id,
            "name": venue.name,
            "created_at_text": format_admin_timestamp(venue.created_at),
            "is_core": bool(venue.is_core),
        }
        for venue in query.all()
    ]


def _build_inactive_item_rows(limit=None):
    query = Item.query.filter(Item.active == False).order_by(Item.created_at.desc(), Item.name.asc())
    if limit is not None:
        query = query.limit(limit)
    rows = []
    for item in query.all():
        rows.append(
            {
                "id": item.id,
                "name": item.name,
                "created_at_text": format_admin_timestamp(item.created_at),
                "tracking_label": "Asset" if item.is_singleton_asset else "Quantity",
                "is_group_parent": bool(item.is_group_parent),
            }
        )
    return rows


def _build_pending_password_token_map(user_ids):
    if not user_ids:
        return {}

    reference_time = ensure_utc(utcnow_naive())
    tokens = (
        PasswordActionToken.query.filter(
            PasswordActionToken.user_id.in_(user_ids),
            PasswordActionToken.consumed_at.is_(None),
        )
        .order_by(PasswordActionToken.created_at.desc(), PasswordActionToken.id.desc())
        .all()
    )

    pending_tokens = {}
    for token in tokens:
        if token.user_id in pending_tokens:
            continue
        if ensure_utc(token.expires_at) <= reference_time:
            continue
        pending_tokens[token.user_id] = token
    return pending_tokens


def _build_user_password_status(user, pending_token=None):
    if pending_token is not None:
        is_setup = pending_token.purpose == "password_setup"
        return {
            "label": "Setup link ready" if is_setup else "Reset link ready",
            "tone": "warning",
            "detail": f"Expires {format_admin_timestamp(pending_token.expires_at)}",
        }
    if user.force_password_change:
        return {
            "label": "Setup required",
            "tone": "warning",
            "detail": "Password has not been set yet.",
        }
    if user.password_changed_at:
        return {
            "label": "Password set",
            "tone": "success",
            "detail": f"Changed {format_admin_timestamp(user.password_changed_at)}",
        }
    return {
        "label": "Password available",
        "tone": "neutral",
        "detail": "Legacy password record.",
    }


def _serialize_user_row(user, *, actor=None, pending_token=None, include_password_state=False):
    role_meta = USER_ROLE_META.get(user.role, USER_ROLE_META["viewer"])
    locked = is_user_locked(user)
    is_self = bool(actor and getattr(actor, "id", None) == user.id)
    row = {
        "id": user.id,
        "email": user.email,
        "display_name": build_user_display_name(user.display_name, user.email),
        "role": user.role,
        "role_label": role_meta["label"],
        "role_tone": role_meta["tone"],
        "role_icon_class": role_meta["icon_class"],
        "is_active": bool(user.active),
        "active_label": "Active" if user.active else "Inactive",
        "active_tone": "success" if user.active else "secondary",
        "is_locked": locked,
        "locked_label": "Locked" if locked else "Unlocked",
        "locked_tone": "warning" if locked else "neutral",
        "failed_login_attempts": int(user.failed_login_attempts or 0),
        "locked_until_text": format_admin_timestamp(user.locked_until, missing_text="Not locked"),
        "created_at_text": format_admin_timestamp(user.created_at),
        "last_login_at_text": format_admin_timestamp(user.last_login_at, missing_text="Never"),
        "password_changed_at_text": format_admin_timestamp(user.password_changed_at, missing_text="Not recorded"),
        "deactivated_at_text": format_admin_timestamp(user.deactivated_at, missing_text="Not deactivated"),
        "is_self": is_self,
        "manage_url": f"/admin/users/{user.id}/edit",
    }
    if include_password_state:
        password_status = _build_user_password_status(user, pending_token=pending_token)
        row.update(
            {
                "password_status_label": password_status["label"],
                "password_status_tone": password_status["tone"],
                "password_status_detail": password_status["detail"],
            }
        )
    return row


def _build_role_counts():
    counts = {role: 0 for role in USER_ROLE_META}
    for role, count in db.session.query(User.role, func.count(User.id)).group_by(User.role).all():
        if role in counts:
            counts[role] = count
    return counts


def _build_recent_operational_activity_rows(limit=RECENT_ACTIVITY_LIMIT):
    check_rows = (
        db.session.query(
            Check.id.label("event_id"),
            Check.created_at.label("changed_at"),
            Venue.name.label("venue_name"),
            User.display_name.label("actor_display_name"),
            User.email.label("actor_email"),
            func.count(CheckLine.id).label("entry_count"),
        )
        .join(Venue, Venue.id == Check.venue_id)
        .outerjoin(User, User.id == Check.user_id)
        .outerjoin(CheckLine, CheckLine.check_id == Check.id)
        .group_by(Check.id, Check.created_at, Venue.name, User.display_name, User.email)
        .order_by(Check.created_at.desc(), Check.id.desc())
        .limit(limit * 2)
        .all()
    )
    count_rows = (
        db.session.query(
            CountSession.id.label("event_id"),
            CountSession.created_at.label("changed_at"),
            Venue.name.label("venue_name"),
            User.display_name.label("actor_display_name"),
            User.email.label("actor_email"),
            func.count(CountLine.id).label("entry_count"),
        )
        .join(Venue, Venue.id == CountSession.venue_id)
        .outerjoin(User, User.id == CountSession.user_id)
        .outerjoin(CountLine, CountLine.count_session_id == CountSession.id)
        .group_by(CountSession.id, CountSession.created_at, Venue.name, User.display_name, User.email)
        .order_by(CountSession.created_at.desc(), CountSession.id.desc())
        .limit(limit * 2)
        .all()
    )

    merged_rows = []
    for row in check_rows:
        actor_name = build_user_display_name(row.actor_display_name, row.actor_email)
        merged_rows.append(
            {
                "kind_label": "Status check",
                "actor_name": actor_name,
                "icon_class": "bi-clipboard-check",
                "title": "Submitted status check",
                "detail": f"{row.venue_name} | {row.entry_count} tracked item{'s' if row.entry_count != 1 else ''}",
                "changed_at": ensure_utc(row.changed_at),
                "changed_at_text": format_admin_timestamp(row.changed_at),
            }
        )

    for row in count_rows:
        actor_name = build_user_display_name(row.actor_display_name, row.actor_email)
        merged_rows.append(
            {
                "kind_label": "Raw count session",
                "actor_name": actor_name,
                "icon_class": "bi-123",
                "title": "Submitted raw count session",
                "detail": f"{row.venue_name} | {row.entry_count} counted line{'s' if row.entry_count != 1 else ''}",
                "changed_at": ensure_utc(row.changed_at),
                "changed_at_text": format_admin_timestamp(row.changed_at),
            }
        )

    merged_rows.sort(
        key=lambda row: (
            row["changed_at"] is None,
            row["changed_at"] or ensure_utc(datetime.min),
        ),
        reverse=True,
    )
    return merged_rows[:limit]


def _build_recent_system_change_rows(limit=RECENT_ACTIVITY_LIMIT):
    note_rows = _build_recent_note_update_rows(limit=limit)
    account_rows = _build_recent_account_event_rows(limit=limit)
    venue_rows = (
        Venue.query.order_by(Venue.created_at.desc(), Venue.id.desc()).limit(limit).all()
    )
    item_rows = (
        Item.query.order_by(Item.created_at.desc(), Item.id.desc()).limit(limit).all()
    )

    merged_rows = []
    for row in note_rows:
        merged_rows.append(
            {
                "icon_class": "bi-journal-text",
                "title": row["title"],
                "detail": row["detail"],
                "changed_at": row["changed_at"],
                "changed_at_text": row["changed_at_text"],
            }
        )

    for row in account_rows:
        merged_rows.append(
            {
                "icon_class": row["icon_class"],
                "title": row["title"],
                "detail": f"{row['detail']} | {row['actor_name']}",
                "changed_at": row["changed_at"],
                "changed_at_text": row["changed_at_text"],
            }
        )

    for venue in venue_rows:
        merged_rows.append(
            {
                "icon_class": "bi-building-add",
                "title": f"Created venue {venue.name}",
                "detail": "Primary venue" if venue.is_core else "Other venue",
                "changed_at": ensure_utc(venue.created_at),
                "changed_at_text": format_admin_timestamp(venue.created_at),
            }
        )

    for item in item_rows:
        merged_rows.append(
            {
                "icon_class": "bi-box-seam",
                "title": f"Created item {item.name}",
                "detail": "Family organizer" if item.is_group_parent else ("Asset" if item.is_singleton_asset else "Quantity item"),
                "changed_at": ensure_utc(item.created_at),
                "changed_at_text": format_admin_timestamp(item.created_at),
            }
        )

    merged_rows.sort(
        key=lambda row: (
            row["changed_at"] is None,
            row["changed_at"] or ensure_utc(datetime.min),
        ),
        reverse=True,
    )
    return merged_rows[:limit]


def _build_recent_account_event_rows(limit=RECENT_HISTORY_LIMIT, target_user_id=None):
    actor_user = aliased(User)
    target_user = aliased(User)
    query = (
        db.session.query(AccountAuditEvent, actor_user, target_user)
        .outerjoin(actor_user, actor_user.id == AccountAuditEvent.actor_user_id)
        .outerjoin(target_user, target_user.id == AccountAuditEvent.target_user_id)
        .order_by(AccountAuditEvent.created_at.desc(), AccountAuditEvent.id.desc())
    )
    if target_user_id is not None:
        query = query.filter(AccountAuditEvent.target_user_id == target_user_id)
    if limit is not None:
        query = query.limit(limit)

    rows = []
    for event, actor, target in query.all():
        actor_name = build_user_display_name(
            getattr(actor, "display_name", None),
            getattr(actor, "email", None),
        )
        target_name = build_user_display_name(
            getattr(target, "display_name", None),
            getattr(target, "email", None) or event.target_email,
        )
        rows.append(
            describe_account_event(
                event,
                actor_name=actor_name,
                target_name=target_name,
            )
        )
    return rows


def _build_recent_note_update_rows(limit=8):
    query_rows = (
        db.session.query(
            VenueNote,
            Venue.name.label("venue_name"),
            User.display_name.label("author_display_name"),
            User.email.label("author_email"),
        )
        .join(Venue, Venue.id == VenueNote.venue_id)
        .outerjoin(User, User.id == VenueNote.author_user_id)
        .order_by(VenueNote.updated_at.desc(), VenueNote.id.desc())
        .limit(limit)
        .all()
    )
    rows = []
    for note, venue_name, author_display_name, author_email in query_rows:
        created_at = ensure_utc(note.created_at)
        updated_at = ensure_utc(note.updated_at)
        is_edited = bool(
            created_at
            and updated_at
            and (updated_at - created_at) > timedelta(seconds=1)
        )
        effective_at = updated_at if updated_at else created_at
        rows.append(
            {
                "title": f"{'Edited' if is_edited else 'Added'} venue note for {venue_name}",
                "detail": f"{note.title} - {build_user_display_name(author_display_name, author_email)}",
                "changed_at": effective_at,
                "changed_at_text": format_admin_timestamp(effective_at),
            }
        )
    return rows


def _build_user_activity_score_rows(window_days=USER_ACTIVITY_WINDOW_DAYS):
    window_start = utcnow_naive() - timedelta(days=window_days)
    check_counts = {
        user_id: count
        for user_id, count in (
            db.session.query(Check.user_id, func.count(Check.id))
            .filter(Check.user_id.is_not(None), Check.created_at >= window_start)
            .group_by(Check.user_id)
            .all()
        )
    }
    count_session_counts = {
        user_id: count
        for user_id, count in (
            db.session.query(CountSession.user_id, func.count(CountSession.id))
            .filter(CountSession.user_id.is_not(None), CountSession.created_at >= window_start)
            .group_by(CountSession.user_id)
            .all()
        )
    }
    note_counts = {
        user_id: count
        for user_id, count in (
            db.session.query(VenueNote.author_user_id, func.count(VenueNote.id))
            .filter(
                VenueNote.author_user_id.is_not(None),
                or_(VenueNote.created_at >= window_start, VenueNote.updated_at >= window_start),
            )
            .group_by(VenueNote.author_user_id)
            .all()
        )
    }

    rows = []
    for user in User.query.order_by(User.email.asc()).all():
        checks = int(check_counts.get(user.id, 0) or 0)
        raw_counts = int(count_session_counts.get(user.id, 0) or 0)
        notes = int(note_counts.get(user.id, 0) or 0)
        score = checks + raw_counts + notes
        rows.append(
            {
                **_serialize_user_row(user),
                "checks": checks,
                "raw_counts": raw_counts,
                "notes": notes,
                "score": score,
                "score_text": f"{score} recorded touchpoint{'s' if score != 1 else ''}",
            }
        )

    rows.sort(
        key=lambda row: (-row["score"], row["display_name"].lower(), row["email"]),
    )
    return rows


def _build_recent_user_activity_rows(limit=RECENT_HISTORY_LIMIT):
    check_rows = (
        db.session.query(
            Check.id.label("event_id"),
            Check.created_at.label("changed_at"),
            Venue.name.label("venue_name"),
            User.display_name.label("actor_display_name"),
            User.email.label("actor_email"),
            func.count(CheckLine.id).label("entry_count"),
        )
        .join(Venue, Venue.id == Check.venue_id)
        .join(User, User.id == Check.user_id)
        .outerjoin(CheckLine, CheckLine.check_id == Check.id)
        .group_by(Check.id, Check.created_at, Venue.name, User.display_name, User.email)
        .order_by(Check.created_at.desc(), Check.id.desc())
        .limit(limit * 2)
        .all()
    )
    count_rows = (
        db.session.query(
            CountSession.id.label("event_id"),
            CountSession.created_at.label("changed_at"),
            Venue.name.label("venue_name"),
            User.display_name.label("actor_display_name"),
            User.email.label("actor_email"),
            func.count(CountLine.id).label("entry_count"),
        )
        .join(Venue, Venue.id == CountSession.venue_id)
        .join(User, User.id == CountSession.user_id)
        .outerjoin(CountLine, CountLine.count_session_id == CountSession.id)
        .group_by(CountSession.id, CountSession.created_at, Venue.name, User.display_name, User.email)
        .order_by(CountSession.created_at.desc(), CountSession.id.desc())
        .limit(limit * 2)
        .all()
    )
    note_rows = (
        db.session.query(
            VenueNote,
            Venue.name.label("venue_name"),
            User.display_name.label("actor_display_name"),
            User.email.label("actor_email"),
        )
        .join(Venue, Venue.id == VenueNote.venue_id)
        .join(User, User.id == VenueNote.author_user_id)
        .order_by(VenueNote.updated_at.desc(), VenueNote.id.desc())
        .limit(limit * 2)
        .all()
    )

    merged_rows = []
    for row in check_rows:
        merged_rows.append(
            {
                "title": "Submitted status check",
                "detail": f"{row.venue_name} - {row.entry_count} tracked item{'s' if row.entry_count != 1 else ''}",
                "actor_name": build_user_display_name(row.actor_display_name, row.actor_email),
                "icon_class": "bi-clipboard-check",
                "changed_at": ensure_utc(row.changed_at),
                "changed_at_text": format_admin_timestamp(row.changed_at),
            }
        )
    for row in count_rows:
        merged_rows.append(
            {
                "title": "Submitted raw count session",
                "detail": f"{row.venue_name} - {row.entry_count} counted line{'s' if row.entry_count != 1 else ''}",
                "actor_name": build_user_display_name(row.actor_display_name, row.actor_email),
                "icon_class": "bi-123",
                "changed_at": ensure_utc(row.changed_at),
                "changed_at_text": format_admin_timestamp(row.changed_at),
            }
        )
    for note, venue_name, actor_display_name, actor_email in note_rows:
        created_at = ensure_utc(note.created_at)
        updated_at = ensure_utc(note.updated_at)
        is_edited = bool(
            created_at
            and updated_at
            and (updated_at - created_at) > timedelta(seconds=1)
        )
        effective_at = updated_at if updated_at else created_at
        merged_rows.append(
            {
                "title": "Edited venue note" if is_edited else "Added venue note",
                "detail": f"{venue_name} - {note.title}",
                "actor_name": build_user_display_name(actor_display_name, actor_email),
                "icon_class": "bi-journal-text",
                "changed_at": effective_at,
                "changed_at_text": format_admin_timestamp(effective_at),
            }
        )

    merged_rows.sort(
        key=lambda row: (
            row["changed_at"] is None,
            row["changed_at"] or ensure_utc(datetime.min),
        ),
        reverse=True,
    )
    return merged_rows[:limit]


def _build_recent_creation_groups(limit=5):
    users = (
        User.query.order_by(User.created_at.desc(), User.id.desc()).limit(limit).all()
    )
    venues = (
        Venue.query.order_by(Venue.created_at.desc(), Venue.id.desc()).limit(limit).all()
    )
    items = (
        Item.query.order_by(Item.created_at.desc(), Item.id.desc()).limit(limit).all()
    )
    return {
        "users": [
            {
                "name": build_user_display_name(user.display_name, user.email),
                "detail": user.email,
                "created_at_text": format_admin_timestamp(user.created_at),
            }
            for user in users
        ],
        "venues": [
            {
                "name": venue.name,
                "detail": "Primary venue" if venue.is_core else "Other venue",
                "created_at_text": format_admin_timestamp(venue.created_at),
            }
            for venue in venues
        ],
        "items": [
            {
                "name": item.name,
                "detail": "Family organizer" if item.is_group_parent else ("Asset" if item.is_singleton_asset else "Quantity item"),
                "created_at_text": format_admin_timestamp(item.created_at),
            }
            for item in items
        ],
    }


def _build_inventory_change_rows(limit=RECENT_HISTORY_LIMIT):
    status_actor_name = _activity_actor_name_expr(User.display_name, User.email)
    previous_status = func.lag(CheckLine.status).over(
        partition_by=(Check.venue_id, CheckLine.item_id),
        order_by=(Check.created_at.asc(), Check.id.asc()),
    )
    status_inner = (
        select(
            literal("status").label("type_key"),
            Check.id.label("event_id"),
            Check.created_at.label("changed_at"),
            Venue.name.label("venue_name"),
            Item.name.label("item_name"),
            status_actor_name.label("actor_name"),
            previous_status.label("old_status_key"),
            CheckLine.status.label("new_status_key"),
            cast(literal(None), Integer).label("old_raw_count"),
            cast(literal(None), Integer).label("new_raw_count"),
        )
        .select_from(Check)
        .join(Venue, Venue.id == Check.venue_id)
        .join(CheckLine, CheckLine.check_id == Check.id)
        .join(Item, Item.id == CheckLine.item_id)
        .outerjoin(User, User.id == Check.user_id)
        .subquery()
    )
    status_events = select(status_inner).where(
        or_(
            and_(
                status_inner.c.old_status_key.is_(None),
                status_inner.c.new_status_key != "not_checked",
            ),
            status_inner.c.old_status_key != status_inner.c.new_status_key,
        )
    )

    count_actor_name = _activity_actor_name_expr(User.display_name, User.email)
    previous_raw_count = func.lag(CountLine.raw_count).over(
        partition_by=(CountSession.venue_id, CountLine.item_id),
        order_by=(CountSession.created_at.asc(), CountSession.id.asc()),
    )
    count_inner = (
        select(
            literal("raw_count").label("type_key"),
            CountSession.id.label("event_id"),
            CountSession.created_at.label("changed_at"),
            Venue.name.label("venue_name"),
            Item.name.label("item_name"),
            count_actor_name.label("actor_name"),
            cast(literal(None), String).label("old_status_key"),
            cast(literal(None), String).label("new_status_key"),
            previous_raw_count.label("old_raw_count"),
            CountLine.raw_count.label("new_raw_count"),
        )
        .select_from(CountSession)
        .join(Venue, Venue.id == CountSession.venue_id)
        .join(CountLine, CountLine.count_session_id == CountSession.id)
        .join(Item, Item.id == CountLine.item_id)
        .outerjoin(User, User.id == CountSession.user_id)
        .subquery()
    )
    count_events = select(count_inner).where(
        or_(
            count_inner.c.old_raw_count.is_(None),
            count_inner.c.old_raw_count != count_inner.c.new_raw_count,
        )
    )

    activity_events = union_all(status_events, count_events).subquery()
    rows = db.session.execute(
        select(activity_events)
        .order_by(activity_events.c.changed_at.desc(), activity_events.c.event_id.desc())
        .limit(limit)
    ).mappings()
    return [_serialize_inventory_change_row(row) for row in rows]


def _activity_actor_name_expr(display_name_col, email_col):
    trimmed_display_name = func.nullif(func.trim(display_name_col), "")
    trimmed_email = func.nullif(func.trim(email_col), "")
    return func.coalesce(trimmed_display_name, func.lower(trimmed_email), literal("Unknown user"))


def _serialize_inventory_change_row(row):
    type_key = row["type_key"]
    changed_at = row["changed_at"]
    actor_name = row["actor_name"] or "Unknown user"
    if type_key == "status":
        old_status_key = normalize_status(row["old_status_key"]) if row["old_status_key"] else None
        new_status_key = normalize_status(row["new_status_key"])
        old_value_text = "No prior status" if old_status_key is None else _status_text(old_status_key)
        new_value_text = _status_text(new_status_key)
        detail_text = (
            f"Initial status recorded as {new_value_text}"
            if old_status_key is None
            else f"Status changed from {old_value_text} to {new_value_text}"
        )
    else:
        old_raw_count = row["old_raw_count"]
        new_raw_count = row["new_raw_count"]
        old_value_text = "No prior count" if old_raw_count is None else str(old_raw_count)
        new_value_text = str(new_raw_count)
        detail_text = (
            f"Initial count recorded as {new_value_text}"
            if old_raw_count is None
            else f"Count changed from {old_value_text} to {new_value_text}"
        )

    return {
        "type_key": type_key,
        "type_label": CHANGE_TYPE_META[type_key]["label"],
        "icon_class": CHANGE_TYPE_META[type_key]["icon_class"],
        "badge_class": CHANGE_TYPE_META[type_key]["badge_class"],
        "venue_name": row["venue_name"],
        "item_name": row["item_name"],
        "actor_name": actor_name,
        "old_value_text": old_value_text,
        "new_value_text": new_value_text,
        "detail_text": detail_text,
        "changed_at": ensure_utc(changed_at),
        "changed_at_text": format_admin_timestamp(changed_at),
    }


def _status_text(status_key):
    mapping = {
        "good": "Good",
        "ok": "OK",
        "low": "Low",
        "out": "Out",
        "not_checked": "Not Checked",
    }
    return mapping.get(normalize_status(status_key), "Not Checked")
