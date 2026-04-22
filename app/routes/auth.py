import os
from datetime import timedelta
from urllib.parse import urljoin, urlparse

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash

from app import AUTH_SESSION_VERSION_SESSION_KEY, db
from app.models import User
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
from app.services.inventory_status import ensure_utc

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


def _is_safe_redirect_target(target):
    if not target:
        return False
    host_url = urlparse(request.host_url)
    redirect_url = urlparse(urljoin(request.host_url, target))
    return redirect_url.scheme in {"http", "https"} and host_url.netloc == redirect_url.netloc


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
            if existing.active and not existing.force_password_change and not _is_user_locked(existing):
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
    db.session.commit()


def _finish_login(user):
    login_user(user)
    session[AUTH_SESSION_VERSION_SESSION_KEY] = int(user.session_version or 1)


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


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    if request.method == "POST":
        quick_role = (request.form.get("quick_login_role") or "").strip().lower()
        if _is_dev_quick_login_enabled() and quick_role in DEV_QUICK_LOGIN_EMAILS:
            user = _resolve_quick_login_user(quick_role)
            if user and user.active and not user.force_password_change and not _is_user_locked(user):
                register_successful_login(user)
                db.session.commit()
                _finish_login(user)
                next_url = request.args.get("next") or request.form.get("next")
                if _is_safe_redirect_target(next_url):
                    return redirect(next_url)
                return redirect(url_for("main.dashboard"))
            flash(f"Quick login failed for {quick_role}. Verify test users exist.", "error")
            return _render_login(status_code=401)

        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""

        user = User.query.filter_by(email=email).first()
        now = utcnow()
        if user and _is_user_locked(user, now):
            locked_until = ensure_utc(user.locked_until)
            minutes_remaining = max(
                1,
                int((locked_until - ensure_utc(now)).total_seconds() // 60) + 1,
            )
            flash(
                f"Too many failed attempts. Try again in about {minutes_remaining} minute(s).",
                "error",
            )
            return _render_login(email=email, status_code=429)

        if user is None or not check_password_hash(user.password_hash, password):
            if user:
                _record_failed_login(user)
            flash("Invalid email or password.", "error")
            return _render_login(email=email, status_code=401)

        if not user.active:
            flash("Your account is inactive. Contact an admin.", "error")
            return _render_login(email=email, status_code=403)

        if user.force_password_change:
            flash("Use your password setup link or contact an admin to finish setting your password.", "error")
            return _render_login(email=email, status_code=403)

        _reset_login_failure_state(user)
        register_successful_login(user)
        db.session.commit()
        _finish_login(user)

        next_url = request.args.get("next") or request.form.get("next")
        if _is_safe_redirect_target(next_url):
            return redirect(next_url)
        return redirect(url_for("main.dashboard"))

    return _render_login()


@auth_bp.post("/logout")
def logout():
    session.pop(AUTH_SESSION_VERSION_SESSION_KEY, None)
    logout_user()
    flash("Signed out.", "success")
    return redirect(url_for("auth.login"))


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for("auth.account"))

    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        user, issued_link = request_password_link_for_email(email)
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
    try:
        token_record = resolve_password_action_token(token)
    except AccountManagementError as exc:
        token_error = str(exc)

    if request.method == "POST":
        if token_error:
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
        else:
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
    return render_template(
        "auth/account.html",
        avatar_initials=_build_account_initials(current_user),
    )


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
    logout_user()
    flash("Password updated. Please sign in again.", "success")
    return redirect(url_for("auth.login"))
