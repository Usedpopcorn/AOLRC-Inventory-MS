import re
from datetime import datetime, timedelta, timezone

import pytest
from werkzeug.security import generate_password_hash

from app import ACTIVE_UI_THEME_SESSION_KEY, db
from app.models import AccountAuditEvent, PasswordActionToken, User
from app.services.account_security import (
    AccountManagementError,
    create_managed_user,
    set_user_active_state,
    update_managed_user,
    validate_new_password,
)
from app.services.admin_hub import build_admin_history_view_model
from app.services.mail_service import (
    MAIL_STATUS_FAILED,
    MAIL_STATUS_SENT,
    MAIL_STATUS_SUPPRESSED,
    MailDeliveryResult,
    send_transactional_email,
)
from app.services.rate_limits import rate_limiter

RESET_LINK_RE = re.compile(r"http://localhost/reset-password/([A-Za-z0-9_\-]+)")
FORGOT_PASSWORD_FLASH = (
    b"If an account matches that email, password reset instructions have been sent."
)


def quick_login(client, role):
    return client.post(
        "/login",
        data={"quick_login_role": role},
        follow_redirects=False,
    )


def create_user(
    *,
    email,
    password="local-test-password",
    role="viewer",
    active=True,
    display_name=None,
    theme_preference=None,
    force_password_change=False,
    locked_until=None,
    failed_login_attempts=0,
):
    now = datetime.now(timezone.utc)
    user_kwargs = dict(
        email=email,
        display_name=display_name,
        password_hash=generate_password_hash(password),
        role=role,
        active=active,
        force_password_change=force_password_change,
        password_changed_at=now,
        created_at=now,
        locked_until=locked_until,
        failed_login_attempts=failed_login_attempts,
        deactivated_at=None if active else now,
    )
    if theme_preference is not None:
        user_kwargs["theme_preference"] = theme_preference
    user = User(
        **user_kwargs,
    )
    db.session.add(user)
    db.session.flush()
    return user


def extract_reset_path(response_text):
    match = RESET_LINK_RE.search(response_text)
    assert match is not None
    return f"/reset-password/{match.group(1)}"


def login_with_password(client, email, password, *, next_path=None, remote_addr=None):
    return client.post(
        "/login",
        data={
            "email": email,
            "password": password,
            "next": next_path or "",
        },
        environ_overrides={"REMOTE_ADDR": remote_addr} if remote_addr else None,
        follow_redirects=False,
    )


def assert_theme_attr(response, expected_theme):
    assert f'data-theme="{expected_theme}"'.encode() in response.data


def assert_checked_theme(response_text, theme):
    assert re.search(rf'value="{theme}"\s+checked', response_text) is not None


def test_validate_new_password_requires_special_character(app):
    with app.app_context():
        expected_error = (
            "Password must be at least 8 characters and include at least 1 special character."
        )
        with pytest.raises(
            AccountManagementError,
            match=expected_error,
        ):
            validate_new_password("password", "password")

        assert validate_new_password("passw0rd!", "passw0rd!") == "passw0rd!"


def test_password_link_logging_omits_raw_url_and_token(caplog, app):
    with app.app_context():
        admin = create_user(
            email="security-admin@example.com",
            role="admin",
            display_name="Security Admin",
        )
        db.session.commit()

        with app.test_request_context(base_url="http://localhost"):
            with caplog.at_level("INFO"):
                _user, issued_link = create_managed_user(
                    actor=admin,
                    email="security-target@example.com",
                    display_name="Security Target",
                    role="viewer",
                )

    logged_messages = "\n".join(record.getMessage() for record in caplog.records)
    assert issued_link.raw_token not in logged_messages
    assert issued_link.url not in logged_messages
    assert "token_fingerprint=" in logged_messages


def test_mail_service_suppression_skips_smtp(app, monkeypatch):
    def fail_if_smtp_is_used(*_args, **_kwargs):
        raise AssertionError("SMTP should not be used when mail is suppressed.")

    monkeypatch.setattr("app.services.mail_service.smtplib.SMTP", fail_if_smtp_is_used)
    app.config.update(
        MAIL_ENABLED=True,
        MAIL_SUPPRESS_SEND=True,
        MAIL_SERVER="127.0.0.1",
        MAIL_PORT=1025,
        MAIL_DEFAULT_SENDER="noreply@example.test",
    )

    with app.app_context():
        result = send_transactional_email(
            to_address="user@example.test",
            subject="Test message",
            body_text="Body",
        )

    assert result.status == MAIL_STATUS_SUPPRESSED
    assert result.suppressed is True


def test_mail_service_missing_config_logs_sanitized_error(caplog, app):
    app.config.update(
        MAIL_ENABLED=True,
        MAIL_SUPPRESS_SEND=False,
        MAIL_SERVER="",
        MAIL_DEFAULT_SENDER="noreply@example.test",
        MAIL_PASSWORD="super-secret-password",
    )

    with app.app_context(), caplog.at_level("ERROR"):
        result = send_transactional_email(
            to_address="user@example.test",
            subject="Test message",
            body_text="Body",
        )

    logged_messages = "\n".join(record.getMessage() for record in caplog.records)
    assert result.status == MAIL_STATUS_FAILED
    assert result.failed is True
    assert "MAIL_SERVER and MAIL_DEFAULT_SENDER are required" in logged_messages
    assert "super-secret-password" not in logged_messages


def test_admin_can_create_user_from_users_page(client, app):
    app.config["MAIL_ENABLED"] = False

    quick_login(client, "admin")

    response = client.post(
        "/admin/users",
        data={
            "email": "managed@example.com",
            "display_name": "Managed User",
            "role": "staff",
            "page": "1",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert (
        b"Created account for managed@example.com. A password setup email is being prepared."
        in response.data
    )
    assert b"Mail delivery is disabled; no password email was sent." in response.data
    assert b"/reset-password/" in response.data

    with app.app_context():
        user = User.query.filter_by(email="managed@example.com").first()
        assert user is not None
        assert user.role == "staff"
        assert user.force_password_change is True
        assert (
            PasswordActionToken.query.filter_by(
                user_id=user.id,
                purpose="password_setup",
            ).count()
            == 1
        )
        event_types = {
            event.event_type
            for event in AccountAuditEvent.query.filter_by(target_user_id=user.id).all()
        }
        assert "user_created" in event_types
        assert "password_setup_initiated" in event_types


def test_admin_can_edit_activate_unlock_and_issue_password_link(client, app):
    app.config["MAIL_ENABLED"] = False

    quick_login(client, "admin")

    with app.app_context():
        locked_until = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=15)
        user = create_user(
            email="support@example.com",
            role="viewer",
            active=False,
            display_name="Support User",
            locked_until=locked_until,
            failed_login_attempts=2,
        )
        user_id = user.id
        db.session.commit()

    edit_response = client.post(
        f"/admin/users/{user_id}/edit",
        data={
            "display_name": "Support Lead",
            "role": "staff",
            "next": f"/admin/users/{user_id}/edit",
        },
        follow_redirects=True,
    )

    assert edit_response.status_code == 200
    assert b"User details updated." in edit_response.data

    activate_response = client.post(
        f"/admin/users/{user_id}/activate",
        data={"next": f"/admin/users/{user_id}/edit"},
        follow_redirects=True,
    )
    assert activate_response.status_code == 200
    assert b"User activated." in activate_response.data

    unlock_response = client.post(
        f"/admin/users/{user_id}/unlock",
        data={"next": f"/admin/users/{user_id}/edit"},
        follow_redirects=True,
    )
    assert unlock_response.status_code == 200
    assert b"User unlocked." in unlock_response.data

    reset_response = client.post(
        f"/admin/users/{user_id}/password-link",
        data={"next": f"/admin/users/{user_id}/edit"},
        follow_redirects=True,
    )
    assert reset_response.status_code == 200
    assert b"Password reset link prepared for support@example.com." in reset_response.data
    assert b"Mail delivery is disabled; no password email was sent." in reset_response.data
    assert b"/reset-password/" in reset_response.data

    with app.app_context():
        user = db.session.get(User, user_id)
        assert user.display_name == "Support Lead"
        assert user.role == "staff"
        assert user.active is True
        assert user.locked_until is None
        assert user.failed_login_attempts == 0


def test_admin_user_password_actions_send_email_without_exposing_link(client, app, monkeypatch):
    app.config["AUTH_DEV_EXPOSE_PASSWORD_LINKS"] = False
    app.config["MAIL_CAPTURE_UI_URL"] = "http://127.0.0.1:8025"
    sent_messages = []

    def fake_send_password_action_email(user, issued_link):
        sent_messages.append(
            {
                "email": user.email,
                "purpose": issued_link.purpose,
                "url": issued_link.url,
            }
        )
        return MailDeliveryResult(
            status=MAIL_STATUS_SENT,
            message="Mail sent.",
            recipient=user.email,
        )

    monkeypatch.setattr(
        "app.routes.admin.send_password_action_email",
        fake_send_password_action_email,
    )
    quick_login(client, "admin")

    create_response = client.post(
        "/admin/users",
        data={
            "email": "admin-mail@example.com",
            "display_name": "Admin Mail",
            "role": "viewer",
            "page": "1",
        },
        follow_redirects=True,
    )

    assert create_response.status_code == 200
    assert (
        b"Created account for admin-mail@example.com. A password setup email is being prepared."
        in create_response.data
    )
    assert b"Password email sent to admin-mail@example.com." in create_response.data
    assert b"Inspect captured mail at http://127.0.0.1:8025." in create_response.data
    assert b"/reset-password/" not in create_response.data
    assert [message["purpose"] for message in sent_messages] == ["password_setup"]

    with app.app_context():
        user = User.query.filter_by(email="admin-mail@example.com").first()
        user_id = user.id

    sent_messages.clear()
    link_response = client.post(
        f"/admin/users/{user_id}/password-link",
        data={"next": f"/admin/users/{user_id}/edit"},
        follow_redirects=True,
    )

    assert link_response.status_code == 200
    assert b"Password setup link prepared for admin-mail@example.com." in link_response.data
    assert b"Password email sent to admin-mail@example.com." in link_response.data
    assert b"/reset-password/" not in link_response.data
    assert [message["purpose"] for message in sent_messages] == ["password_setup"]


def test_self_deactivation_is_blocked_route(client, app):
    quick_login(client, "admin")

    with app.app_context():
        admin_user = User.query.filter_by(email="admin@example.com").first()
        admin_id = admin_user.id

    response = client.post(
        f"/admin/users/{admin_id}/deactivate",
        data={"next": "/admin/users"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"You cannot deactivate your own account." in response.data

    with app.app_context():
        assert db.session.get(User, admin_id).active is True


def test_self_admin_demotion_is_blocked_route(client, app):
    quick_login(client, "admin")

    with app.app_context():
        admin_user = User.query.filter_by(email="admin@example.com").first()
        admin_id = admin_user.id

    response = client.post(
        f"/admin/users/{admin_id}/edit",
        data={
            "display_name": "Admin User",
            "role": "viewer",
            "next": f"/admin/users/{admin_id}/edit",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"You cannot remove your own admin role." in response.data

    with app.app_context():
        assert db.session.get(User, admin_id).role == "admin"


def test_last_active_admin_invariant_is_enforced_in_service(app):
    with app.app_context():
        admin_one = create_user(email="one@example.com", role="admin", display_name="Admin One")
        admin_two = create_user(email="two@example.com", role="admin", display_name="Admin Two")
        db.session.commit()

        set_user_active_state(actor=admin_one, user_id=admin_two.id, should_be_active=False)

        try:
            update_managed_user(
                actor=admin_two,
                user_id=admin_one.id,
                display_name="Admin One",
                role="viewer",
            )
        except AccountManagementError as exc:
            assert str(exc) == "At least one active admin account must remain."
        else:
            raise AssertionError(
                "Expected the last active admin safeguard to block the role change."
            )

        assert db.session.get(User, admin_one.id).role == "admin"
        assert db.session.get(User, admin_one.id).active is True


def test_forgot_password_reset_flow_is_one_time_and_restores_login(client, app):
    with app.app_context():
        create_user(email="resetme@example.com", password="old-password-1", display_name="Reset Me")
        db.session.commit()

    request_response = client.post(
        "/forgot-password",
        data={"email": "resetme@example.com"},
        follow_redirects=True,
    )

    assert request_response.status_code == 200
    assert FORGOT_PASSWORD_FLASH in request_response.data
    reset_path = extract_reset_path(request_response.get_data(as_text=True))

    invalid_response = client.post(
        reset_path,
        data={
            "new_password": "password",
            "confirm_password": "password",
        },
        follow_redirects=True,
    )
    assert invalid_response.status_code == 200
    assert b"Password needs one change." in invalid_response.data
    assert (
        b"Password must be at least 8 characters and include at least 1 special character."
        in invalid_response.data
    )
    assert b"Set Password" not in invalid_response.data
    assert b"Reset Password" in invalid_response.data
    assert b"Password rules" in invalid_response.data
    assert b"data-password-toggle=\"new_password\"" in invalid_response.data

    with app.app_context():
        user = User.query.filter_by(email="resetme@example.com").first()
        token = PasswordActionToken.query.filter_by(user_id=user.id).first()
        assert token.consumed_at is None

    reused_response = client.post(
        reset_path,
        data={
            "new_password": "old-password-1",
            "confirm_password": "old-password-1",
        },
        follow_redirects=True,
    )
    assert reused_response.status_code == 200
    assert b"New password must be different from your current password." in reused_response.data

    with app.app_context():
        user = User.query.filter_by(email="resetme@example.com").first()
        token = PasswordActionToken.query.filter_by(user_id=user.id).first()
        assert token.consumed_at is None

    reset_response = client.post(
        reset_path,
        data={
            "new_password": "new-password-1",
            "confirm_password": "new-password-1",
        },
        follow_redirects=True,
    )

    assert reset_response.status_code == 200
    assert b"Password updated. You can now sign in." in reset_response.data

    second_use = client.post(
        reset_path,
        data={
            "new_password": "another-password-1",
            "confirm_password": "another-password-1",
        },
        follow_redirects=True,
    )
    assert second_use.status_code == 200
    assert b"This password link has already been used." in second_use.data

    login_response = client.post(
        "/login",
        data={"email": "resetme@example.com", "password": "new-password-1"},
        follow_redirects=False,
    )
    assert login_response.status_code == 302
    assert login_response.headers["Location"].endswith("/dashboard")

    with app.app_context():
        user = User.query.filter_by(email="resetme@example.com").first()
        token = PasswordActionToken.query.filter_by(user_id=user.id).first()
        assert user.password_changed_at is not None
        assert user.force_password_change is False
        assert user.locked_until is None
        assert token.consumed_at is not None
        assert AccountAuditEvent.query.filter_by(
            target_user_id=user.id,
            event_type="password_reset_completed",
        ).count() == 1


def test_forgot_password_for_unknown_email_stays_generic(client, app):
    response = client.post(
        "/forgot-password",
        data={"email": "missing@example.com"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert FORGOT_PASSWORD_FLASH in response.data
    assert b"/reset-password/" not in response.data

    with app.app_context():
        assert PasswordActionToken.query.count() == 0


def test_forgot_password_sends_email_without_exposing_link(client, app, monkeypatch):
    app.config["AUTH_DEV_EXPOSE_PASSWORD_LINKS"] = False
    app.config["APP_BASE_URL"] = "https://inventory.example.org"
    sent_messages = []

    def fake_send_password_action_email(user, issued_link):
        sent_messages.append(
            {
                "email": user.email,
                "purpose": issued_link.purpose,
                "url": issued_link.url,
            }
        )
        return MailDeliveryResult(
            status=MAIL_STATUS_SENT,
            message="Mail sent.",
            recipient=user.email,
        )

    monkeypatch.setattr(
        "app.routes.auth.send_password_action_email",
        fake_send_password_action_email,
    )
    with app.app_context():
        create_user(
            email="email-reset@example.com",
            password="old-password-1",
            display_name="Email Reset",
        )
        db.session.commit()

    response = client.post(
        "/forgot-password",
        data={"email": "email-reset@example.com"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert FORGOT_PASSWORD_FLASH in response.data
    assert b"/reset-password/" not in response.data
    assert len(sent_messages) == 1
    assert sent_messages[0]["email"] == "email-reset@example.com"
    assert sent_messages[0]["purpose"] == "password_reset"
    assert sent_messages[0]["url"].startswith("https://inventory.example.org/reset-password/")


def test_forgot_password_does_not_send_email_for_unknown_or_inactive_users(
    client,
    app,
    monkeypatch,
):
    app.config["AUTH_DEV_EXPOSE_PASSWORD_LINKS"] = False
    sent_messages = []

    def fake_send_password_action_email(user, issued_link):
        sent_messages.append((user.email, issued_link.url))
        return MailDeliveryResult(status=MAIL_STATUS_SENT, message="Mail sent.")

    monkeypatch.setattr(
        "app.routes.auth.send_password_action_email",
        fake_send_password_action_email,
    )
    with app.app_context():
        create_user(
            email="inactive-reset@example.com",
            password="old-password-1",
            display_name="Inactive Reset",
            active=False,
        )
        db.session.commit()

    missing_response = client.post(
        "/forgot-password",
        data={"email": "missing@example.com"},
        follow_redirects=True,
    )
    inactive_response = client.post(
        "/forgot-password",
        data={"email": "inactive-reset@example.com"},
        follow_redirects=True,
    )

    assert missing_response.status_code == 200
    assert inactive_response.status_code == 200
    assert FORGOT_PASSWORD_FLASH in missing_response.data
    assert FORGOT_PASSWORD_FLASH in inactive_response.data
    assert b"/reset-password/" not in missing_response.data
    assert b"/reset-password/" not in inactive_response.data
    assert sent_messages == []


def test_users_default_theme_preference_is_purple_and_account_page_reflects_it(client, app):
    with app.app_context():
        created_user = create_user(
            email="theme-default@example.com",
            password="Theme!123",
            display_name="Theme Default",
        )
        db.session.commit()
        assert created_user.theme_preference == "purple"

    quick_login(client, "admin")
    response = client.get("/account")

    assert response.status_code == 200
    assert_theme_attr(response, "purple")
    assert_checked_theme(response.get_data(as_text=True), "purple")

    with app.app_context():
        admin_user = User.query.filter_by(email="admin@example.com").first()
        assert admin_user is not None
        assert admin_user.theme_preference == "purple"


def test_account_custom_settings_updates_theme_preference_and_session(client, app):
    quick_login(client, "admin")

    response = client.post(
        "/account/custom-settings",
        data={"theme_preference": "blue"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Custom settings updated." in response.data
    assert_theme_attr(response, "blue")
    assert_checked_theme(response.get_data(as_text=True), "blue")

    with client.session_transaction() as session_state:
        assert session_state[ACTIVE_UI_THEME_SESSION_KEY] == "blue"

    with app.app_context():
        admin_user = User.query.filter_by(email="admin@example.com").first()
        assert admin_user is not None
        assert admin_user.theme_preference == "blue"


def test_account_page_activity_snapshot_links_to_dashboard_actor_filter(client, app):
    quick_login(client, "admin")

    with app.app_context():
        admin_user = User.query.filter_by(email="admin@example.com").first()
        admin_id = admin_user.id

    response = client.get("/account")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Recent Activity" in body
    assert "See More" in body
    assert f"activity_actor_user_id={admin_id}" in body
    assert "activity_view=full" not in body
    assert "Full History" not in body


def test_dashboard_activity_tab_renders_actor_filter_summary_when_selected(client, app):
    quick_login(client, "admin")

    with app.app_context():
        admin_user = User.query.filter_by(email="admin@example.com").first()
        admin_id = admin_user.id

    response = client.get(f"/dashboard?tab=activity&activity_actor_user_id={admin_id}")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'name="activity_actor_user_id"' in body
    assert f'value="{admin_id}"' in body
    assert "Filtered to:" in body
    assert "Clear user filter" in body


def test_invalid_theme_preference_is_rejected_without_mutation(client, app):
    quick_login(client, "admin")

    response = client.post(
        "/account/custom-settings",
        data={"theme_preference": "sunset"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Theme selection is invalid." in response.data
    assert_theme_attr(response, "purple")
    assert_checked_theme(response.get_data(as_text=True), "purple")

    with client.session_transaction() as session_state:
        assert session_state[ACTIVE_UI_THEME_SESSION_KEY] == "purple"

    with app.app_context():
        admin_user = User.query.filter_by(email="admin@example.com").first()
        assert admin_user is not None
        assert admin_user.theme_preference == "purple"


def test_login_logout_and_auth_pages_preserve_theme_hint(client, app):
    with app.app_context():
        create_user(
            email="blue-theme@example.com",
            password="BlueTheme!1",
            display_name="Blue Theme",
            theme_preference="blue",
        )
        db.session.commit()

    login_response = login_with_password(client, "blue-theme@example.com", "BlueTheme!1")

    assert login_response.status_code == 302
    assert login_response.headers["Location"].endswith("/dashboard")

    with client.session_transaction() as session_state:
        assert session_state[ACTIVE_UI_THEME_SESSION_KEY] == "blue"

    account_response = client.get("/account")
    assert account_response.status_code == 200
    assert_theme_attr(account_response, "blue")
    assert_checked_theme(account_response.get_data(as_text=True), "blue")

    logout_response = client.post("/logout", follow_redirects=True)
    assert logout_response.status_code == 200
    assert_theme_attr(logout_response, "blue")

    with client.session_transaction() as session_state:
        assert session_state[ACTIVE_UI_THEME_SESSION_KEY] == "blue"

    forgot_password_page = client.get("/forgot-password")
    assert forgot_password_page.status_code == 200
    assert_theme_attr(forgot_password_page, "blue")

    request_response = client.post(
        "/forgot-password",
        data={"email": "blue-theme@example.com"},
        follow_redirects=True,
    )
    assert request_response.status_code == 200
    reset_path = extract_reset_path(request_response.get_data(as_text=True))

    reset_page = client.get(reset_path)
    assert reset_page.status_code == 200
    assert_theme_attr(reset_page, "blue")


def test_seed_dev_auth_command_upserts_known_users(app):
    runner = app.test_cli_runner()

    result = runner.invoke(args=["seed-dev-auth"])

    assert result.exit_code == 0
    assert "Seeded dev auth users" in result.output

    with app.app_context():
        users_by_email = {user.email: user for user in User.query.all()}
        assert users_by_email["admin@example.com"].role == "admin"
        assert users_by_email["staff@example.com"].role == "staff"
        assert users_by_email["viewer@example.com"].role == "viewer"
        assert users_by_email["inactive@example.com"].active is False
        assert users_by_email["locked@example.com"].locked_until is not None


def test_history_view_model_includes_account_events(app):
    with app.app_context():
        admin = create_user(
            email="history-admin@example.com",
            role="admin",
            display_name="History Admin",
        )
        db.session.commit()

        with app.test_request_context(base_url="http://localhost"):
            create_managed_user(
                actor=admin,
                email="history-target@example.com",
                display_name="History Target",
                role="viewer",
            )

        history_view = build_admin_history_view_model()

    assert history_view["account_events"]["preview"]
    assert history_view["account_events"]["preview"][0]["title"] in {
        "Issued password setup link",
        "Created user account",
    }


def test_admin_created_user_lifecycle_tracks_logins_and_account_events(app):
    with app.test_client() as admin_client:
        quick_login(admin_client, "admin")
        create_response = admin_client.post(
            "/admin/users",
            data={
                "email": "chain@example.com",
                "display_name": "Lifecycle User",
                "role": "staff",
                "page": "1",
            },
            follow_redirects=True,
        )

        assert create_response.status_code == 200
        setup_path = extract_reset_path(create_response.get_data(as_text=True))

        with app.app_context():
            user = User.query.filter_by(email="chain@example.com").first()
            admin_user = User.query.filter_by(email="admin@example.com").first()
            user_id = user.id
            admin_id = admin_user.id

    with app.test_client() as setup_client:
        setup_response = setup_client.post(
            setup_path,
            data={
                "new_password": "chain-password-1",
                "confirm_password": "chain-password-1",
            },
            follow_redirects=True,
        )
        assert setup_response.status_code == 200
        assert b"Password updated. You can now sign in." in setup_response.data

    with app.test_client() as user_client:
        first_login = login_with_password(user_client, "chain@example.com", "chain-password-1")
        assert first_login.status_code == 302
        assert first_login.headers["Location"].endswith("/dashboard")

    with app.app_context():
        user = db.session.get(User, user_id)
        first_login_at = user.last_login_at
        assert first_login_at is not None
        assert user.force_password_change is False

    with app.test_client() as admin_client:
        quick_login(admin_client, "admin")
        deactivate_response = admin_client.post(
            f"/admin/users/{user_id}/deactivate",
            data={"next": f"/admin/users/{user_id}/edit"},
            follow_redirects=True,
        )
        assert deactivate_response.status_code == 200
        assert b"User deactivated." in deactivate_response.data

    with app.test_client() as blocked_client:
        blocked_login = login_with_password(blocked_client, "chain@example.com", "chain-password-1")
        assert blocked_login.status_code == 401
        assert b"Invalid credentials or account unavailable." in blocked_login.data

    with app.test_client() as admin_client:
        quick_login(admin_client, "admin")
        activate_response = admin_client.post(
            f"/admin/users/{user_id}/activate",
            data={"next": f"/admin/users/{user_id}/edit"},
            follow_redirects=True,
        )
        assert activate_response.status_code == 200
        assert b"User activated." in activate_response.data

    with app.test_client() as user_client:
        second_login = login_with_password(user_client, "chain@example.com", "chain-password-1")
        assert second_login.status_code == 302
        assert second_login.headers["Location"].endswith("/dashboard")

    with app.app_context():
        user = db.session.get(User, user_id)
        assert user.last_login_at is not None
        assert user.last_login_at >= first_login_at
        event_types = [
            event.event_type
            for event in AccountAuditEvent.query.filter_by(target_user_id=user_id)
            .order_by(AccountAuditEvent.id.asc())
            .all()
        ]
        assert event_types == [
            "user_created",
            "password_setup_initiated",
            "password_reset_completed",
            "user_deactivated",
            "user_reactivated",
        ]
        event_actors = {
            event.event_type: event.actor_user_id
            for event in AccountAuditEvent.query.filter_by(target_user_id=user_id).all()
        }
        assert event_actors["user_created"] == admin_id
        assert event_actors["user_deactivated"] == admin_id
        assert event_actors["user_reactivated"] == admin_id
        assert event_actors["password_reset_completed"] == user_id


def test_deactivated_signed_in_user_loses_access_on_next_request(app):
    with app.app_context():
        target_user = create_user(
            email="session-user@example.com",
            password="session-password-1",
            role="viewer",
            display_name="Session User",
        )
        user_id = target_user.id
        db.session.commit()

    user_client = app.test_client()
    admin_client = app.test_client()

    login_response = login_with_password(
        user_client,
        "session-user@example.com",
        "session-password-1",
    )
    assert login_response.status_code == 302

    quick_login(admin_client, "admin")
    deactivate_response = admin_client.post(
        f"/admin/users/{user_id}/deactivate",
        data={"next": "/admin/users"},
        follow_redirects=True,
    )
    assert deactivate_response.status_code == 200

    with app.app_context():
        db.session.remove()
        assert db.session.get(User, user_id).active is False

    protected_response = user_client.get("/dashboard", follow_redirects=False)
    assert protected_response.status_code == 302
    assert "/login" in protected_response.headers["Location"]


def test_deactivation_invalidates_existing_setup_link_until_admin_reissues(app):
    with app.test_client() as admin_client:
        quick_login(admin_client, "admin")
        create_response = admin_client.post(
            "/admin/users",
            data={
                "email": "pending@example.com",
                "display_name": "Pending User",
                "role": "viewer",
                "page": "1",
            },
            follow_redirects=True,
        )
        first_setup_path = extract_reset_path(create_response.get_data(as_text=True))

        with app.app_context():
            user = User.query.filter_by(email="pending@example.com").first()
            user_id = user.id

        deactivate_response = admin_client.post(
            f"/admin/users/{user_id}/deactivate",
            data={"next": f"/admin/users/{user_id}/edit"},
            follow_redirects=True,
        )
        assert deactivate_response.status_code == 200
        assert b"User deactivated." in deactivate_response.data

        with app.app_context():
            open_tokens = PasswordActionToken.query.filter_by(
                user_id=user_id,
                consumed_at=None,
            ).count()
            assert open_tokens == 0

        invalidated_response = admin_client.get(first_setup_path)
        assert invalidated_response.status_code == 200
        assert b"This password link has already been used." in invalidated_response.data

        admin_client.post(
            f"/admin/users/{user_id}/activate",
            data={"next": f"/admin/users/{user_id}/edit"},
            follow_redirects=True,
        )
        reissue_response = admin_client.post(
            f"/admin/users/{user_id}/password-link",
            data={"next": f"/admin/users/{user_id}/edit"},
            follow_redirects=True,
        )
        assert reissue_response.status_code == 200
        assert b"Password setup link prepared for pending@example.com." in reissue_response.data
        second_setup_path = extract_reset_path(reissue_response.get_data(as_text=True))

    with app.test_client() as setup_client:
        invalid_setup_response = setup_client.post(
            second_setup_path,
            data={
                "new_password": "password",
                "confirm_password": "password",
            },
            follow_redirects=True,
        )
        assert invalid_setup_response.status_code == 200
        assert b"Password needs one change." in invalid_setup_response.data
        assert b"Set Password" in invalid_setup_response.data
        assert b"Start a New Password Reset" not in invalid_setup_response.data
        assert b"data-password-toggle=\"new_password\"" in invalid_setup_response.data

        with app.app_context():
            open_tokens = PasswordActionToken.query.filter_by(
                user_id=user_id,
                consumed_at=None,
            ).count()
            assert open_tokens == 1

        completion_response = setup_client.post(
            second_setup_path,
            data={
                "new_password": "pending-password-1",
                "confirm_password": "pending-password-1",
            },
            follow_redirects=True,
        )
        assert completion_response.status_code == 200
        assert b"Password updated. You can now sign in." in completion_response.data

    with app.test_client() as user_client:
        login_response = login_with_password(
            user_client,
            "pending@example.com",
            "pending-password-1",
        )
        assert login_response.status_code == 302
        assert login_response.headers["Location"].endswith("/dashboard")


def test_lockout_unlock_and_login_chain_tracks_state_and_activity(app):
    app.config["AUTH_LOGIN_ACCOUNT_THROTTLE_LIMIT"] = (
        app.config["AUTH_MAX_FAILED_LOGIN_ATTEMPTS"] + 2
    )
    app.config["AUTH_LOGIN_IP_THROTTLE_LIMIT"] = (
        app.config["AUTH_MAX_FAILED_LOGIN_ATTEMPTS"] + 4
    )

    with app.app_context():
        user = create_user(
            email="lock-chain@example.com",
            password="lock-password-1",
            display_name="Lock Chain",
        )
        user_id = user.id
        db.session.commit()

    max_attempts = app.config["AUTH_MAX_FAILED_LOGIN_ATTEMPTS"]

    with app.test_client() as user_client:
        for _ in range(max_attempts):
            invalid_response = login_with_password(
                user_client,
                "lock-chain@example.com",
                "wrong-password",
            )
            assert invalid_response.status_code == 401
            assert b"Invalid credentials or account unavailable." in invalid_response.data

        locked_response = login_with_password(
            user_client,
            "lock-chain@example.com",
            "lock-password-1",
        )
        assert locked_response.status_code == 401
        assert b"Invalid credentials or account unavailable." in locked_response.data

    with app.app_context():
        locked_user = db.session.get(User, user_id)
        assert locked_user.failed_login_attempts == 0
        assert locked_user.locked_until is not None
        assert locked_user.last_login_at is None

    with app.test_client() as admin_client:
        quick_login(admin_client, "admin")
        unlock_response = admin_client.post(
            f"/admin/users/{user_id}/unlock",
            data={"next": f"/admin/users/{user_id}/edit"},
            follow_redirects=True,
        )
        assert unlock_response.status_code == 200
        assert b"User unlocked." in unlock_response.data

    with app.test_client() as user_client:
        login_response = login_with_password(
            user_client,
            "lock-chain@example.com",
            "lock-password-1",
        )
        assert login_response.status_code == 302
        assert login_response.headers["Location"].endswith("/dashboard")

    with app.app_context():
        unlocked_user = db.session.get(User, user_id)
        assert unlocked_user.failed_login_attempts == 0
        assert unlocked_user.locked_until is None
        assert unlocked_user.last_login_at is not None
        assert AccountAuditEvent.query.filter_by(
            target_user_id=user_id,
            event_type="user_unlocked",
        ).count() == 1


def test_stale_failed_login_counter_does_not_lock_account_on_next_typo(app):
    max_attempts = app.config["AUTH_MAX_FAILED_LOGIN_ATTEMPTS"]

    with app.app_context():
        user = create_user(
            email="stale-failures@example.com",
            password="Stale!Password1",
            display_name="Stale Failures",
            failed_login_attempts=max_attempts - 1,
        )
        user_id = user.id
        db.session.commit()

    rate_limiter.reset_all()

    with app.test_client() as user_client:
        typo_response = login_with_password(
            user_client,
            "stale-failures@example.com",
            "wrong-password",
            remote_addr="198.51.100.30",
        )

    assert typo_response.status_code == 401
    assert b"Invalid credentials or account unavailable." in typo_response.data

    with app.app_context():
        user = db.session.get(User, user_id)
        assert user.failed_login_attempts == 1
        assert user.locked_until is None


def test_default_login_throttle_allows_lockout_threshold_before_account_throttle(app):
    assert app.config["AUTH_MAX_FAILED_LOGIN_ATTEMPTS"] == 8
    assert (
        app.config["AUTH_LOGIN_ACCOUNT_THROTTLE_LIMIT"]
        > app.config["AUTH_MAX_FAILED_LOGIN_ATTEMPTS"]
    )
    assert (
        app.config["AUTH_LOGIN_IP_THROTTLE_LIMIT"]
        > app.config["AUTH_LOGIN_ACCOUNT_THROTTLE_LIMIT"]
    )


def test_quick_login_does_not_bypass_force_password_change_accounts(client, app):
    with app.app_context():
        create_user(
            email="admin@example.com",
            role="admin",
            display_name="Forced Setup Admin",
            force_password_change=True,
        )
        db.session.commit()

    response = quick_login(client, "admin")

    assert response.status_code == 401
    assert b"Quick login failed for admin." in response.data


def test_password_login_does_not_bypass_force_password_change(client, app):
    with app.app_context():
        create_user(
            email="setup-required@example.com",
            password="setup-password-1",
            role="viewer",
            display_name="Setup Required",
            force_password_change=True,
        )
        db.session.commit()

    response = login_with_password(
        client,
        "setup-required@example.com",
        "setup-password-1",
    )

    assert response.status_code == 401
    assert b"Invalid credentials or account unavailable." in response.data


def test_quick_login_does_not_bypass_locked_accounts(client, app):
    with app.app_context():
        create_user(
            email="admin@example.com",
            role="admin",
            display_name="Locked Admin",
            locked_until=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=15),
        )
        db.session.commit()

    response = quick_login(client, "admin")

    assert response.status_code == 401
    assert b"Quick login failed for admin." in response.data


def test_login_rejects_external_redirect_targets(app):
    with app.app_context():
        create_user(
            email="redirect@example.com",
            password="redirect-password-1",
            display_name="Redirect User",
        )
        db.session.commit()

    with app.test_client() as user_client:
        response = user_client.post(
            "/login?next=https://evil.example/steal-session",
            data={"email": "redirect@example.com", "password": "redirect-password-1"},
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard")


def test_admin_account_actions_reject_external_redirect_targets(client, app):
    quick_login(client, "admin")

    with app.app_context():
        target_user = create_user(
            email="redirect-target@example.com",
            display_name="Redirect Target",
        )
        user_id = target_user.id
        db.session.commit()

    response = client.post(
        f"/admin/users/{user_id}/deactivate",
        data={"next": "https://evil.example/admin/users"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/users")

    with app.app_context():
        db.session.remove()
        assert db.session.get(User, user_id).active is False


def test_invalid_role_tampering_is_rejected_on_create_and_edit(client, app):
    quick_login(client, "admin")

    create_response = client.post(
        "/admin/users",
        data={
            "email": "tampered-create@example.com",
            "display_name": "Tampered Create",
            "role": "superadmin",
            "page": "1",
        },
        follow_redirects=True,
    )

    assert create_response.status_code == 200
    assert b"Role selection is invalid." in create_response.data

    with app.app_context():
        assert User.query.filter_by(email="tampered-create@example.com").first() is None
        target_user = create_user(email="tampered-edit@example.com", display_name="Tampered Edit")
        user_id = target_user.id
        db.session.commit()

    edit_response = client.post(
        f"/admin/users/{user_id}/edit",
        data={
            "display_name": "Tampered Edit",
            "role": "owner",
            "next": f"/admin/users/{user_id}/edit",
        },
        follow_redirects=True,
    )

    assert edit_response.status_code == 200
    assert b"Role selection is invalid." in edit_response.data

    with app.app_context():
        user = db.session.get(User, user_id)
        assert user.role == "viewer"


def test_forgot_password_does_not_issue_tokens_for_inactive_accounts(client, app):
    with app.app_context():
        create_user(
            email="inactive-reset@example.com",
            password="inactive-password-1",
            active=False,
            display_name="Inactive Reset",
        )
        db.session.commit()

    response = client.post(
        "/forgot-password",
        data={"email": "inactive-reset@example.com"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert FORGOT_PASSWORD_FLASH in response.data
    assert b"/reset-password/" not in response.data

    with app.app_context():
        assert PasswordActionToken.query.count() == 0


def test_account_password_change_invalidates_prior_reset_links_and_logs_event(app):
    with app.app_context():
        create_user(
            email="self-serve@example.com",
            password="start-password-1",
            display_name="Self Serve",
        )
        db.session.commit()

    with app.test_client() as anon_client:
        request_response = anon_client.post(
            "/forgot-password",
            data={"email": "self-serve@example.com"},
            follow_redirects=True,
        )
        reset_path = extract_reset_path(request_response.get_data(as_text=True))

    with app.test_client() as user_client:
        login_response = login_with_password(
            user_client,
            "self-serve@example.com",
            "start-password-1",
        )
        assert login_response.status_code == 302

        account_response = user_client.get("/account")
        assert account_response.status_code == 200
        assert b"data-password-toggle=\"current_password\"" in account_response.data
        assert (
            b"Choose a password that is different from your current password."
            in account_response.data
        )

        reused_response = user_client.post(
            "/account/password",
            data={
                "current_password": "start-password-1",
                "new_password": "start-password-1",
                "confirm_password": "start-password-1",
            },
            follow_redirects=True,
        )
        assert reused_response.status_code == 200
        assert b"New password must be different from your current password." in reused_response.data

        change_response = user_client.post(
            "/account/password",
            data={
                "current_password": "start-password-1",
                "new_password": "updated-password-1",
                "confirm_password": "updated-password-1",
            },
            follow_redirects=False,
        )
        assert change_response.status_code == 302
        assert change_response.headers["Location"].endswith("/login")

        logged_out_response = user_client.get("/account", follow_redirects=False)
        assert logged_out_response.status_code == 302
        assert "/login" in logged_out_response.headers["Location"]

    with app.test_client() as anon_client:
        expired_link_response = anon_client.post(
            reset_path,
            data={
                "new_password": "should-not-work-1",
                "confirm_password": "should-not-work-1",
            },
            follow_redirects=True,
        )
        assert expired_link_response.status_code == 200
        assert b"This password link has already been used." in expired_link_response.data

    with app.test_client() as user_client:
        login_response = login_with_password(
            user_client,
            "self-serve@example.com",
            "updated-password-1",
        )
        assert login_response.status_code == 302
        assert login_response.headers["Location"].endswith("/dashboard")

    with app.app_context():
        user = User.query.filter_by(email="self-serve@example.com").first()
        assert AccountAuditEvent.query.filter_by(
            target_user_id=user.id,
            event_type="password_changed",
        ).count() == 1


def test_login_throttle_blocks_repeated_failed_attempts_without_leaking_account_state(app):
    app.config["AUTH_LOGIN_ACCOUNT_THROTTLE_LIMIT"] = 2
    app.config["AUTH_LOGIN_IP_THROTTLE_LIMIT"] = 5

    with app.app_context():
        create_user(
            email="throttle@example.com",
            password="Throttle!1",
            display_name="Throttle User",
        )
        db.session.commit()

    with app.test_client() as user_client:
        first_response = login_with_password(
            user_client,
            "throttle@example.com",
            "wrong-password",
            remote_addr="198.51.100.20",
        )
        second_response = login_with_password(
            user_client,
            "throttle@example.com",
            "wrong-password",
            remote_addr="198.51.100.20",
        )
        recovered_response = login_with_password(
            user_client,
            "throttle@example.com",
            "Throttle!1",
            remote_addr="198.51.100.20",
        )

    assert first_response.status_code == 401
    assert b"Invalid credentials or account unavailable." in first_response.data
    assert second_response.status_code == 429
    assert b"Too many attempts. Please wait a few minutes and try again." in second_response.data
    assert recovered_response.status_code == 302
    assert recovered_response.headers["Location"].endswith("/dashboard")


def test_forgot_password_throttle_stays_generic_for_existing_accounts(app):
    app.config["AUTH_PASSWORD_REQUEST_ACCOUNT_THROTTLE_LIMIT"] = 2
    app.config["AUTH_PASSWORD_REQUEST_IP_THROTTLE_LIMIT"] = 4

    with app.app_context():
        create_user(
            email="forgot-throttle@example.com",
            password="Forgot!1",
            display_name="Forgot Throttle",
        )
        db.session.commit()

    with app.test_client() as anon_client:
        first_response = anon_client.post(
            "/forgot-password",
            data={"email": "forgot-throttle@example.com"},
            environ_overrides={"REMOTE_ADDR": "198.51.100.30"},
            follow_redirects=True,
        )
        second_response = anon_client.post(
            "/forgot-password",
            data={"email": "forgot-throttle@example.com"},
            environ_overrides={"REMOTE_ADDR": "198.51.100.30"},
            follow_redirects=True,
        )

    assert first_response.status_code == 200
    assert FORGOT_PASSWORD_FLASH in first_response.data
    assert b"/reset-password/" in first_response.data
    assert second_response.status_code == 200
    assert FORGOT_PASSWORD_FLASH in second_response.data
    assert b"/reset-password/" not in second_response.data


def test_reset_password_throttle_blocks_repeated_invalid_posts(app):
    app.config["AUTH_PASSWORD_RESET_ACCOUNT_THROTTLE_LIMIT"] = 2
    app.config["AUTH_PASSWORD_RESET_IP_THROTTLE_LIMIT"] = 4

    with app.app_context():
        create_user(
            email="reset-throttle@example.com",
            password="Reset!1",
            display_name="Reset Throttle",
        )
        db.session.commit()

    with app.test_client() as anon_client:
        request_response = anon_client.post(
            "/forgot-password",
            data={"email": "reset-throttle@example.com"},
            environ_overrides={"REMOTE_ADDR": "198.51.100.40"},
            follow_redirects=True,
        )
        reset_path = extract_reset_path(request_response.get_data(as_text=True))

        first_response = anon_client.post(
            reset_path,
            data={
                "new_password": "password",
                "confirm_password": "password",
            },
            environ_overrides={"REMOTE_ADDR": "198.51.100.40"},
            follow_redirects=True,
        )
        second_response = anon_client.post(
            reset_path,
            data={
                "new_password": "password",
                "confirm_password": "password",
            },
            environ_overrides={"REMOTE_ADDR": "198.51.100.40"},
            follow_redirects=False,
        )

    assert first_response.status_code == 200
    assert (
        b"Password must be at least 8 characters and include at least 1 special character."
        in first_response.data
    )
    assert second_response.status_code == 429


def test_expired_password_link_is_rejected(client, app):
    with app.app_context():
        create_user(
            email="expired@example.com",
            password="expired-password-1",
            display_name="Expired User",
        )
        db.session.commit()

    request_response = client.post(
        "/forgot-password",
        data={"email": "expired@example.com"},
        follow_redirects=True,
    )
    reset_path = extract_reset_path(request_response.get_data(as_text=True))

    with app.app_context():
        token = PasswordActionToken.query.first()
        token.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)
        db.session.commit()

    expired_response = client.get(reset_path)

    assert expired_response.status_code == 200
    assert b"This password link has expired." in expired_response.data


def test_password_reset_invalidates_existing_authenticated_session(app):
    with app.app_context():
        create_user(
            email="reset-session@example.com",
            password="original-password-1",
            display_name="Reset Session",
        )
        db.session.commit()

    user_client = app.test_client()
    anon_client = app.test_client()

    login_response = login_with_password(
        user_client,
        "reset-session@example.com",
        "original-password-1",
    )
    assert login_response.status_code == 302

    request_response = anon_client.post(
        "/forgot-password",
        data={"email": "reset-session@example.com"},
        follow_redirects=True,
    )
    reset_path = extract_reset_path(request_response.get_data(as_text=True))

    reset_response = anon_client.post(
        reset_path,
        data={
            "new_password": "replacement-password-1",
            "confirm_password": "replacement-password-1",
        },
        follow_redirects=True,
    )
    assert reset_response.status_code == 200
    assert b"Password updated. You can now sign in." in reset_response.data

    stale_session_response = user_client.get("/dashboard", follow_redirects=False)
    assert stale_session_response.status_code == 302
    assert "/login" in stale_session_response.headers["Location"]

    fresh_login_response = login_with_password(
        user_client,
        "reset-session@example.com",
        "replacement-password-1",
    )
    assert fresh_login_response.status_code == 302
    assert fresh_login_response.headers["Location"].endswith("/dashboard")


def test_admin_role_change_invalidates_existing_session(app):
    with app.app_context():
        target_user = create_user(
            email="session-role@example.com",
            password="role-password-1",
            role="viewer",
            display_name="Session Role",
        )
        user_id = target_user.id
        db.session.commit()

    user_client = app.test_client()
    admin_client = app.test_client()

    login_response = login_with_password(user_client, "session-role@example.com", "role-password-1")
    assert login_response.status_code == 302

    quick_login(admin_client, "admin")
    edit_response = admin_client.post(
        f"/admin/users/{user_id}/edit",
        data={
            "display_name": "Session Role",
            "role": "staff",
            "next": f"/admin/users/{user_id}/edit",
        },
        follow_redirects=True,
    )
    assert edit_response.status_code == 200
    assert b"User details updated." in edit_response.data

    stale_session_response = user_client.get("/dashboard", follow_redirects=False)
    assert stale_session_response.status_code == 302
    assert "/login" in stale_session_response.headers["Location"]

    fresh_login_response = login_with_password(
        user_client,
        "session-role@example.com",
        "role-password-1",
    )
    assert fresh_login_response.status_code == 302
    assert fresh_login_response.headers["Location"].endswith("/dashboard")


def test_password_action_links_use_configured_app_base_url(app):
    app.config["APP_BASE_URL"] = "https://inventory.example.org"

    with app.app_context():
        admin = create_user(
            email="base-url-admin@example.com",
            role="admin",
            display_name="Base Admin",
        )
        db.session.commit()

        with app.test_request_context(base_url="http://localhost"):
            _user, issued_link = create_managed_user(
                actor=admin,
                email="base-url-user@example.com",
                display_name="Base User",
                role="viewer",
            )

    assert issued_link.url.startswith("https://inventory.example.org/reset-password/")


def test_login_page_emits_security_headers(client):
    response = client.get("/login")

    assert response.status_code == 200
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert "object-src 'none'" in response.headers["Content-Security-Policy"]
    assert "script-src 'self' 'nonce-" in response.headers["Content-Security-Policy"]
    assert "script-src 'self' 'unsafe-inline'" not in response.headers["Content-Security-Policy"]
    assert b'<script nonce="' in response.data
    assert b"data-password-toggle=\"password\"" in response.data


def test_dev_quick_login_can_be_disabled_by_config(client, app):
    app.config["AUTH_ALLOW_DEV_QUICK_LOGIN"] = False

    response = quick_login(client, "admin")

    assert response.status_code == 401
    assert b"Invalid credentials or account unavailable." in response.data
