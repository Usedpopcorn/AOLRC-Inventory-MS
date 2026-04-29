import os
from datetime import timedelta
from urllib.parse import urlparse

DEFAULT_SECRET_KEY = "dev-secret-change-me"
DEFAULT_APP_NAME = "AOLRC Inventory Management System"


def _env_flag(name, default=False):
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name, default):
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_csv(name):
    raw = os.getenv(name)
    if raw is None:
        return []
    return [value.strip() for value in raw.split(",") if value.strip()]


def _default_trusted_hosts():
    hosts = {"localhost", "127.0.0.1", "::1"}

    def add_host_from_value(raw_value, *, is_url=True):
        value = (raw_value or "").strip()
        if not value:
            return
        if is_url or "://" in value:
            parsed = urlparse(value)
            if parsed.hostname:
                hosts.add(parsed.hostname.lower())
            return
        hosts.add(value.lower())

    add_host_from_value(os.getenv("APP_BASE_URL"))
    add_host_from_value(os.getenv("RENDER_EXTERNAL_URL"))
    add_host_from_value(os.getenv("RENDER_EXTERNAL_HOSTNAME"), is_url=False)

    # Render free services usually serve from <service>.onrender.com.
    render_service_name = (os.getenv("RENDER_SERVICE_NAME") or "").strip().lower()
    if render_service_name:
        hosts.add(f"{render_service_name}.onrender.com")
    if _env_flag("RENDER", default=False):
        hosts.add(".onrender.com")

    return sorted(hosts)


def is_development_environment():
    if _env_flag("RENDER", default=False):
        return False
    runtime_env = (os.getenv("FLASK_ENV") or "").strip().lower()
    return runtime_env in {"", "development"}


class Config:
    APP_NAME = (os.getenv("APP_NAME") or DEFAULT_APP_NAME).strip() or DEFAULT_APP_NAME
    SECRET_KEY = DEFAULT_SECRET_KEY
    SQLALCHEMY_DATABASE_URI = "sqlite:///local.db"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    APP_BASE_URL = (os.getenv("APP_BASE_URL") or "").strip() or None
    FEEDBACK_REVIEW_PIN = (os.getenv("FEEDBACK_REVIEW_PIN") or "").strip()
    TRUSTED_HOSTS = tuple(_env_csv("TRUSTED_HOSTS") or _default_trusted_hosts())
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _env_flag(
        "SESSION_COOKIE_SECURE",
        default=not is_development_environment(),
    )
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_SECURE = _env_flag(
        "REMEMBER_COOKIE_SECURE",
        default=not is_development_environment(),
    )
    PERMANENT_SESSION_LIFETIME = timedelta(
        hours=max(1, _env_int("PERMANENT_SESSION_HOURS", 12))
    )
    AUTH_MAX_FAILED_LOGIN_ATTEMPTS = max(
        1, _env_int("AUTH_MAX_FAILED_LOGIN_ATTEMPTS", 8)
    )
    AUTH_LOCKOUT_MINUTES = max(1, _env_int("AUTH_LOCKOUT_MINUTES", 15))
    AUTH_PASSWORD_MIN_LENGTH = max(8, _env_int("AUTH_PASSWORD_MIN_LENGTH", 8))
    AUTH_PASSWORD_TOKEN_TTL_HOURS = max(1, _env_int("AUTH_PASSWORD_TOKEN_TTL_HOURS", 4))
    AUTH_LOGIN_ACCOUNT_THROTTLE_LIMIT = max(
        1,
        _env_int(
            "AUTH_LOGIN_ACCOUNT_THROTTLE_LIMIT",
            AUTH_MAX_FAILED_LOGIN_ATTEMPTS + 2,
        ),
    )
    AUTH_LOGIN_IP_THROTTLE_LIMIT = max(
        1,
        _env_int(
            "AUTH_LOGIN_IP_THROTTLE_LIMIT",
            max(12, AUTH_MAX_FAILED_LOGIN_ATTEMPTS * 3),
        ),
    )
    AUTH_LOGIN_THROTTLE_WINDOW_SECONDS = max(
        60, _env_int("AUTH_LOGIN_THROTTLE_WINDOW_SECONDS", 300)
    )
    AUTH_PASSWORD_REQUEST_ACCOUNT_THROTTLE_LIMIT = max(
        1, _env_int("AUTH_PASSWORD_REQUEST_ACCOUNT_THROTTLE_LIMIT", 4)
    )
    AUTH_PASSWORD_REQUEST_IP_THROTTLE_LIMIT = max(
        1, _env_int("AUTH_PASSWORD_REQUEST_IP_THROTTLE_LIMIT", 8)
    )
    AUTH_PASSWORD_REQUEST_THROTTLE_WINDOW_SECONDS = max(
        60, _env_int("AUTH_PASSWORD_REQUEST_THROTTLE_WINDOW_SECONDS", 300)
    )
    AUTH_PASSWORD_RESET_ACCOUNT_THROTTLE_LIMIT = max(
        1, _env_int("AUTH_PASSWORD_RESET_ACCOUNT_THROTTLE_LIMIT", 6)
    )
    AUTH_PASSWORD_RESET_IP_THROTTLE_LIMIT = max(
        1, _env_int("AUTH_PASSWORD_RESET_IP_THROTTLE_LIMIT", 8)
    )
    AUTH_PASSWORD_RESET_THROTTLE_WINDOW_SECONDS = max(
        60, _env_int("AUTH_PASSWORD_RESET_THROTTLE_WINDOW_SECONDS", 300)
    )
    FEEDBACK_SUBMISSION_LIMIT = max(1, _env_int("FEEDBACK_SUBMISSION_LIMIT", 4))
    FEEDBACK_SUBMISSION_WINDOW_SECONDS = max(
        60, _env_int("FEEDBACK_SUBMISSION_WINDOW_SECONDS", 300)
    )
    VENUE_FILE_MAX_BYTES = max(1, _env_int("VENUE_FILE_MAX_BYTES", 25 * 1024 * 1024))
    AUTH_ALLOW_DEV_QUICK_LOGIN = _env_flag(
        "AUTH_ALLOW_DEV_QUICK_LOGIN",
        default=is_development_environment(),
    )
    AUTH_DEV_EXPOSE_PASSWORD_LINKS = _env_flag(
        "AUTH_DEV_EXPOSE_PASSWORD_LINKS",
        default=is_development_environment(),
    )
    MAIL_ENABLED = _env_flag("MAIL_ENABLED", default=False)
    MAIL_BACKEND = (os.getenv("MAIL_BACKEND") or "smtp").strip().lower()
    MAIL_SERVER = (os.getenv("MAIL_SERVER") or "").strip()
    MAIL_PORT = max(1, _env_int("MAIL_PORT", 25))
    MAIL_USERNAME = (os.getenv("MAIL_USERNAME") or "").strip()
    MAIL_PASSWORD = os.getenv("MAIL_PASSWORD") or ""
    MAIL_USE_TLS = _env_flag("MAIL_USE_TLS", default=False)
    MAIL_USE_SSL = _env_flag("MAIL_USE_SSL", default=False)
    MAIL_DEFAULT_SENDER = (os.getenv("MAIL_DEFAULT_SENDER") or "").strip()
    MAIL_SUPPRESS_SEND = _env_flag("MAIL_SUPPRESS_SEND", default=False)
    MAIL_TIMEOUT_SECONDS = max(1, _env_int("MAIL_TIMEOUT_SECONDS", 10))
    MAIL_CAPTURE_UI_URL = (
        (os.getenv("MAIL_CAPTURE_UI_URL") or "http://127.0.0.1:8025").strip()
        if is_development_environment()
        else (os.getenv("MAIL_CAPTURE_UI_URL") or "").strip()
    )
