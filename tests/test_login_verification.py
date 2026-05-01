from datetime import datetime, timedelta, timezone

from werkzeug.security import generate_password_hash

from app import db
from app.models import LoginVerificationChallenge, TrustedDevice, User
from app.services.login_verification import hash_trusted_token, hash_user_agent
from app.services.mail_service import MailDeliveryResult


def create_user(*, email, password="local-test-password", role="viewer", active=True):
    now = datetime.now(timezone.utc)
    user = User(
        email=email,
        password_hash=generate_password_hash(password),
        role=role,
        active=active,
        force_password_change=False,
        password_changed_at=now,
        created_at=now,
    )
    db.session.add(user)
    db.session.flush()
    return user


def test_unknown_device_requires_email_verification(client, app, monkeypatch):
    app.config["AUTH_ALLOW_DEV_QUICK_LOGIN"] = False
    app.config["LOGIN_EMAIL_2FA_ENABLED"] = True
    captured_codes = []

    def fake_send_login_verification_email(*, user, verification_code, expires_at):
        captured_codes.append((user.email, verification_code, expires_at))
        return MailDeliveryResult(status="sent", message="ok", recipient=user.email)

    monkeypatch.setattr(
        "app.routes.auth.send_login_verification_email",
        fake_send_login_verification_email,
    )

    with app.app_context():
        create_user(email="verify@example.com", password="Verify!123")
        db.session.commit()

    login_response = client.post(
        "/login",
        data={"email": "verify@example.com", "password": "Verify!123"},
        follow_redirects=False,
    )
    assert login_response.status_code == 302
    assert login_response.headers["Location"].endswith("/verify-login")
    assert captured_codes and captured_codes[0][0] == "verify@example.com"

    invalid_response = client.post(
        "/verify-login",
        data={"verification_code": "000000"},
        follow_redirects=False,
    )
    assert invalid_response.status_code == 401

    verify_response = client.post(
        "/verify-login",
        data={"verification_code": captured_codes[0][1], "trust_device": "1"},
        follow_redirects=False,
    )
    assert verify_response.status_code == 302
    assert verify_response.headers["Location"].endswith("/dashboard")
    assert "aolrc_trusted_device=" in verify_response.headers.get("Set-Cookie", "")


def test_trusted_device_bypasses_verification_on_next_login(app, monkeypatch):
    app.config["AUTH_ALLOW_DEV_QUICK_LOGIN"] = False
    app.config["LOGIN_EMAIL_2FA_ENABLED"] = True
    user_agent = "Mozilla/5.0 Test Browser"
    raw_token = "trusted-token-123"
    mail_calls = []

    def fake_send_login_verification_email(*, user, verification_code, expires_at):
        mail_calls.append((user.email, verification_code))
        return MailDeliveryResult(status="sent", message="ok", recipient=user.email)

    monkeypatch.setattr(
        "app.routes.auth.send_login_verification_email",
        fake_send_login_verification_email,
    )

    with app.app_context():
        user = create_user(email="trusted@example.com", password="Trusted!123")
        db.session.flush()
        trusted_device = TrustedDevice(
            user_id=user.id,
            token_hash=hash_trusted_token(raw_token),
            user_agent_hash=hash_user_agent(user_agent),
            user_agent_summary="Test Browser",
            last_ip="198.51.100.10",
            last_country="US",
            expires_at=datetime.now(timezone.utc) + timedelta(days=30),
            last_seen_at=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
        )
        db.session.add(trusted_device)
        db.session.commit()

    with app.test_client() as trusted_client:
        trusted_client.set_cookie(
            app.config["TRUSTED_DEVICE_COOKIE_NAME"],
            raw_token,
        )
        response = trusted_client.post(
            "/login",
            data={"email": "trusted@example.com", "password": "Trusted!123"},
            follow_redirects=False,
            headers={"User-Agent": user_agent},
            environ_overrides={"REMOTE_ADDR": "198.51.100.10"},
        )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard")
    assert mail_calls == []


def test_stale_trusted_device_requires_reverification(app, monkeypatch):
    app.config["AUTH_ALLOW_DEV_QUICK_LOGIN"] = False
    app.config["LOGIN_EMAIL_2FA_ENABLED"] = True
    app.config["TRUSTED_DEVICE_DAYS_VIEWER"] = 1
    user_agent = "Mozilla/5.0 Test Browser"
    raw_token = "stale-trusted-token"
    mail_calls = []

    def fake_send_login_verification_email(*, user, verification_code, expires_at):
        mail_calls.append((user.email, verification_code))
        return MailDeliveryResult(status="sent", message="ok", recipient=user.email)

    monkeypatch.setattr(
        "app.routes.auth.send_login_verification_email",
        fake_send_login_verification_email,
    )

    with app.app_context():
        user = create_user(email="stale@example.com", password="Stale!123")
        db.session.flush()
        trusted_device = TrustedDevice(
            user_id=user.id,
            token_hash=hash_trusted_token(raw_token),
            user_agent_hash=hash_user_agent(user_agent),
            user_agent_summary="Test Browser",
            last_ip="198.51.100.10",
            last_country="US",
            expires_at=datetime.now(timezone.utc) + timedelta(days=30),
            last_seen_at=datetime.now(timezone.utc) - timedelta(days=5),
            created_at=datetime.now(timezone.utc) - timedelta(days=5),
        )
        db.session.add(trusted_device)
        db.session.commit()

    with app.test_client() as stale_client:
        stale_client.set_cookie(
            app.config["TRUSTED_DEVICE_COOKIE_NAME"],
            raw_token,
        )
        response = stale_client.post(
            "/login",
            data={"email": "stale@example.com", "password": "Stale!123"},
            follow_redirects=False,
            headers={"User-Agent": user_agent},
            environ_overrides={"REMOTE_ADDR": "198.51.100.10"},
        )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/verify-login")
    assert mail_calls


def test_admin_can_revoke_trusted_devices(client, app):
    with app.app_context():
        admin = create_user(email="admin@example.com", password="Admin!123", role="admin")
        user = create_user(email="revoke-me@example.com", password="Viewer!123", role="viewer")
        db.session.flush()
        db.session.add(
            TrustedDevice(
                user_id=user.id,
                token_hash=hash_trusted_token("revoke-token"),
                user_agent_hash=hash_user_agent("ua"),
                user_agent_summary="ua",
                expires_at=datetime.now(timezone.utc) + timedelta(days=10),
                last_seen_at=datetime.now(timezone.utc),
            )
        )
        db.session.commit()
        admin_id = admin.id
        user_id = user.id

    with client.session_transaction() as session_state:
        session_state["_user_id"] = str(admin_id)
        session_state["_fresh"] = True
        session_state["_auth_sv"] = 1

    response = client.post(
        f"/admin/users/{user_id}/trusted-devices/revoke",
        data={"next": "/admin/users"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Revoked 1 trusted device(s)" in response.data

    with app.app_context():
        active_count = TrustedDevice.query.filter(
            TrustedDevice.user_id == user_id,
            TrustedDevice.revoked_at.is_(None),
        ).count()
        assert active_count == 0


def test_password_reset_forces_followup_login_verification(client, app, monkeypatch):
    app.config["LOGIN_EMAIL_2FA_ENABLED"] = True
    app.config["AUTH_DEV_EXPOSE_PASSWORD_LINKS"] = True
    captured_codes = []

    def fake_send_login_verification_email(*, user, verification_code, expires_at):
        captured_codes.append(verification_code)
        return MailDeliveryResult(status="sent", message="ok", recipient=user.email)

    monkeypatch.setattr(
        "app.routes.auth.send_login_verification_email",
        fake_send_login_verification_email,
    )

    with app.app_context():
        create_user(email="reset2fa@example.com", password="OldPass!123")
        db.session.commit()

    forgot = client.post(
        "/forgot-password",
        data={"email": "reset2fa@example.com"},
        follow_redirects=True,
    )
    body = forgot.get_data(as_text=True)
    token = body.split("/reset-password/")[1].split('"')[0].split()[0]
    reset_path = f"/reset-password/{token}"

    reset_response = client.post(
        reset_path,
        data={"new_password": "NewPass!123", "confirm_password": "NewPass!123"},
        follow_redirects=True,
    )
    assert reset_response.status_code == 200

    login_response = client.post(
        "/login",
        data={"email": "reset2fa@example.com", "password": "NewPass!123"},
        follow_redirects=False,
    )
    assert login_response.status_code == 302
    assert login_response.headers["Location"].endswith("/verify-login")
    assert captured_codes

    with app.app_context():
        challenge = (
            LoginVerificationChallenge.query.order_by(
                LoginVerificationChallenge.id.desc()
            ).first()
        )
        assert challenge is not None
