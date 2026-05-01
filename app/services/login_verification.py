from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import timedelta
from ipaddress import ip_address

from flask import current_app, request

from app import db
from app.models import LoginVerificationChallenge, TrustedDevice
from app.services.account_security import log_account_event, utcnow
from app.services.inventory_status import ensure_utc

LOGIN_VERIFICATION_PURPOSE = "login_verification"
VERIFICATION_CODE_LENGTH = 6


@dataclass(frozen=True)
class VerificationDecision:
    requires_verification: bool
    reasons: tuple[str, ...]
    trusted_device_id: int | None = None


def get_request_country():
    raw_country = (request.headers.get("CF-IPCountry") or "").strip().upper()
    if not raw_country or len(raw_country) > 8:
        return None
    return raw_country


def get_request_user_agent():
    return (request.headers.get("User-Agent") or "").strip()[:512] or "unknown"


def get_trusted_device_cookie_name():
    return current_app.config["TRUSTED_DEVICE_COOKIE_NAME"]


def trusted_device_days_for_user(user):
    role = (getattr(user, "role", "viewer") or "viewer").strip().lower()
    if role == "admin":
        return int(current_app.config["TRUSTED_DEVICE_DAYS_ADMIN"])
    if role == "staff":
        return int(current_app.config["TRUSTED_DEVICE_DAYS_STAFF"])
    return int(current_app.config["TRUSTED_DEVICE_DAYS_VIEWER"])


def trusted_device_max_age_seconds(user):
    return trusted_device_days_for_user(user) * 24 * 60 * 60


def hash_verification_code(code):
    secret = current_app.config.get("SECRET_KEY") or ""
    return hashlib.sha256(f"login-code:{code}:{secret}".encode("utf-8")).hexdigest()


def hash_trusted_token(raw_token):
    return hashlib.sha256((raw_token or "").encode("utf-8")).hexdigest()


def hash_user_agent(user_agent):
    return hashlib.sha256((user_agent or "").strip().lower().encode("utf-8")).hexdigest()


def summarize_user_agent(user_agent):
    cleaned = " ".join((user_agent or "").split())
    return cleaned[:255] if cleaned else "Unknown device"


def _network_prefix(ip_text):
    try:
        parsed = ip_address((ip_text or "").strip())
    except ValueError:
        return None
    packed = parsed.packed
    if parsed.version == 4:
        return packed[:3].hex()
    return packed[:6].hex()


def _is_significantly_different_ip(previous_ip, current_ip):
    if not previous_ip or not current_ip:
        return False
    return _network_prefix(previous_ip) != _network_prefix(current_ip)


def _active_trusted_device_for_user(user, raw_cookie_token):
    if not raw_cookie_token:
        return None
    now = utcnow()
    token_hash = hash_trusted_token(raw_cookie_token)
    trusted_device = TrustedDevice.query.filter_by(
        user_id=user.id,
        token_hash=token_hash,
    ).first()
    if trusted_device is None:
        return None
    if trusted_device.revoked_at is not None:
        return None
    if ensure_utc(trusted_device.expires_at) <= ensure_utc(now):
        return None
    return trusted_device


def evaluate_login_verification_need(
    *,
    user,
    ip_address_text,
    user_agent,
    request_country,
    had_recent_failures,
    raw_cookie_token,
):
    if not current_app.config.get("LOGIN_EMAIL_2FA_ENABLED"):
        return VerificationDecision(requires_verification=False, reasons=tuple())

    reasons = []
    now = ensure_utc(utcnow())
    trusted_device = _active_trusted_device_for_user(user, raw_cookie_token)
    user_agent_hash = hash_user_agent(user_agent)

    if trusted_device is None:
        reasons.append("unknown_device")
        if user.role == "admin":
            reasons.append("admin_untrusted_device")
    else:
        trusted_last_seen = ensure_utc(trusted_device.last_seen_at or trusted_device.created_at)
        stale_threshold = now - timedelta(days=trusted_device_days_for_user(user))
        if trusted_last_seen <= stale_threshold:
            reasons.append("stale_trusted_device")
        if trusted_device.user_agent_hash != user_agent_hash:
            reasons.append("user_agent_changed")
        if _is_significantly_different_ip(trusted_device.last_ip, ip_address_text):
            reasons.append("network_changed")
        if (
            trusted_device.last_country
            and request_country
            and trusted_device.last_country != request_country
        ):
            reasons.append("country_changed")

    if had_recent_failures:
        reasons.append("recent_failed_attempts")
    if getattr(user, "require_login_verification", False):
        reasons.append("post_security_event")

    reason_codes = tuple(dict.fromkeys(reasons))
    return VerificationDecision(
        requires_verification=bool(reason_codes),
        reasons=reason_codes,
        trusted_device_id=getattr(trusted_device, "id", None),
    )


def create_login_verification_challenge(
    *,
    user,
    next_url,
    ip_address_text,
    user_agent,
    request_country,
    reason_codes,
    event_type="login_verification_required",
):
    now = utcnow()
    code = f"{secrets.randbelow(1_000_000):06d}"
    expires_at = now + timedelta(minutes=int(current_app.config["LOGIN_2FA_CODE_TTL_MINUTES"]))
    LoginVerificationChallenge.query.filter(
        LoginVerificationChallenge.user_id == user.id,
        LoginVerificationChallenge.purpose == LOGIN_VERIFICATION_PURPOSE,
        LoginVerificationChallenge.consumed_at.is_(None),
    ).update({"consumed_at": now}, synchronize_session=False)
    challenge = LoginVerificationChallenge(
        user_id=user.id,
        purpose=LOGIN_VERIFICATION_PURPOSE,
        code_hash=hash_verification_code(code),
        expires_at=expires_at,
        sent_to_email=user.email,
        request_ip=ip_address_text,
        user_agent=user_agent,
        request_country=request_country,
        next_url=next_url,
        reason_codes=json.dumps(list(reason_codes or [])),
        last_sent_at=now,
    )
    db.session.add(challenge)
    db.session.flush()
    log_account_event(
        event_type,
        actor=user,
        target=user,
        details={"reasons": list(reason_codes or []), "challenge_id": challenge.id},
    )
    return challenge, code


def verify_login_challenge_code(*, user, challenge_id, submitted_code):
    now = ensure_utc(utcnow())
    challenge = LoginVerificationChallenge.query.filter_by(
        id=challenge_id,
        user_id=user.id,
        purpose=LOGIN_VERIFICATION_PURPOSE,
    ).first()
    if challenge is None:
        return False, "invalid"
    if challenge.consumed_at is not None:
        return False, "used"
    if ensure_utc(challenge.expires_at) <= now:
        return False, "expired"

    max_attempts = int(current_app.config["LOGIN_2FA_MAX_ATTEMPTS"])
    if int(challenge.failed_attempts or 0) >= max_attempts:
        return False, "attempt_limit"

    expected_hash = challenge.code_hash
    submitted_hash = hash_verification_code((submitted_code or "").strip())
    if not hmac.compare_digest(expected_hash, submitted_hash):
        challenge.failed_attempts = int(challenge.failed_attempts or 0) + 1
        if challenge.failed_attempts >= max_attempts:
            challenge.consumed_at = utcnow()
        log_account_event(
            "login_verification_failed",
            actor=user,
            target=user,
            details={"challenge_id": challenge.id, "reason": "invalid_code"},
        )
        return False, "invalid"

    challenge.consumed_at = utcnow()
    user.last_login_verification_at = utcnow()
    user.require_login_verification = False
    log_account_event(
        "login_verification_succeeded",
        actor=user,
        target=user,
        details={"challenge_id": challenge.id},
    )
    return True, "ok"


def resend_allowed_for_challenge(challenge):
    if challenge is None:
        return False, 0
    now = ensure_utc(utcnow())
    last_sent_at = ensure_utc(challenge.last_sent_at or challenge.created_at)
    cooldown = int(current_app.config["LOGIN_2FA_RESEND_COOLDOWN_SECONDS"])
    retry_at = last_sent_at + timedelta(seconds=cooldown)
    if now >= retry_at:
        return True, 0
    return False, int((retry_at - now).total_seconds())


def issue_trusted_device(*, user, user_agent, ip_address_text, request_country):
    now = utcnow()
    raw_token = secrets.token_urlsafe(48)
    expires_at = now + timedelta(days=trusted_device_days_for_user(user))
    record = TrustedDevice(
        user_id=user.id,
        token_hash=hash_trusted_token(raw_token),
        user_agent_hash=hash_user_agent(user_agent),
        user_agent_summary=summarize_user_agent(user_agent),
        last_ip=ip_address_text,
        last_country=request_country,
        expires_at=expires_at,
        last_seen_at=now,
    )
    db.session.add(record)
    db.session.flush()
    log_account_event(
        "trusted_device_created",
        actor=user,
        target=user,
        details={"trusted_device_id": record.id, "expires_at": ensure_utc(expires_at).isoformat()},
    )
    return record, raw_token


def touch_trusted_device_if_present(*, user, raw_cookie_token, ip_address_text, request_country):
    trusted_device = _active_trusted_device_for_user(user, raw_cookie_token)
    if trusted_device is None:
        return None
    trusted_device.last_seen_at = utcnow()
    trusted_device.last_ip = ip_address_text
    trusted_device.last_country = request_country
    return trusted_device


def revoke_all_trusted_devices_for_user(*, actor, user):
    now = utcnow()
    active_devices = TrustedDevice.query.filter(
        TrustedDevice.user_id == user.id,
        TrustedDevice.revoked_at.is_(None),
        TrustedDevice.expires_at > now,
    ).all()
    for device in active_devices:
        device.revoked_at = now
    if active_devices:
        log_account_event(
            "trusted_device_revoked",
            actor=actor,
            target=user,
            details={"scope": "all", "count": len(active_devices)},
        )
    return len(active_devices)
