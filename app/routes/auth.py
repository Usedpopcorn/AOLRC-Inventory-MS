import os
from datetime import datetime, timedelta
from urllib.parse import urlparse, urljoin

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash

from app import db
from app.models import User

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
    return (os.getenv("FLASK_ENV") or "").strip().lower() == "development"


def _render_login(email="", status_code=200):
    return (
        render_template(
            "auth/login.html",
            email=email,
            quick_login_enabled=_is_dev_quick_login_enabled(),
        ),
        status_code,
    )


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


def _resolve_quick_login_user(quick_role):
    role_value = DEV_QUICK_LOGIN_ROLE_MAP.get(quick_role)
    if role_value:
        user = (
            User.query.filter_by(role=role_value, active=True)
            .order_by(User.id.asc())
            .first()
        )
        if user:
            return user

    fallback_email = DEV_QUICK_LOGIN_EMAILS.get(quick_role)
    if fallback_email:
        existing = User.query.filter_by(email=fallback_email).first()
        if existing:
            return existing
        # Development-only convenience: bootstrap quick-login users in fresh local DBs.
        user = User(
            email=fallback_email,
            display_name=f"{role_value.title()} User" if role_value else "Viewer User",
            password_hash=generate_password_hash(DEV_QUICK_LOGIN_DEFAULT_PASSWORD),
            role=role_value or "viewer",
            active=True,
        )
        db.session.add(user)
        db.session.commit()
        return user
    return None


def _utcnow():
    return datetime.utcnow()


def _record_failed_login(user):
    max_attempts = current_app.config["AUTH_MAX_FAILED_LOGIN_ATTEMPTS"]
    lockout_minutes = current_app.config["AUTH_LOCKOUT_MINUTES"]

    user.failed_login_attempts = (user.failed_login_attempts or 0) + 1
    if user.failed_login_attempts >= max_attempts:
        user.locked_until = _utcnow() + timedelta(minutes=lockout_minutes)
        user.failed_login_attempts = 0
    db.session.commit()


def _reset_login_failure_state(user):
    if user.failed_login_attempts or user.locked_until:
        user.failed_login_attempts = 0
        user.locked_until = None
        db.session.commit()


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    if request.method == "POST":
        quick_role = (request.form.get("quick_login_role") or "").strip().lower()
        if _is_dev_quick_login_enabled() and quick_role in DEV_QUICK_LOGIN_EMAILS:
            user = _resolve_quick_login_user(quick_role)
            if user and user.active:
                login_user(user)
                next_url = request.args.get("next") or request.form.get("next")
                if _is_safe_redirect_target(next_url):
                    return redirect(next_url)
                return redirect(url_for("main.dashboard"))
            flash(f"Quick login failed for {quick_role}. Verify test users exist.", "error")
            return _render_login(status_code=401)

        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""

        user = User.query.filter_by(email=email).first()
        now = _utcnow()
        if user and user.locked_until and user.locked_until > now:
            minutes_remaining = max(
                1,
                int((user.locked_until - now).total_seconds() // 60) + 1,
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

        _reset_login_failure_state(user)
        login_user(user)

        next_url = request.args.get("next") or request.form.get("next")
        if _is_safe_redirect_target(next_url):
            return redirect(next_url)
        return redirect(url_for("main.dashboard"))

    return _render_login()


@auth_bp.post("/logout")
def logout():
    logout_user()
    flash("Signed out.", "success")
    return redirect(url_for("auth.login"))


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

    if new_password != confirm_password:
        flash("New password and confirmation do not match.", "error")
        return redirect(url_for("auth.account"))

    if len(new_password) < 8:
        flash("New password must be at least 8 characters.", "error")
        return redirect(url_for("auth.account"))

    if check_password_hash(current_user.password_hash, new_password):
        flash("New password must be different from current password.", "error")
        return redirect(url_for("auth.account"))

    current_user.password_hash = generate_password_hash(new_password)
    db.session.commit()
    flash("Password updated successfully.", "success")
    return redirect(url_for("auth.account"))
