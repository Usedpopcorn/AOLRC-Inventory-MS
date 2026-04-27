import hashlib
from datetime import datetime, timedelta
from pathlib import Path
import secrets

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

from app import ACTIVE_UI_THEME_SESSION_KEY, AUTH_SESSION_VERSION_SESSION_KEY, db
from app.models import (
    User,
    VALID_THEME_PREFERENCES,
    normalize_theme_preference,
)
from app.security import get_client_ip, is_safe_redirect_target
from app.services.account_security import (
    AccountManagementError,
    build_dev_password_link_message,
    change_password_for_user,
    complete_password_action,
    register_successful_login,
    request_password_link_for_email,
    resolve_password_action_token,
    utcnow,
    validate_new_password,
)
from app.services.feedback import FEEDBACK_REVIEW_SESSION_KEY
from app.services.inventory_status import ensure_utc
from app.services.rate_limits import rate_limiter

auth_bp = Blueprint("auth", __name__)
DEV_QUICK_LOGIN_EMAILS = {
    "admin": "admin@example.com",
    "staff": "staff@example.com",
    "user": "viewer@example.com",
}
DEV_QUICK_LOGIN_DEFAULT_PASSWORD = "local-test-password"
DEV_QUICK_LOGIN_ROLE_MAP = {
    "admin": "admin",
    "staff": "staff",
    "user": "viewer",
}
GENERIC_LOGIN_FAILURE_MESSAGE = "Invalid credentials or account unavailable."
AUTH_THROTTLE_MESSAGE = "Too many attempts. Please wait a few minutes and try again."
ACCOUNT_THEME_OPTIONS = (
    {
        "description": "Keep the existing AOLRC purple primary with the shared gold accent.",
        "label": "Purple",
        "value": "purple",
    },
    {
        "description": "Swap in the warm blue primary while keeping the shared gold accent.",
        "label": "Blue",
        "value": "blue",
    },
)


def _is_dev_quick_login_enabled():
    return bool(current_app.config.get("AUTH_ALLOW_DEV_QUICK_LOGIN"))


def _build_account_initials(user):
    source = (user.display_name or user.email or "").strip()
    if not source:
        return "U"
    parts = [p for p in source.replace("@", " ").replace(".", " ").split() if p]
    if not parts:
        return source[:1].upper()
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[1][0]).upper()


def _avatar_upload_dir():
    upload_dir = current_app.config.get("AVATAR_UPLOAD_DIR")
    if upload_dir:
        return Path(upload_dir)
    return Path(current_app.static_folder) / "uploads" / "avatars"


def _avatar_web_prefix():
    return (current_app.config.get("AVATAR_WEB_PREFIX") or "uploads/avatars").strip("/")


def _avatar_url_for_user(user):
    filename = (user.avatar_filename or "").strip()
    if not filename:
        return None
    return url_for("static", filename=f"{_avatar_web_prefix()}/{filename}")


def _is_allowed_avatar_file(filename):
    if "." not in filename:
        return False
    ext = filename.rsplit(".", 1)[1].lower()
    return ext in {"jpg", "jpeg", "png", "webp"}


def _build_account_activity(user_id, full_history=False, page=1):
    from app.routes.main import build_activity_page

    if full_history:
        return build_activity_page(actor_user_id=user_id, page=page)
    return build_activity_page(actor_user_id=user_id, page=1, page_size=6)


def _is_user_locked(user, now=None):
    reference_time = ensure_utc(now or utcnow())
    locked_until = ensure_utc(user.locked_until)
    return bool(reference_time and locked_until and locked_until > reference_time)


def _resolve_quick_login_user(quick_role):
    role_value = DEV_QUICK_LOGIN_ROLE_MAP.get(quick_role)
    if role_value:
        candidates = (
            User.query.filter_by(role=role_value, active=True)
            .order_by(User.id.asc())
            .all()
        )
        for user in candidates:
            if not user.force_password_change and not _is_user_locked(user):
                return user

    fallback_email = DEV_QUICK_LOGIN_EMAILS.get(quick_role)
    if fallback_email:
        existing = User.query.filter_by(email=fallback_email).first()
        if existing:
            if (
                existing.active
                and not existing.force_password_change
                and not _is_user_locked(existing)
            ):
                return existing
            return None
        user = User(
            email=fallback_email,
            display_name=f"{role_value.title()} User" if role_value else "Viewer User",
            password_hash=generate_password_hash(DEV_QUICK_LOGIN_DEFAULT_PASSWORD),
            role=role_value or "viewer",
            active=True,
            force_password_change=False,
            password_changed_at=utcnow(),
        )
        db.session.add(user)
        db.session.commit()
        return user
    return None


def _record_failed_login(user):
    max_attempts = current_app.config["AUTH_MAX_FAILED_LOGIN_ATTEMPTS"]
    lockout_minutes = current_app.config["AUTH_LOCKOUT_MINUTES"]

    user.failed_login_attempts = (user.failed_login_attempts or 0) + 1
    if user.failed_login_attempts >= max_attempts:
        user.locked_until = utcnow() + timedelta(minutes=lockout_minutes)
        user.failed_login_attempts = 0
        _log_auth_security_event(
            "login_lockout",
            email=user.email,
            user_id=user.id,
            details={"locked_until": ensure_utc(user.locked_until).isoformat()},
        )
    db.session.commit()


def _finish_login(user):
    login_user(user)
    session[AUTH_SESSION_VERSION_SESSION_KEY] = int(user.session_version or 1)
    session[ACTIVE_UI_THEME_SESSION_KEY] = normalize_theme_preference(
        getattr(user, "theme_preference", None)
    )


def _reset_login_failure_state(user):
    user.failed_login_attempts = 0
    user.locked_until = None


def _render_login(email="", status_code=200):
    return (
        render_template(
            "auth/login.html",
            email=email,
            quick_login_enabled=_is_dev_quick_login_enabled(),
        ),
        status_code,
    )


def _log_auth_security_event(
    event_type,
    *,
    email=None,
    user_id=None,
    ip_address=None,
    details=None,
):
    metadata = details or {}
    current_app.logger.warning(
        "auth_security event=%s email=%s user_id=%s ip=%s details=%s",
        event_type,
        (email or "").strip().lower() or "-",
        user_id if user_id is not None else "-",
        ip_address or get_client_ip(),
        metadata,
    )


def _throttle_key(value, *, fallback):
    normalized = (value or "").strip().lower()
    return normalized or fallback


def _peek_rate_limit(bucket, key, *, limit, window_seconds):
    return rate_limiter.peek(bucket, key, limit=limit, window_seconds=window_seconds)


def _record_rate_limit(bucket, key, *, limit, window_seconds):
    return rate_limiter.record(bucket, key, limit=limit, window_seconds=window_seconds)


def _clear_rate_limit(bucket, key):
    rate_limiter.clear(bucket, key)


def _peek_login_throttle(email, ip_address):
    config = current_app.config
    email_key = _throttle_key(email, fallback="blank-email")
    ip_key = _throttle_key(ip_address, fallback="unknown-ip")
    decisions = [
        _peek_rate_limit(
            "login-account",
            email_key,
            limit=config["AUTH_LOGIN_ACCOUNT_THROTTLE_LIMIT"],
            window_seconds=config["AUTH_LOGIN_THROTTLE_WINDOW_SECONDS"],
        ),
        _peek_rate_limit(
            "login-ip",
            ip_key,
            limit=config["AUTH_LOGIN_IP_THROTTLE_LIMIT"],
            window_seconds=config["AUTH_LOGIN_THROTTLE_WINDOW_SECONDS"],
        ),
    ]
    limited_decisions = [decision for decision in decisions if decision.limited]
    if not limited_decisions:
        return None
    return max(limited_decisions, key=lambda decision: decision.retry_after_seconds)


def _record_login_failure(email, ip_address):
    config = current_app.config
    email_key = _throttle_key(email, fallback="blank-email")
    ip_key = _throttle_key(ip_address, fallback="unknown-ip")
    decisions = [
        _record_rate_limit(
            "login-account",
            email_key,
            limit=config["AUTH_LOGIN_ACCOUNT_THROTTLE_LIMIT"],
            window_seconds=config["AUTH_LOGIN_THROTTLE_WINDOW_SECONDS"],
        ),
        _record_rate_limit(
            "login-ip",
            ip_key,
            limit=config["AUTH_LOGIN_IP_THROTTLE_LIMIT"],
            window_seconds=config["AUTH_LOGIN_THROTTLE_WINDOW_SECONDS"],
        ),
    ]
    limited_decisions = [decision for decision in decisions if decision.limited]
    if not limited_decisions:
        return None
    return max(limited_decisions, key=lambda decision: decision.retry_after_seconds)


def _clear_login_failures(email, ip_address):
    email_key = _throttle_key(email, fallback="blank-email")
    ip_key = _throttle_key(ip_address, fallback="unknown-ip")
    _clear_rate_limit("login-account", email_key)
    _clear_rate_limit("login-ip", ip_key)


def _record_password_request_attempt(email, ip_address):
    config = current_app.config
    email_key = _throttle_key(email, fallback="blank-email")
    ip_key = _throttle_key(ip_address, fallback="unknown-ip")
    decisions = [
        _record_rate_limit(
            "forgot-password-account",
            email_key,
            limit=config["AUTH_PASSWORD_REQUEST_ACCOUNT_THROTTLE_LIMIT"],
            window_seconds=config["AUTH_PASSWORD_REQUEST_THROTTLE_WINDOW_SECONDS"],
        ),
        _record_rate_limit(
            "forgot-password-ip",
            ip_key,
            limit=config["AUTH_PASSWORD_REQUEST_IP_THROTTLE_LIMIT"],
            window_seconds=config["AUTH_PASSWORD_REQUEST_THROTTLE_WINDOW_SECONDS"],
        ),
    ]
    limited_decisions = [decision for decision in decisions if decision.limited]
    if not limited_decisions:
        return None
    return max(limited_decisions, key=lambda decision: decision.retry_after_seconds)


def _record_password_reset_attempt(account_key, ip_address):
    config = current_app.config
    normalized_account_key = _throttle_key(account_key, fallback="unknown-account")
    ip_key = _throttle_key(ip_address, fallback="unknown-ip")
    decisions = [
        _record_rate_limit(
            "reset-password-account",
            normalized_account_key,
            limit=config["AUTH_PASSWORD_RESET_ACCOUNT_THROTTLE_LIMIT"],
            window_seconds=config["AUTH_PASSWORD_RESET_THROTTLE_WINDOW_SECONDS"],
        ),
        _record_rate_limit(
            "reset-password-ip",
            ip_key,
            limit=config["AUTH_PASSWORD_RESET_IP_THROTTLE_LIMIT"],
            window_seconds=config["AUTH_PASSWORD_RESET_THROTTLE_WINDOW_SECONDS"],
        ),
    ]
    limited_decisions = [decision for decision in decisions if decision.limited]
    if not limited_decisions:
        return None
    return max(limited_decisions, key=lambda decision: decision.retry_after_seconds)


def _clear_password_reset_attempts(account_key, ip_address):
    normalized_account_key = _throttle_key(account_key, fallback="unknown-account")
    ip_key = _throttle_key(ip_address, fallback="unknown-ip")
    _clear_rate_limit("reset-password-account", normalized_account_key)
    _clear_rate_limit("reset-password-ip", ip_key)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    if request.method == "POST":
        ip_address = get_client_ip()
        quick_role = (request.form.get("quick_login_role") or "").strip().lower()
        if _is_dev_quick_login_enabled() and quick_role in DEV_QUICK_LOGIN_EMAILS:
            user = _resolve_quick_login_user(quick_role)
            if (
                user
                and user.active
                and not user.force_password_change
                and not _is_user_locked(user)
            ):
                register_successful_login(user)
                db.session.commit()
                _finish_login(user)
                next_url = request.args.get("next") or request.form.get("next")
                if is_safe_redirect_target(next_url):
                    return redirect(next_url)
                return redirect(url_for("main.dashboard"))
            flash(f"Quick login failed for {quick_role}. Verify test users exist.", "error")
            return _render_login(status_code=401)

        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        user = User.query.filter_by(email=email).first()
        now = utcnow()
        password_matches = bool(user and check_password_hash(user.password_hash, password))
        throttle_decision = _peek_login_throttle(email, ip_address)
        if throttle_decision is not None and not password_matches:
            _log_auth_security_event(
                "login_throttled",
                email=email,
                ip_address=ip_address,
                details={"retry_after_seconds": throttle_decision.retry_after_seconds},
            )
            flash(AUTH_THROTTLE_MESSAGE, "error")
            return _render_login(email=email, status_code=429)

        if not password_matches:
            throttle_decision = _record_login_failure(email, ip_address)
            if throttle_decision is not None:
                _log_auth_security_event(
                    "login_throttled",
                    email=email,
                    user_id=getattr(user, "id", None),
                    ip_address=ip_address,
                    details={"retry_after_seconds": throttle_decision.retry_after_seconds},
                )
                flash(AUTH_THROTTLE_MESSAGE, "error")
                return _render_login(email=email, status_code=429)
            if (
                user
                and user.active
                and not user.force_password_change
                and not _is_user_locked(user, now)
            ):
                _record_failed_login(user)
            _log_auth_security_event(
                "login_failed",
                email=email,
                user_id=getattr(user, "id", None),
                ip_address=ip_address,
            )
            flash(GENERIC_LOGIN_FAILURE_MESSAGE, "error")
            return _render_login(email=email, status_code=401)

        if _is_user_locked(user, now):
            _record_login_failure(email, ip_address)
            _log_auth_security_event(
                "login_denied",
                email=email,
                user_id=user.id,
                ip_address=ip_address,
                details={"reason": "locked"},
            )
            flash(GENERIC_LOGIN_FAILURE_MESSAGE, "error")
            return _render_login(email=email, status_code=401)

        if not user.active or user.force_password_change:
            _record_login_failure(email, ip_address)
            _log_auth_security_event(
                "login_denied",
                email=email,
                user_id=user.id,
                ip_address=ip_address,
                details={
                    "reason": "inactive" if not user.active else "password_setup_required",
                },
            )
            flash(GENERIC_LOGIN_FAILURE_MESSAGE, "error")
            return _render_login(email=email, status_code=401)

        _clear_login_failures(email, ip_address)
        _reset_login_failure_state(user)
        register_successful_login(user)
        db.session.commit()
        _finish_login(user)

        next_url = request.args.get("next") or request.form.get("next")
        if is_safe_redirect_target(next_url):
            return redirect(next_url)
        return redirect(url_for("main.dashboard"))

    return _render_login()


@auth_bp.post("/logout")
def logout():
    session.pop(AUTH_SESSION_VERSION_SESSION_KEY, None)
    session.pop(FEEDBACK_REVIEW_SESSION_KEY, None)
    logout_user()
    flash("Signed out.", "success")
    return redirect(url_for("auth.login"))


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for("auth.account"))

    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        ip_address = get_client_ip()
        throttle_decision = _record_password_request_attempt(email, ip_address)
        if throttle_decision is not None:
            _log_auth_security_event(
                "password_request_throttled",
                email=email,
                ip_address=ip_address,
                details={"retry_after_seconds": throttle_decision.retry_after_seconds},
            )
            flash(
                "If an account matches that email, password reset instructions are ready.",
                "success",
            )
            return redirect(url_for("auth.login"))

        user, issued_link = request_password_link_for_email(email)
        _log_auth_security_event(
            "password_request_submitted",
            email=email,
            user_id=getattr(user, "id", None),
            ip_address=ip_address,
            details={"issued": bool(issued_link)},
        )
        flash(
            "If an account matches that email, password reset instructions are ready.",
            "success",
        )
        if issued_link and current_app.config["AUTH_DEV_EXPOSE_PASSWORD_LINKS"]:
            flash(build_dev_password_link_message(user, issued_link), "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/forgot_password.html")


@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    token_record = None
    token_error = None
    ip_address = get_client_ip()
    try:
        token_record = resolve_password_action_token(token)
    except AccountManagementError as exc:
        token_error = str(exc)

    if request.method == "POST":
        account_key = getattr(getattr(token_record, "user", None), "email", None) or (
            f"token:{hashlib.sha256((token or '').encode('utf-8')).hexdigest()[:12]}"
        )
        throttle_decision = _record_password_reset_attempt(account_key, ip_address)
        if throttle_decision is not None:
            _log_auth_security_event(
                "password_reset_throttled",
                email=getattr(getattr(token_record, "user", None), "email", None),
                user_id=getattr(getattr(token_record, "user", None), "id", None),
                ip_address=ip_address,
                details={"retry_after_seconds": throttle_decision.retry_after_seconds},
            )
            flash(AUTH_THROTTLE_MESSAGE, "error")
            return (
                render_template(
                    "auth/reset_password.html",
                    token=token,
                    token_record=token_record,
                    token_error=token_error,
                    purpose=(token_record.purpose if token_record else None),
                ),
                429,
            )

        if token_error:
            _log_auth_security_event(
                "password_reset_denied",
                email=None,
                ip_address=ip_address,
                details={"reason": token_error},
            )
            flash(token_error, "error")
            return redirect(url_for("auth.forgot_password"))

        new_password = request.form.get("new_password") or ""
        confirm_password = request.form.get("confirm_password") or ""
        try:
            complete_password_action(
                token=token,
                new_password=new_password,
                confirm_password=confirm_password,
            )
        except AccountManagementError as exc:
            token_error = str(exc)
            _log_auth_security_event(
                "password_reset_failed",
                email=getattr(getattr(token_record, "user", None), "email", None),
                user_id=getattr(getattr(token_record, "user", None), "id", None),
                ip_address=ip_address,
                details={"reason": token_error},
            )
        else:
            _clear_password_reset_attempts(account_key, ip_address)
            _log_auth_security_event(
                "password_reset_completed",
                email=getattr(getattr(token_record, "user", None), "email", None),
                user_id=getattr(getattr(token_record, "user", None), "id", None),
                ip_address=ip_address,
            )
            flash("Password updated. You can now sign in.", "success")
            return redirect(url_for("auth.login"))

    return render_template(
        "auth/reset_password.html",
        token=token,
        token_record=token_record,
        token_error=token_error,
        purpose=(token_record.purpose if token_record else None),
    )


@auth_bp.get("/account")
@login_required
def account():
    recent_activity_data = _build_account_activity(
        current_user.id,
        full_history=False,
        page=1,
    )
    activity_search_seed = (current_user.display_name or current_user.email or "").strip()
    dashboard_activity_url = url_for(
        "main.dashboard",
        tab="activity",
        activity_actor_user_id=current_user.id,
        activity_q=activity_search_seed,
    )

    return render_template(
        "auth/account.html",
        avatar_initials=_build_account_initials(current_user),
        avatar_url=_avatar_url_for_user(current_user),
        recent_activity_rows=recent_activity_data["rows"],
        dashboard_activity_url=dashboard_activity_url,
        current_theme_preference=normalize_theme_preference(
            getattr(current_user, "theme_preference", None)
        ),
        theme_options=ACCOUNT_THEME_OPTIONS,
    )


@auth_bp.post("/account/avatar")
@login_required
def upload_avatar():
    file_obj = request.files.get("avatar")
    if file_obj is None or not (file_obj.filename or "").strip():
        flash("Please choose an image to upload.", "error")
        return redirect(url_for("auth.account"))

    original_name = secure_filename(file_obj.filename or "")
    if not original_name or not _is_allowed_avatar_file(original_name):
        flash("Please upload a JPG, JPEG, PNG, or WEBP image.", "error")
        return redirect(url_for("auth.account"))

    extension = original_name.rsplit(".", 1)[1].lower()
    safe_name = f"user_{current_user.id}_{secrets.token_hex(8)}.{extension}"

    upload_dir = _avatar_upload_dir()
    upload_dir.mkdir(parents=True, exist_ok=True)

    old_filename = (current_user.avatar_filename or "").strip()
    file_obj.save(upload_dir / safe_name)

    if old_filename and old_filename != safe_name:
        old_path = upload_dir / old_filename
        if old_path.exists():
            old_path.unlink()

    current_user.avatar_filename = safe_name
    current_user.avatar_updated_at = datetime.utcnow()
    db.session.commit()
    flash("Profile picture updated.", "success")
    return redirect(url_for("auth.account"))


@auth_bp.post("/account/avatar/remove")
@login_required
def remove_avatar():
    upload_dir = _avatar_upload_dir()
    old_filename = (current_user.avatar_filename or "").strip()
    if old_filename:
        old_path = upload_dir / old_filename
        if old_path.exists():
            old_path.unlink()

    current_user.avatar_filename = None
    current_user.avatar_updated_at = None
    db.session.commit()
    flash("Profile picture removed.", "success")
    return redirect(url_for("auth.account"))


@auth_bp.post("/account/profile")
@login_required
def update_profile():
    display_name = (request.form.get("display_name") or "").strip()

    if len(display_name) > 120:
        flash("Display name must be 120 characters or fewer.", "error")
        return redirect(url_for("auth.account"))

    current_user.display_name = display_name or None
    db.session.commit()
    flash("Profile updated.", "success")
    return redirect(url_for("auth.account"))


@auth_bp.post("/account/custom-settings")
@login_required
def update_custom_settings():
    selected_theme = (request.form.get("theme_preference") or "").strip().lower()
    if selected_theme not in VALID_THEME_PREFERENCES:
        flash("Theme selection is invalid.", "error")
        return redirect(url_for("auth.account"))

    current_user.theme_preference = selected_theme
    db.session.commit()
    session[ACTIVE_UI_THEME_SESSION_KEY] = selected_theme
    flash("Custom settings updated.", "success")
    return redirect(url_for("auth.account"))


@auth_bp.post("/account/password")
@login_required
def change_password():
    current_password = request.form.get("current_password") or ""
    new_password = request.form.get("new_password") or ""
    confirm_password = request.form.get("confirm_password") or ""

    if not current_password or not new_password or not confirm_password:
        flash("Please fill in all password fields.", "error")
        return redirect(url_for("auth.account"))

    if not check_password_hash(current_user.password_hash, current_password):
        flash("Current password is incorrect.", "error")
        return redirect(url_for("auth.account"))

    try:
        validate_new_password(new_password, confirm_password)
    except AccountManagementError as exc:
        flash(str(exc), "error")
        return redirect(url_for("auth.account"))

    if check_password_hash(current_user.password_hash, new_password):
        flash("New password must be different from current password.", "error")
        return redirect(url_for("auth.account"))

    change_password_for_user(user=current_user, new_password=new_password)
    session.pop(AUTH_SESSION_VERSION_SESSION_KEY, None)
    session.pop(FEEDBACK_REVIEW_SESSION_KEY, None)
    logout_user()
    flash("Password updated. Please sign in again.", "success")
    return redirect(url_for("auth.login"))
