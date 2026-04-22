from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin
from flask import current_app, url_for
from sqlalchemy.orm import Query
from werkzeug.security import generate_password_hash

from app import db
from app.models import (
    AccountAuditEvent,
    PASSWORD_ACTION_PURPOSES,
    PasswordActionToken,
    User,
    VALID_ROLES,
    normalize_role,
)
from app.services.inventory_status import ensure_utc

PASSWORD_SETUP_PURPOSE = "password_setup"
PASSWORD_RESET_PURPOSE = "password_reset"

ACCOUNT_EVENT_META = {
    "user_created": {"label": "User Created", "icon_class": "bi-person-plus"},
    "user_updated": {"label": "User Updated", "icon_class": "bi-person-gear"},
    "role_changed": {"label": "Role Changed", "icon_class": "bi-person-badge"},
    "user_deactivated": {"label": "User Deactivated", "icon_class": "bi-person-dash"},
    "user_reactivated": {"label": "User Reactivated", "icon_class": "bi-person-check"},
    "user_unlocked": {"label": "User Unlocked", "icon_class": "bi-unlock"},
    "password_setup_initiated": {"label": "Password Setup", "icon_class": "bi-key"},
    "password_reset_initiated": {"label": "Password Reset", "icon_class": "bi-arrow-clockwise"},
    "password_reset_completed": {"label": "Password Updated", "icon_class": "bi-check2-circle"},
    "password_changed": {"label": "Password Changed", "icon_class": "bi-shield-check"},
}


class AccountManagementError(ValueError):
    """Raised when account lifecycle actions fail validation or safety checks."""


@dataclass(frozen=True)
class IssuedPasswordLink:
    purpose: str
    raw_token: str
    url: str
    expires_at: datetime


def utcnow():
    return datetime.now(timezone.utc)


def normalize_email(email):
    return (email or "").strip().lower()


def normalize_display_name(display_name):
    cleaned = (display_name or "").strip()
    return cleaned or None


def normalize_role_value(role):
    normalized = (role or "").strip().lower()
    if normalized not in VALID_ROLES:
        raise AccountManagementError("Role selection is invalid.")
    return normalized


def role_label(role):
    normalized = normalize_role(role)
    return {
        "admin": "Admin",
        "staff": "Staff",
        "viewer": "Viewer",
    }.get(normalized, "Viewer")


def validate_email(email):
    normalized = normalize_email(email)
    if not normalized:
        raise AccountManagementError("Email is required.")
    if len(normalized) > 255:
        raise AccountManagementError("Email must be 255 characters or fewer.")
    local_part, at_symbol, domain = normalized.partition("@")
    if not at_symbol or not local_part or not domain or "." not in domain:
        raise AccountManagementError("Enter a valid email address.")
    return normalized


def validate_display_name(display_name):
    normalized = normalize_display_name(display_name)
    if normalized and len(normalized) > 120:
        raise AccountManagementError("Display name must be 120 characters or fewer.")
    return normalized


def validate_new_password(new_password, confirm_password=None):
    password = new_password or ""
    if not password:
        raise AccountManagementError("Password is required.")
    if confirm_password is not None and password != (confirm_password or ""):
        raise AccountManagementError("Password and confirmation do not match.")
    min_length = current_app.config["AUTH_PASSWORD_MIN_LENGTH"]
    if len(password) < min_length:
        raise AccountManagementError(f"Password must be at least {min_length} characters.")
    return password


def make_unusable_password_hash():
    return generate_password_hash(secrets.token_urlsafe(48))


def rotate_user_session(user):
    user.session_version = int(getattr(user, "session_version", None) or 0) + 1


def format_account_timestamp(value, missing_text="No recorded time"):
    normalized = ensure_utc(value)
    if normalized is None:
        return missing_text
    return normalized.strftime("%Y-%m-%d %I:%M %p")


def _coerce_datetime(value):
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


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


def log_account_event(event_type, *, actor=None, target=None, target_email=None, details=None):
    event = AccountAuditEvent(
        event_type=event_type,
        actor_user_id=getattr(actor, "id", None),
        target_user_id=getattr(target, "id", None),
        target_email=target_email or getattr(target, "email", None),
        details_json=serialize_event_details(details),
    )
    db.session.add(event)
    return event


def describe_account_event(event, *, actor_name=None, target_name=None):
    meta = ACCOUNT_EVENT_META.get(
        event.event_type,
        {"label": "Account Event", "icon_class": "bi-clock-history"},
    )
    details = deserialize_event_details(event.details_json)
    target_email = event.target_email or details.get("target_email") or target_name or "Unknown account"

    if event.event_type == "user_created":
        title = "Created user account"
        detail = f"{target_email} | Role {role_label(details.get('role'))}"
    elif event.event_type == "user_updated":
        title = "Updated user profile"
        changed_fields = details.get("changed_fields") or []
        if changed_fields:
            detail = f"{target_email} | Changed {', '.join(changed_fields)}"
        else:
            detail = target_email
    elif event.event_type == "role_changed":
        title = "Changed user role"
        previous_role = role_label(details.get("previous_role"))
        new_role = role_label(details.get("new_role"))
        detail = f"{target_email} | {previous_role} -> {new_role}"
    elif event.event_type == "user_deactivated":
        title = "Deactivated user account"
        detail = target_email
    elif event.event_type == "user_reactivated":
        title = "Reactivated user account"
        detail = target_email
    elif event.event_type == "user_unlocked":
        title = "Unlocked user account"
        detail = f"{target_email} | Cleared lockout state"
    elif event.event_type == "password_setup_initiated":
        title = "Issued password setup link"
        detail = f"{target_email} | Expires {format_account_timestamp(_coerce_datetime(details.get('expires_at')))}"
    elif event.event_type == "password_reset_initiated":
        title = "Issued password reset link"
        detail = f"{target_email} | Expires {format_account_timestamp(_coerce_datetime(details.get('expires_at')))}"
    elif event.event_type == "password_reset_completed":
        title = "Completed password update"
        detail = f"{target_email} | Via {'setup' if details.get('purpose') == PASSWORD_SETUP_PURPOSE else 'reset'} flow"
    elif event.event_type == "password_changed":
        title = "Changed account password"
        detail = target_email
    else:
        title = meta["label"]
        detail = target_email

    return {
        "title": title,
        "detail": detail,
        "kind_label": meta["label"],
        "icon_class": meta["icon_class"],
        "actor_name": actor_name or "System",
        "target_name": target_name or target_email,
        "changed_at": ensure_utc(event.created_at),
        "changed_at_text": format_account_timestamp(event.created_at),
    }


def create_managed_user(*, actor, email, display_name, role):
    try:
        normalized_email = validate_email(email)
        normalized_display_name = validate_display_name(display_name)
        normalized_role = normalize_role_value(role)

        existing = User.query.filter_by(email=normalized_email).first()
        if existing is not None:
            raise AccountManagementError("A user with that email already exists.")

        user = User(
            email=normalized_email,
            display_name=normalized_display_name,
            password_hash=make_unusable_password_hash(),
            role=normalized_role,
            active=True,
            force_password_change=True,
            created_by_user_id=getattr(actor, "id", None),
        )
        db.session.add(user)
        db.session.flush()

        log_account_event(
            "user_created",
            actor=actor,
            target=user,
            details={"role": normalized_role},
        )
        issued_link = _issue_password_link(
            actor=actor,
            user=user,
            purpose=PASSWORD_SETUP_PURPOSE,
        )
        db.session.commit()
        return user, issued_link
    except Exception:
        db.session.rollback()
        raise


def update_managed_user(*, actor, user_id, display_name, role):
    try:
        user = _query_user_for_update(user_id).first()
        if user is None:
            raise AccountManagementError("User not found.")

        normalized_display_name = validate_display_name(display_name)
        normalized_role = normalize_role_value(role)
        _enforce_user_admin_safeguards(
            actor=actor,
            target=user,
            new_role=normalized_role,
            new_active=user.active,
        )

        changed_fields = []
        if user.display_name != normalized_display_name:
            user.display_name = normalized_display_name
            changed_fields.append("display name")

        previous_role = user.role
        if user.role != normalized_role:
            user.role = normalized_role
            rotate_user_session(user)
            changed_fields.append("role")
            log_account_event(
                "role_changed",
                actor=actor,
                target=user,
                details={
                    "previous_role": previous_role,
                    "new_role": normalized_role,
                },
            )

        if changed_fields and changed_fields != ["role"]:
            log_account_event(
                "user_updated",
                actor=actor,
                target=user,
                details={"changed_fields": [field for field in changed_fields if field != "role"]},
            )

        db.session.commit()
        return user, changed_fields
    except Exception:
        db.session.rollback()
        raise


def set_user_active_state(*, actor, user_id, should_be_active):
    try:
        user = _query_user_for_update(user_id).first()
        if user is None:
            raise AccountManagementError("User not found.")

        desired_state = bool(should_be_active)
        _enforce_user_admin_safeguards(
            actor=actor,
            target=user,
            new_role=user.role,
            new_active=desired_state,
        )

        if user.active == desired_state:
            return user, False

        if desired_state:
            user.active = True
            user.deactivated_at = None
            user.deactivated_by_user_id = None
            rotate_user_session(user)
            log_account_event("user_reactivated", actor=actor, target=user)
        else:
            deactivated_at = utcnow()
            user.active = False
            user.deactivated_at = deactivated_at
            user.deactivated_by_user_id = getattr(actor, "id", None)
            rotate_user_session(user)
            _invalidate_open_password_tokens(user.id, consumed_at=deactivated_at)
            log_account_event("user_deactivated", actor=actor, target=user)

        db.session.commit()
        return user, True
    except Exception:
        db.session.rollback()
        raise


def unlock_user_account(*, actor, user_id):
    try:
        user = _query_user_for_update(user_id).first()
        if user is None:
            raise AccountManagementError("User not found.")

        if not user.failed_login_attempts and user.locked_until is None:
            return user, False

        user.failed_login_attempts = 0
        user.locked_until = None
        log_account_event("user_unlocked", actor=actor, target=user)
        db.session.commit()
        return user, True
    except Exception:
        db.session.rollback()
        raise


def issue_admin_password_link(*, actor, user_id):
    try:
        user = _query_user_for_update(user_id).first()
        if user is None:
            raise AccountManagementError("User not found.")

        purpose = PASSWORD_SETUP_PURPOSE if user.force_password_change else PASSWORD_RESET_PURPOSE
        issued_link = _issue_password_link(actor=actor, user=user, purpose=purpose)
        db.session.commit()
        return user, issued_link
    except Exception:
        db.session.rollback()
        raise


def request_password_link_for_email(email):
    try:
        normalized_email = validate_email(email)
        user = User.query.filter_by(email=normalized_email).first()
        if user is None or not user.active:
            return None, None

        purpose = PASSWORD_SETUP_PURPOSE if user.force_password_change else PASSWORD_RESET_PURPOSE
        issued_link = _issue_password_link(actor=None, user=user, purpose=purpose)
        db.session.commit()
        return user, issued_link
    except AccountManagementError:
        db.session.rollback()
        return None, None
    except Exception:
        db.session.rollback()
        raise


def complete_password_action(*, token, new_password, confirm_password):
    try:
        password = validate_new_password(new_password, confirm_password)
        token_record = resolve_password_action_token(token)
        user = token_record.user
        now = utcnow()

        user.password_hash = generate_password_hash(password)
        user.password_changed_at = now
        user.force_password_change = False
        rotate_user_session(user)
        user.failed_login_attempts = 0
        user.locked_until = None
        token_record.consumed_at = now
        _invalidate_open_password_tokens(user.id, consumed_at=now, skip_token_id=token_record.id)
        log_account_event(
            "password_reset_completed",
            actor=user,
            target=user,
            details={"purpose": token_record.purpose},
        )
        db.session.commit()
        return user, token_record.purpose
    except Exception:
        db.session.rollback()
        raise


def change_password_for_user(*, user, new_password):
    try:
        password = validate_new_password(new_password)
        now = utcnow()

        user.password_hash = generate_password_hash(password)
        user.password_changed_at = now
        user.force_password_change = False
        rotate_user_session(user)
        user.failed_login_attempts = 0
        user.locked_until = None
        _invalidate_open_password_tokens(user.id, consumed_at=now)
        log_account_event("password_changed", actor=user, target=user)
        db.session.commit()
        return user
    except Exception:
        db.session.rollback()
        raise


def register_successful_login(user):
    user.last_login_at = utcnow()


def resolve_password_action_token(token):
    token_hash = _hash_password_token(token)
    token_record = (
        PasswordActionToken.query.filter_by(token_hash=token_hash)
        .first()
    )
    if token_record is None:
        raise AccountManagementError("This password link is invalid.")
    if token_record.consumed_at is not None:
        raise AccountManagementError("This password link has already been used.")
    if ensure_utc(token_record.expires_at) <= utcnow():
        raise AccountManagementError("This password link has expired.")
    if token_record.purpose not in PASSWORD_ACTION_PURPOSES:
        raise AccountManagementError("This password link is invalid.")
    if token_record.user is None or not token_record.user.active:
        raise AccountManagementError("This password link is no longer valid.")
    return token_record


def build_dev_password_link_message(user, issued_link):
    action_label = "Setup" if issued_link.purpose == PASSWORD_SETUP_PURPOSE else "Reset"
    return f"{action_label} link for {user.email} (dev only): {issued_link.url}"


def build_password_action_url(raw_token):
    relative_path = url_for("auth.reset_password", token=raw_token)
    base_url = (current_app.config.get("APP_BASE_URL") or "").strip()
    if base_url:
        return urljoin(f"{base_url.rstrip('/')}/", relative_path.lstrip("/"))
    return url_for("auth.reset_password", token=raw_token, _external=True)


def _issue_password_link(*, actor, user, purpose):
    now = utcnow()
    expires_at = now + timedelta(hours=current_app.config["AUTH_PASSWORD_TOKEN_TTL_HOURS"])
    _invalidate_open_password_tokens(user.id, consumed_at=now)

    raw_token = secrets.token_urlsafe(32)
    token_record = PasswordActionToken(
        user_id=user.id,
        created_by_user_id=getattr(actor, "id", None),
        purpose=purpose,
        token_hash=_hash_password_token(raw_token),
        expires_at=expires_at,
    )
    db.session.add(token_record)

    event_type = (
        "password_setup_initiated"
        if purpose == PASSWORD_SETUP_PURPOSE
        else "password_reset_initiated"
    )
    log_account_event(
        event_type,
        actor=actor,
        target=user,
        details={
            "expires_at": expires_at.isoformat(),
            "purpose": purpose,
        },
    )

    issued_link = IssuedPasswordLink(
        purpose=purpose,
        raw_token=raw_token,
        url=build_password_action_url(raw_token),
        expires_at=expires_at,
    )
    current_app.logger.info(
        "%s password link issued for %s: %s",
        "Setup" if purpose == PASSWORD_SETUP_PURPOSE else "Reset",
        user.email,
        issued_link.url,
    )
    return issued_link


def _invalidate_open_password_tokens(user_id, *, consumed_at, skip_token_id=None):
    query = PasswordActionToken.query.filter(
        PasswordActionToken.user_id == user_id,
        PasswordActionToken.consumed_at.is_(None),
    )
    if skip_token_id is not None:
        query = query.filter(PasswordActionToken.id != skip_token_id)
    query.update({"consumed_at": consumed_at}, synchronize_session=False)


def _query_user_for_update(user_id) -> Query:
    query = User.query.filter(User.id == user_id)
    if db.session.get_bind().dialect.name != "sqlite":
        query = query.with_for_update()
    return query


def _active_admin_ids():
    query = User.query.filter(User.role == "admin", User.active == True).order_by(User.id.asc())
    if db.session.get_bind().dialect.name != "sqlite":
        query = query.with_for_update()
    return [user.id for user in query.all()]


def _enforce_user_admin_safeguards(*, actor, target, new_role, new_active):
    if actor and actor.id == target.id and not new_active:
        raise AccountManagementError("You cannot deactivate your own account.")
    if actor and actor.id == target.id and target.role == "admin" and new_role != "admin":
        raise AccountManagementError("You cannot remove your own admin role.")

    target_will_be_admin = new_role == "admin"
    target_will_be_active = bool(new_active)
    if target.role == "admin" and target.active:
        active_admin_ids = _active_admin_ids()
        if target.id in active_admin_ids and (not target_will_be_admin or not target_will_be_active) and len(active_admin_ids) <= 1:
            raise AccountManagementError("At least one active admin account must remain.")


def _hash_password_token(raw_token):
    return hashlib.sha256((raw_token or "").encode("utf-8")).hexdigest()
