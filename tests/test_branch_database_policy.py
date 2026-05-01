import pytest

import app as app_module


def test_create_app_rejects_postgres_on_non_main_branch(monkeypatch):
    monkeypatch.setenv("FLASK_ENV", "development")
    monkeypatch.setenv("DATABASE_URL", "postgresql://example.invalid/shared")
    monkeypatch.setattr(app_module, "_current_git_branch", lambda: "feature-notes")

    with pytest.raises(RuntimeError, match="Feature branches must use local SQLite"):
        app_module.create_app()


def test_create_app_allows_sqlite_on_non_main_branch(monkeypatch):
    monkeypatch.setenv("FLASK_ENV", "development")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setattr(app_module, "_current_git_branch", lambda: "feature-notes")

    app = app_module.create_app()

    assert app.config["SQLALCHEMY_DATABASE_URI"] == "sqlite:///:memory:"


def test_create_app_allows_postgres_on_main_branch(monkeypatch):
    monkeypatch.setenv("FLASK_ENV", "development")
    monkeypatch.setenv("DATABASE_URL", "postgresql://example.invalid/shared")
    monkeypatch.setattr(app_module, "_current_git_branch", lambda: "main")

    app = app_module.create_app()

    assert app.config["SQLALCHEMY_DATABASE_URI"] == "postgresql://example.invalid/shared"


def test_create_app_requires_app_base_url_outside_development(monkeypatch):
    monkeypatch.setenv("FLASK_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.delenv("APP_BASE_URL", raising=False)
    monkeypatch.setenv("SECRET_KEY", "not-default-secret")
    monkeypatch.setenv("AUTH_ALLOW_DEV_QUICK_LOGIN", "0")
    monkeypatch.setenv("AUTH_DEV_EXPOSE_PASSWORD_LINKS", "0")

    with pytest.raises(RuntimeError, match="APP_BASE_URL must be configured outside development"):
        app_module.create_app()


def test_create_app_requires_https_app_base_url_outside_development(monkeypatch):
    monkeypatch.setenv("FLASK_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("APP_BASE_URL", "http://inventory.example.org")
    monkeypatch.setenv("SECRET_KEY", "not-default-secret")
    monkeypatch.setenv("AUTH_ALLOW_DEV_QUICK_LOGIN", "0")
    monkeypatch.setenv("AUTH_DEV_EXPOSE_PASSWORD_LINKS", "0")

    with pytest.raises(RuntimeError, match="APP_BASE_URL must use https:// outside development"):
        app_module.create_app()
