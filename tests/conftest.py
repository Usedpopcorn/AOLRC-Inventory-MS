import os

import pytest

os.environ.setdefault("FLASK_ENV", "development")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from app import create_app, db  # noqa: E402
from app.services.rate_limits import rate_limiter  # noqa: E402


@pytest.fixture
def app():
    rate_limiter.reset_all()
    app = create_app()
    app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
    )
    with app.app_context():
        db.drop_all()
        db.create_all()

    yield app

    with app.app_context():
        db.session.remove()
        db.drop_all()
    rate_limiter.reset_all()


@pytest.fixture
def client(app):
    return app.test_client()
