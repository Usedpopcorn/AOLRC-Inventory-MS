import os
from datetime import timedelta

DEFAULT_SECRET_KEY = "dev-secret-change-me"


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


def is_development_environment():
    if _env_flag("RENDER", default=False):
        return False
    runtime_env = (os.getenv("FLASK_ENV") or "").strip().lower()
    return runtime_env in {"", "development"}


class Config:
    SECRET_KEY = DEFAULT_SECRET_KEY
    SQLALCHEMY_DATABASE_URI = "sqlite:///local.db"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    APP_BASE_URL = (os.getenv("APP_BASE_URL") or "").strip() or None
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
        1, _env_int("AUTH_MAX_FAILED_LOGIN_ATTEMPTS", 5)
    )
    AUTH_LOCKOUT_MINUTES = max(1, _env_int("AUTH_LOCKOUT_MINUTES", 15))
    AUTH_PASSWORD_MIN_LENGTH = max(8, _env_int("AUTH_PASSWORD_MIN_LENGTH", 8))
    AUTH_PASSWORD_TOKEN_TTL_HOURS = max(1, _env_int("AUTH_PASSWORD_TOKEN_TTL_HOURS", 24))
    AUTH_ALLOW_DEV_QUICK_LOGIN = _env_flag(
        "AUTH_ALLOW_DEV_QUICK_LOGIN",
        default=is_development_environment(),
    )
    AUTH_DEV_EXPOSE_PASSWORD_LINKS = _env_flag(
        "AUTH_DEV_EXPOSE_PASSWORD_LINKS",
        default=is_development_environment(),
    )
