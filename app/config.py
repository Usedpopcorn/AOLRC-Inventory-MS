import os
from datetime import timedelta


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


class Config:
    SECRET_KEY = "dev-secret-change-me"
    SQLALCHEMY_DATABASE_URI = "sqlite:///local.db"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _env_flag("SESSION_COOKIE_SECURE", default=False)
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_SECURE = _env_flag("REMEMBER_COOKIE_SECURE", default=False)
    PERMANENT_SESSION_LIFETIME = timedelta(
        hours=max(1, _env_int("PERMANENT_SESSION_HOURS", 12))
    )
    AUTH_MAX_FAILED_LOGIN_ATTEMPTS = max(
        1, _env_int("AUTH_MAX_FAILED_LOGIN_ATTEMPTS", 5)
    )
    AUTH_LOCKOUT_MINUTES = max(1, _env_int("AUTH_LOCKOUT_MINUTES", 15))