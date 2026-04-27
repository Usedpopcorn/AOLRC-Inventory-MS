from app import db
from app.models import User


def test_create_app_registers_healthcheck(app):
    routes = {rule.rule for rule in app.url_map.iter_rules()}
    assert "/healthz" in routes


def test_healthcheck_returns_ok(client):
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_login_page_renders(client):
    response = client.get("/login")

    assert response.status_code == 200
    assert b"<title>Sign In" in response.data
    assert b'name="email"' in response.data


def test_dashboard_requires_authentication(client):
    response = client.get("/dashboard")

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_development_quick_login_reaches_dashboard(client, app):
    response = client.post(
        "/login",
        data={"quick_login_role": "admin"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard")

    with app.app_context():
        user_count = db.session.query(User).count()

    assert user_count == 1
