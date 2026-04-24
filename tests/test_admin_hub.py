from datetime import datetime, timedelta, timezone

from werkzeug.security import generate_password_hash

from app import db
from app.models import Check, CheckLine, CountLine, CountSession, Item, User, Venue, VenueNote
from app.services.admin_hub import (
    build_admin_history_view_model,
    build_admin_overview_view_model,
    build_admin_user_audit_view_model,
    build_admin_user_list_view_model,
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
    role="viewer",
    active=True,
    display_name=None,
    created_at=None,
    locked_until=None,
    force_password_change=False,
    password_changed_at=None,
    last_login_at=None,
):
    created_at = created_at or datetime.now(timezone.utc)
    user = User(
        email=email,
        display_name=display_name,
        password_hash=generate_password_hash("local-test-password"),
        role=role,
        active=active,
        created_at=created_at,
        locked_until=locked_until,
        force_password_change=force_password_change,
        password_changed_at=password_changed_at or created_at,
        last_login_at=last_login_at,
    )
    db.session.add(user)
    db.session.flush()
    return user


def test_admin_hub_routes_accessible_for_admin(client):
    login_response = quick_login(client, "admin")

    assert login_response.status_code == 302

    for path in (
        "/admin",
        "/admin/users",
        "/admin/inventory-rules",
        "/admin/items",
        "/admin/tracking-setup",
        "/admin/audit/users",
        "/admin/history",
    ):
        response = client.get(path)
        assert response.status_code == 200


def test_admin_nav_points_to_admin_overview(client):
    quick_login(client, "admin")

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert b'href="/admin"' in response.data


def test_admin_hub_routes_redirect_unauthenticated(client):
    for path in (
        "/admin",
        "/admin/users",
        "/admin/inventory-rules",
        "/admin/items",
        "/admin/tracking-setup",
        "/admin/audit/users",
        "/admin/history",
    ):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]


def test_admin_hub_routes_block_non_admin_users(client):
    for role in ("staff", "user"):
        quick_login(client, role)
        for path in (
            "/admin",
            "/admin/users",
            "/admin/inventory-rules",
            "/admin/tracking-setup",
            "/admin/audit/users",
            "/admin/history",
            "/admin/items",
        ):
            response = client.get(path, follow_redirects=False)
            assert response.status_code == 302
            assert response.headers["Location"].endswith("/dashboard")
        client.post("/logout")


def test_admin_overview_view_model_counts_locked_users(app):
    with app.app_context():
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        create_user(email="admin1@example.com", role="admin", display_name="Admin One")
        create_user(email="staff1@example.com", role="staff", display_name="Staff One")
        create_user(
            email="viewer1@example.com",
            role="viewer",
            active=False,
            display_name="Viewer One",
        )
        create_user(
            email="locked@example.com",
            role="viewer",
            display_name="Locked User",
            locked_until=now + timedelta(minutes=30),
        )
        db.session.commit()

        overview = build_admin_overview_view_model()

    assert overview["summary"]["total_users"] == 4
    assert overview["summary"]["active_users"] == 3
    assert overview["summary"]["inactive_users"] == 1
    assert overview["summary"]["locked_users"] == 1
    assert overview["locked_users"]["preview"][0]["email"] == "locked@example.com"


def test_admin_overview_need_attention_count_deduplicates_locked_inactive_users(app):
    with app.app_context():
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        create_user(
            email="overlap@example.com",
            role="viewer",
            active=False,
            display_name="Overlap User",
            locked_until=now + timedelta(minutes=20),
        )
        db.session.commit()

        overview = build_admin_overview_view_model()

    assert overview["summary"]["inactive_users"] == 1
    assert overview["summary"]["locked_users"] == 1
    assert overview["summary"]["attention_users"] == 1
    assert overview["module_summary"]["audit"]["primary_value"] == 1


def test_user_list_view_model_exposes_account_state(app):
    with app.app_context():
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        create_user(
            email="active@example.com",
            role="staff",
            active=True,
            display_name="Active Staff",
        )
        create_user(
            email="inactive@example.com",
            role="viewer",
            active=False,
            display_name="Inactive Viewer",
        )
        create_user(
            email="locked@example.com",
            role="viewer",
            active=True,
            display_name="Locked Viewer",
            locked_until=now + timedelta(minutes=15),
        )
        db.session.commit()

        view_model = build_admin_user_list_view_model()
        rows_by_email = {row["email"]: row for row in view_model["rows"]}

    assert rows_by_email["active@example.com"]["active_label"] == "Active"
    assert rows_by_email["inactive@example.com"]["active_label"] == "Inactive"
    assert rows_by_email["locked@example.com"]["locked_label"] == "Locked"
    assert rows_by_email["locked@example.com"]["locked_until_text"] != "Not locked"
    assert view_model["pagination"]["total_count"] == 3


def test_user_list_view_model_paginates_large_directory(app):
    with app.app_context():
        base_time = datetime.now(timezone.utc)
        for idx in range(15):
            create_user(
                email=f"paged-{idx}@example.com",
                role="viewer",
                display_name=f"Paged User {idx}",
                created_at=base_time - timedelta(minutes=idx),
            )
        db.session.commit()

        view_model = build_admin_user_list_view_model(page=2, per_page=12)

    assert len(view_model["rows"]) == 3
    assert view_model["pagination"]["current_page"] == 2
    assert view_model["pagination"]["total_pages"] == 2
    assert view_model["pagination"]["has_prev"] is True
    assert view_model["pagination"]["has_next"] is False


def test_user_audit_view_model_scores_operational_activity(app):
    with app.app_context():
        now = datetime.now(timezone.utc)
        venue = Venue(name="Dharma Hall", active=True, created_at=now)
        item = Item(
            name="Tea Box",
            item_type="consumable",
            tracking_mode="quantity",
            item_category="consumable",
            active=True,
            created_at=now,
        )
        db.session.add_all([venue, item])
        db.session.flush()

        power_user = create_user(
            email="power@example.com",
            role="staff",
            display_name="Power User",
            created_at=now - timedelta(days=5),
        )
        create_user(
            email="quiet@example.com",
            role="viewer",
            display_name="Quiet User",
            created_at=now - timedelta(days=5),
        )
        note_user = create_user(
            email="note@example.com",
            role="viewer",
            display_name="Note User",
            created_at=now - timedelta(days=5),
        )
        db.session.flush()

        check = Check(venue_id=venue.id, user_id=power_user.id, created_at=now - timedelta(days=1))
        db.session.add(check)
        db.session.flush()
        db.session.add(CheckLine(check_id=check.id, item_id=item.id, status="good"))

        count_session = CountSession(
            venue_id=venue.id,
            user_id=power_user.id,
            created_at=now - timedelta(hours=20),
        )
        db.session.add(count_session)
        db.session.flush()
        db.session.add(CountLine(count_session_id=count_session.id, item_id=item.id, raw_count=4))

        db.session.add(
            VenueNote(
                venue_id=venue.id,
                author_user_id=power_user.id,
                title="Power note",
                body="Detailed follow-up",
                created_at=now - timedelta(hours=10),
                updated_at=now - timedelta(hours=9),
            )
        )
        db.session.add(
            VenueNote(
                venue_id=venue.id,
                author_user_id=note_user.id,
                title="Note only",
                body="One note",
                created_at=now - timedelta(hours=8),
                updated_at=now - timedelta(hours=8),
            )
        )
        db.session.commit()

        view_model = build_admin_user_audit_view_model()

    assert view_model["most_active_users"]["preview"][0]["email"] == "power@example.com"
    assert view_model["most_active_users"]["preview"][0]["score"] == 3
    assert view_model["least_active_users"]["preview"][0]["email"] == "quiet@example.com"
    assert view_model["recent_activity"]["preview"]


def test_history_view_model_handles_partial_retained_data(app):
    with app.app_context():
        now = datetime.now(timezone.utc)
        venue = Venue(name="Archive Venue", active=False, created_at=now - timedelta(days=2))
        item = Item(
            name="Archive Item",
            item_type="consumable",
            tracking_mode="quantity",
            item_category="consumable",
            active=False,
            created_at=now - timedelta(days=1),
        )
        db.session.add_all([venue, item])
        db.session.flush()

        author = create_user(
            email="history@example.com",
            role="admin",
            display_name="History Author",
            active=False,
            created_at=now - timedelta(days=3),
        )
        db.session.flush()

        db.session.add(
            VenueNote(
                venue_id=venue.id,
                author_user_id=author.id,
                title="History note",
                body="Retained note",
                created_at=now - timedelta(hours=4),
                updated_at=now - timedelta(hours=2),
            )
        )
        db.session.commit()

        view_model = build_admin_history_view_model()

    assert view_model["inventory_changes"]["loaded_count"] == 0
    assert view_model["note_updates"]["preview"][0]["title"].startswith("Edited venue note")
    assert view_model["archive"]["users"]["preview"][0]["email"] == "history@example.com"
    assert view_model["archive"]["venues"]["preview"][0]["name"] == "Archive Venue"
    assert view_model["archive"]["items"]["preview"][0]["name"] == "Archive Item"


def test_item_management_redirects_remain_intact(client, app):
    quick_login(client, "admin")

    create_response = client.post(
        "/admin/items",
        data={
            "name": "Regression Item",
            "tracking_mode": "quantity",
            "item_category": "consumable",
            "parent_item_id": "",
            "active": "1",
            "unit": "box",
            "sort_order": "1",
        },
        follow_redirects=False,
    )

    assert create_response.status_code == 302
    assert create_response.headers["Location"].endswith("/admin/items")

    with app.app_context():
        item = Item.query.filter_by(name="Regression Item").first()
        item_id = item.id

    edit_response = client.post(
        f"/admin/items/{item_id}/edit",
        data={
            "name": "Regression Item Updated",
            "tracking_mode": "quantity",
            "item_category": "consumable",
            "parent_item_id": "",
            "active": "1",
            "unit": "case",
            "sort_order": "2",
        },
        follow_redirects=False,
    )

    assert edit_response.status_code == 302
    assert edit_response.headers["Location"].endswith("/admin/items")

    confirm_response = client.get(f"/admin/items/{item_id}/deactivate")
    assert confirm_response.status_code == 200

    deactivate_response = client.post(
        f"/admin/items/{item_id}/deactivate",
        follow_redirects=False,
    )
    assert deactivate_response.status_code == 302
    assert deactivate_response.headers["Location"].endswith("/admin/items")

    activate_response = client.post(
        f"/admin/items/{item_id}/activate",
        follow_redirects=False,
    )
    assert activate_response.status_code == 302
    assert activate_response.headers["Location"].endswith("/admin/items")


def test_item_catalog_route_supports_search_and_filters(client, app):
    quick_login(client, "admin")

    with app.app_context():
        family = Item(
            name="Filtered Family",
            item_type="consumable",
            tracking_mode="quantity",
            item_category="consumable",
            is_group_parent=True,
            active=True,
        )
        inactive_direct = Item(
            name="Filter Match Archive Bin",
            item_type="durable",
            tracking_mode="quantity",
            item_category="office",
            active=False,
        )
        active_direct = Item(
            name="Should Not Match Active Mat",
            item_type="durable",
            tracking_mode="quantity",
            item_category="durable",
            active=True,
        )
        db.session.add_all([family, inactive_direct, active_direct])
        db.session.commit()

    response = client.get("/admin/items?q=archive&status=inactive&structure=direct")

    assert response.status_code == 200
    assert b"Showing 1-1 of 1 item" in response.data
    assert b"Filter Match Archive Bin" in response.data
    assert b"Should Not Match Active Mat" not in response.data


def test_item_catalog_partial_route_returns_live_results_payload(client, app):
    quick_login(client, "admin")

    with app.app_context():
        db.session.add(
            Item(
                name="Live Filter Towels",
                item_type="consumable",
                tracking_mode="quantity",
                item_category="consumable",
                active=True,
            )
        )
        db.session.commit()

    response = client.get(
        "/admin/items?catalog_partial=1&q=towels",
        headers={
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert "Live Filter Towels" in payload["html"]
    assert payload["pagination"]["current_page"] == 1
    assert payload["pagination"]["total_count"] == 1


def test_item_catalog_route_paginates_large_results(client, app):
    quick_login(client, "admin")

    with app.app_context():
        for idx in range(55):
            db.session.add(
                Item(
                    name=f"Paged Item {idx:02d}",
                    item_type="consumable",
                    tracking_mode="quantity",
                    item_category="consumable",
                    active=True,
                    sort_order=idx,
                )
            )
        db.session.commit()

    response = client.get("/admin/items?page=2")

    assert response.status_code == 200
    assert b"Page 2 of 2" in response.data
    assert b"Paged Item 54" in response.data
    assert b"Paged Item 00" not in response.data
