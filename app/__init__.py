import os
import subprocess
import click
from datetime import datetime, timedelta, timezone

from flask import Flask, flash, jsonify, redirect, request, session, url_for
from flask_wtf.csrf import CSRFError, CSRFProtect
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager, current_user
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import generate_password_hash
from dotenv import load_dotenv

from .config import Config, DEFAULT_SECRET_KEY, is_development_environment
from .security import get_csp_nonce, is_safe_redirect_target

db = SQLAlchemy()
migrate = Migrate()
csrf = CSRFProtect()
login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Please sign in to continue."
login_manager.login_message_category = "error"
AUTH_SESSION_VERSION_SESSION_KEY = "_auth_sv"
ACTIVE_UI_THEME_SESSION_KEY = "_ui_theme"


@login_manager.user_loader
def load_user(user_id):
    from .models import User

    if not user_id:
        return None
    try:
        parsed_user_id = int(user_id)
    except (TypeError, ValueError):
        return None
    user = db.session.get(User, parsed_user_id)
    if user is None or not user.active:
        return None
    session_version = session.get(AUTH_SESSION_VERSION_SESSION_KEY)
    try:
        parsed_session_version = int(session_version)
    except (TypeError, ValueError):
        return None
    if parsed_session_version != int(user.session_version or 1):
        return None
    return user


@login_manager.unauthorized_handler
def handle_unauthorized():
    from .authz import wants_json_response

    if wants_json_response():
        return jsonify({"error": "authentication required", "code": "unauthenticated"}), 401
    next_path = request.full_path if request.query_string else request.path
    return redirect(url_for("auth.login", next=next_path))


def _current_git_branch():
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    try:
        completed = subprocess.run(
            ["git", "branch", "--show-current"],
            check=False,
            capture_output=True,
            text=True,
            cwd=repo_root,
        )
    except OSError:
        return None

    if completed.returncode != 0:
        return None
    branch_name = (completed.stdout or "").strip()
    if not branch_name or branch_name == "HEAD":
        return None
    return branch_name


def _is_sqlite_database_url(value):
    return (value or "").strip().lower().startswith("sqlite:")


def _enforce_development_database_branch_policy(database_url):
    if not is_development_environment():
        return

    current_branch = _current_git_branch()
    if not current_branch or current_branch.lower() == "main":
        return
    if _is_sqlite_database_url(database_url):
        return

    raise RuntimeError(
        f"Current branch '{current_branch}' is configured with a non-SQLite DATABASE_URL. "
        "Feature branches must use local SQLite instead of the shared Postgres database. "
        "Update .env and recreate the Docker container so it picks up the branch-local SQLite setting."
    )


def _save_user(email, password, role, display_name=None):
    from .models import DEFAULT_THEME_PREFERENCE, User, normalize_role, normalize_theme_preference

    normalized_email = (email or "").strip().lower()
    normalized_role = normalize_role(role)
    normalized_display_name = (display_name or "").strip() or None
    now = datetime.now(timezone.utc)

    if not normalized_email:
        raise click.ClickException("Email is required.")
    if not password:
        raise click.ClickException("Password is required.")

    existing = User.query.filter_by(email=normalized_email).first()
    if existing:
        existing.password_hash = generate_password_hash(password)
        existing.role = normalized_role
        existing.display_name = normalized_display_name
        existing.theme_preference = normalize_theme_preference(
            getattr(existing, "theme_preference", None)
        )
        existing.active = True
        existing.force_password_change = False
        existing.require_login_verification = False
        existing.session_version = int(existing.session_version or 0) + 1
        existing.password_changed_at = now
        existing.last_login_at = None
        existing.last_login_verification_at = None
        existing.failed_login_attempts = 0
        existing.locked_until = None
        existing.deactivated_at = None
        existing.deactivated_by_user_id = None
        db.session.commit()
        return existing, False

    user = User(
        email=normalized_email,
        display_name=normalized_display_name,
        theme_preference=DEFAULT_THEME_PREFERENCE,
        password_hash=generate_password_hash(password),
        role=normalized_role,
        active=True,
        force_password_change=False,
        require_login_verification=False,
        password_changed_at=now,
    )
    db.session.add(user)
    db.session.commit()
    return user, True


def create_app():
    from .models import normalize_theme_preference

    # Keep explicit shell/runtime env vars in control and only fill missing values from .env.
    load_dotenv(override=False)

    app = Flask(
    __name__,
    template_folder="../templates",
    static_folder="../static"
)
    app.config.from_object(Config)
    if app.config.get("TRUST_PROXY_HEADERS"):
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", app.config["SECRET_KEY"])
    app.config["APP_BASE_URL"] = (os.getenv("APP_BASE_URL", app.config["APP_BASE_URL"] or "").strip() or None)
    app.config["APP_TIMEZONE"] = (
        os.getenv("APP_TIMEZONE", app.config.get("APP_TIMEZONE") or "UTC").strip() or "UTC"
    )
    app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL", app.config["SQLALCHEMY_DATABASE_URI"])
    _enforce_development_database_branch_policy(app.config["SQLALCHEMY_DATABASE_URI"])
    app_base_url = (app.config.get("APP_BASE_URL") or "").strip()
    if not is_development_environment():
        if not app_base_url:
            raise RuntimeError(
                "APP_BASE_URL must be configured outside development so account emails use the deployed origin."
            )
        if not app_base_url.lower().startswith("https://"):
            raise RuntimeError(
                "APP_BASE_URL must use https:// outside development."
            )
    venue_file_max_bytes = int(app.config.get("VENUE_FILE_MAX_BYTES") or (25 * 1024 * 1024))
    try:
        max_content_length = int(os.getenv("MAX_CONTENT_LENGTH", str(max(2 * 1024 * 1024, venue_file_max_bytes))))
    except ValueError:
        max_content_length = max(2 * 1024 * 1024, venue_file_max_bytes)
    app.config["MAX_CONTENT_LENGTH"] = max_content_length
    app.config["VENUE_FILE_UPLOAD_DIR"] = os.getenv(
        "VENUE_FILE_UPLOAD_DIR",
        os.path.join(app.instance_path, "venue_files"),
    )
    app.config["AVATAR_UPLOAD_DIR"] = os.getenv(
        "AVATAR_UPLOAD_DIR",
        os.path.join(app.static_folder, "uploads", "avatars"),
    )
    app.config["AVATAR_WEB_PREFIX"] = os.getenv("AVATAR_WEB_PREFIX", "uploads/avatars")
    if (
        app.config["SECRET_KEY"] == DEFAULT_SECRET_KEY
        and not app.debug
        and not is_development_environment()
    ):
        raise RuntimeError(
            "SECRET_KEY is using the default development value. "
            "Set a unique SECRET_KEY before running outside development."
        )
    if app.config["AUTH_ALLOW_DEV_QUICK_LOGIN"] and not is_development_environment():
        raise RuntimeError(
            "AUTH_ALLOW_DEV_QUICK_LOGIN is enabled outside development. "
            "Disable dev quick login before running outside development."
        )
    if app.config["AUTH_DEV_EXPOSE_PASSWORD_LINKS"] and not is_development_environment():
        raise RuntimeError(
            "AUTH_DEV_EXPOSE_PASSWORD_LINKS is enabled outside development. "
            "Disable dev password-link exposure before running outside development."
        )

    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)
    login_manager.init_app(app)

    def _resolve_active_ui_theme():
        if current_user.is_authenticated:
            return normalize_theme_preference(
                getattr(current_user, "theme_preference", None)
            )
        return normalize_theme_preference(session.get(ACTIVE_UI_THEME_SESSION_KEY))

    @app.before_request
    def sync_authenticated_theme_hint():
        if not current_user.is_authenticated:
            return
        active_theme = normalize_theme_preference(
            getattr(current_user, "theme_preference", None)
        )
        if session.get(ACTIVE_UI_THEME_SESSION_KEY) != active_theme:
            session[ACTIVE_UI_THEME_SESSION_KEY] = active_theme

    @app.context_processor
    def inject_security_template_helpers():
        return {
            "active_ui_theme": _resolve_active_ui_theme(),
            "csp_nonce": get_csp_nonce,
        }

    from .routes.main import main_bp
    app.register_blueprint(main_bp)

    from .routes.auth import auth_bp
    app.register_blueprint(auth_bp)

    from .routes.admin import admin_bp
    app.register_blueprint(admin_bp)

    from .routes.venue_items import venue_items_bp
    app.register_blueprint(venue_items_bp)

    from .routes.venue_settings import venue_settings_bp
    app.register_blueprint(venue_settings_bp)

    from .routes.supplies import supplies_bp
    app.register_blueprint(supplies_bp)

    from .routes.orders import orders_bp
    app.register_blueprint(orders_bp)

    from .routes.feedback import feedback_bp
    app.register_blueprint(feedback_bp)

    from . import models  # ensures models are registered for migrations

    @app.get("/healthz")
    def healthcheck():
        return jsonify({"status": "ok"}), 200

    @app.errorhandler(CSRFError)
    def handle_csrf_error(error):
        flash("Your form session expired. Please try again.", "error")
        referrer = request.referrer or ""
        if referrer and is_safe_redirect_target(referrer):
            return redirect(referrer)
        return redirect(url_for("auth.login"))

    @app.after_request
    def apply_security_headers(response):
        script_nonce = get_csp_nonce()
        response.headers.setdefault(
            "Content-Security-Policy",
            "; ".join(
                [
                    "default-src 'self'",
                    "base-uri 'self'",
                    "frame-ancestors 'none'",
                    "object-src 'none'",
                    "img-src 'self' data:",
                    "font-src 'self' data: https://cdn.jsdelivr.net https://fonts.gstatic.com",
                    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com",
                    f"script-src 'self' 'nonce-{script_nonce}' https://cdn.jsdelivr.net",
                    ]
            ),
        )
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), geolocation=(), microphone=()",
        )
        if request.is_secure or app.config.get("SESSION_COOKIE_SECURE"):
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
        return response

    @app.cli.command("create-admin")
    @click.option("--email", prompt=True, help="Admin email")
    @click.option("--display-name", default="", help="Optional display name")
    @click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True, help="Admin password")
    def create_admin(email, display_name, password):
        user, created = _save_user(
            email=email,
            password=password,
            role="admin",
            display_name=display_name,
        )
        action = "Created" if created else "Updated existing"
        click.echo(f"{action} admin user: {user.email}")

    @app.cli.command("create-user")
    @click.option("--email", prompt=True, help="User email")
    @click.option("--display-name", default="", help="Optional display name")
    @click.option(
        "--role",
        type=click.Choice(["viewer", "staff", "admin"], case_sensitive=False),
        default="viewer",
        show_default=True,
        help="User role",
    )
    @click.option(
        "--password",
        prompt=True,
        hide_input=True,
        confirmation_prompt=True,
        help="User password",
    )
    def create_user(email, display_name, role, password):
        user, created = _save_user(
            email=email,
            password=password,
            role=role,
            display_name=display_name,
        )
        action = "Created" if created else "Updated existing"
        click.echo(f"{action} user: {user.email} ({user.role})")

    @app.cli.command("seed-dev-auth")
    @click.option(
        "--password",
        default="local-test-password",
        show_default=True,
        help="Shared password for the seeded development users.",
    )
    @click.option(
        "--lockout-minutes",
        default=None,
        type=int,
        help="Override the locked test user's remaining lockout window.",
    )
    def seed_dev_auth(password, lockout_minutes):
        from .models import DEFAULT_THEME_PREFERENCE, User

        now = datetime.now(timezone.utc)
        effective_lockout_minutes = lockout_minutes or app.config["AUTH_LOCKOUT_MINUTES"]
        locked_until = now + timedelta(minutes=effective_lockout_minutes)
        fixtures = [
            {
                "email": "admin@example.com",
                "display_name": "Admin User",
                "role": "admin",
                "active": True,
                "locked_until": None,
            },
            {
                "email": "staff@example.com",
                "display_name": "Staff User",
                "role": "staff",
                "active": True,
                "locked_until": None,
            },
            {
                "email": "viewer@example.com",
                "display_name": "Viewer User",
                "role": "viewer",
                "active": True,
                "locked_until": None,
            },
            {
                "email": "inactive@example.com",
                "display_name": "Inactive User",
                "role": "viewer",
                "active": False,
                "locked_until": None,
            },
            {
                "email": "locked@example.com",
                "display_name": "Locked User",
                "role": "viewer",
                "active": True,
                "locked_until": locked_until,
            },
        ]

        for fixture in fixtures:
            user = User.query.filter_by(email=fixture["email"]).first()
            if user is None:
                user = User(email=fixture["email"])
                db.session.add(user)
                user.session_version = 1
            else:
                user.session_version = int(user.session_version or 0) + 1

            user.display_name = fixture["display_name"]
            user.theme_preference = DEFAULT_THEME_PREFERENCE
            user.password_hash = generate_password_hash(password)
            user.role = fixture["role"]
            user.active = fixture["active"]
            user.force_password_change = False
            user.require_login_verification = False
            user.password_changed_at = now
            user.last_login_at = None
            user.last_login_verification_at = None
            user.failed_login_attempts = 0
            user.locked_until = fixture["locked_until"]
            user.deactivated_at = None if fixture["active"] else now
            user.deactivated_by_user_id = None

        db.session.commit()
        click.echo("Seeded dev auth users: admin, staff, viewer, inactive, and locked.")
    return app
