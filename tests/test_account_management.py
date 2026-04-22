import re
from datetime import datetime, timedelta, timezone

from werkzeug.security import generate_password_hash

from app import db
from app.models import AccountAuditEvent, PasswordActionToken, User
from app.services.account_security import (
    AccountManagementError,
    create_managed_user,
    set_user_active_state,
    update_managed_user,
)
from app.services.admin_hub import build_admin_history_view_model

RESET_LINK_RE = re.compile(r"http://localhost/reset-password/([A-Za-z0-9_\-]+)")
FORGOT_PASSWORD_FLASH = b"If an account matches that email, password reset instructions are ready."


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
    force_password_change=False,
    locked_until=None,
    failed_login_attempts=0,
):
    now = datetime.now(timezone.utc)
    user = User(
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
    db.session.add(user)
    db.session.flush()
    return user


def extract_reset_path(response_text):
    match = RESET_LINK_RE.search(response_text)
    assert match is not None
    return f"/reset-password/{match.group(1)}"


def login_with_password(client, email, password, *, next_path=None):
    return client.post(
        "/login",
        data={
            "email": email,
            "password": password,
            "next": next_path or "",
        },
        follow_redirects=False,
    )


def test_admin_can_create_user_from_users_page(client, app):
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
    assert b"Created account for managed@example.com." in response.data
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
    assert b"/reset-password/" in reset_response.data

    with app.app_context():
        user = db.session.get(User, user_id)
        assert user.display_name == "Support Lead"
        assert user.role == "staff"
        assert user.active is True
        assert user.locked_until is None
        assert user.failed_login_attempts == 0


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
        assert blocked_login.status_code == 403
        assert b"Your account is inactive. Contact an admin." in blocked_login.data

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
            assert b"Invalid email or password." in invalid_response.data

        locked_response = login_with_password(
            user_client,
            "lock-chain@example.com",
            "lock-password-1",
        )
        assert locked_response.status_code == 429
        assert b"Too many failed attempts." in locked_response.data

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


def test_dev_quick_login_can_be_disabled_by_config(client, app):
    app.config["AUTH_ALLOW_DEV_QUICK_LOGIN"] = False

    response = quick_login(client, "admin")

    assert response.status_code == 401
    assert b"Invalid email or password." in response.data
