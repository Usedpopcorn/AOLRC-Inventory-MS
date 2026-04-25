import json
from datetime import datetime, timedelta, timezone

from app import db
from app.models import InventoryAdminEvent, InventoryPolicy, Item, Venue, VenueItem
from app.services.admin_hub import build_admin_history_view_model
from app.services.venue_profile import build_venue_profile_view_model


def quick_login(client, role="admin"):
    return client.post(
        "/login",
        data={"quick_login_role": role},
        follow_redirects=False,
    )


def create_direct_item(
    name,
    *,
    default_par_level=None,
    stale_threshold_days=None,
    setup_group_code=None,
    setup_group_label=None,
    created_at=None,
):
    item = Item(
        name=name,
        item_type="consumable",
        tracking_mode="quantity",
        item_category="consumable",
        active=True,
        default_par_level=default_par_level,
        stale_threshold_days=stale_threshold_days,
        setup_group_code=setup_group_code,
        setup_group_label=setup_group_label,
        created_at=created_at or datetime.now(timezone.utc),
    )
    db.session.add(item)
    db.session.flush()
    return item


def flatten_inventory_rows(venue_profile):
    rows = {}
    for group in venue_profile["inventory_groups"]:
        if group["kind"] == "family":
            for child in group["children"]:
                rows[child["name"]] = child
        else:
            rows[group["row"]["name"]] = group["row"]
    return rows


def test_inventory_rules_route_updates_global_stale_threshold_and_logs_event(client, app):
    quick_login(client)

    response = client.post(
        "/admin/inventory-rules",
        data={"default_stale_threshold_days": "5"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/inventory-rules")

    with app.app_context():
        policy = InventoryPolicy.query.first()
        event = InventoryAdminEvent.query.filter_by(event_type="inventory_policy_updated").first()

    assert policy is not None
    assert policy.default_stale_threshold_days == 5
    assert event is not None
    assert json.loads(event.details_json)["new_threshold_days"] == 5


def test_item_create_assigns_venues_and_defaults(client, app):
    quick_login(client)

    with app.app_context():
        venue_one = Venue(name="Mandala Hall", active=True)
        venue_two = Venue(name="Lotus Lounge", active=True)
        db.session.add_all([venue_one, venue_two])
        db.session.commit()
        venue_one_id = venue_one.id
        venue_two_id = venue_two.id

    response = client.post(
        "/admin/items",
        data={
            "name": "Hand Towels",
            "tracking_mode": "quantity",
            "item_category": "consumable",
            "parent_item_id": "",
            "active": "1",
            "unit": "bundle",
            "sort_order": "1",
            "default_par_level": "12",
            "stale_threshold_days": "4",
            "venue_ids": [str(venue_one_id), str(venue_two_id)],
        },
        follow_redirects=False,
    )

    assert response.status_code == 302

    with app.app_context():
        item = Item.query.filter_by(name="Hand Towels").first()
        links = (
            VenueItem.query.filter_by(item_id=item.id, active=True)
            .order_by(VenueItem.venue_id.asc())
            .all()
        )
        event_types = [
            event.event_type
            for event in InventoryAdminEvent.query.order_by(InventoryAdminEvent.id.asc()).all()
        ]

    assert item.default_par_level == 12
    assert item.stale_threshold_days == 4
    assert [link.venue_id for link in links] == [venue_one_id, venue_two_id]
    assert "item_created" in event_types
    assert "item_tracking_updated" in event_types


def test_item_create_saves_builtin_setup_group(client, app):
    quick_login(client)

    response = client.post(
        "/admin/items",
        data={
            "name": "Meditation Shawls",
            "tracking_mode": "quantity",
            "item_category": "consumable",
            "parent_item_id": "",
            "setup_group_selection": "01",
            "active": "1",
            "unit": "each",
            "sort_order": "0",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302

    with app.app_context():
        item = Item.query.filter_by(name="Meditation Shawls").first()

    assert item is not None
    assert item.setup_group_code == "01"
    assert item.setup_group_label == "Yoga/Meditation Materials"


def test_item_edit_updates_and_clears_setup_group(client, app):
    quick_login(client)

    with app.app_context():
        item = create_direct_item("Setup Group Item")
        db.session.commit()
        item_id = item.id

    assign_response = client.post(
        f"/admin/items/{item_id}/edit",
        data={
            "name": "Setup Group Item",
            "tracking_mode": "quantity",
            "item_category": "consumable",
            "parent_item_id": "",
            "setup_group_selection": "03",
            "active": "1",
            "unit": "",
            "sort_order": "0",
            "default_par_level": "",
            "stale_threshold_days": "",
        },
        follow_redirects=False,
    )

    assert assign_response.status_code == 302

    clear_response = client.post(
        f"/admin/items/{item_id}/edit",
        data={
            "name": "Setup Group Item",
            "tracking_mode": "quantity",
            "item_category": "consumable",
            "parent_item_id": "",
            "setup_group_selection": "",
            "active": "1",
            "unit": "",
            "sort_order": "0",
            "default_par_level": "",
            "stale_threshold_days": "",
        },
        follow_redirects=False,
    )

    assert clear_response.status_code == 302

    with app.app_context():
        item = db.session.get(Item, item_id)

    assert item.setup_group_code is None
    assert item.setup_group_label is None


def test_item_create_custom_setup_group_can_be_reused(client, app):
    quick_login(client)

    first_response = client.post(
        "/admin/items",
        data={
            "name": "A/V Cart",
            "tracking_mode": "quantity",
            "item_category": "durable",
            "parent_item_id": "",
            "setup_group_selection": "__custom__",
            "setup_group_code": "06A",
            "setup_group_label": "Audio Visual Support",
            "active": "1",
            "unit": "each",
            "sort_order": "0",
        },
        follow_redirects=False,
    )

    assert first_response.status_code == 302

    second_response = client.post(
        "/admin/items",
        data={
            "name": "Extension Cables",
            "tracking_mode": "quantity",
            "item_category": "durable",
            "parent_item_id": "",
            "setup_group_selection": "06A",
            "active": "1",
            "unit": "each",
            "sort_order": "1",
        },
        follow_redirects=False,
    )

    assert second_response.status_code == 302

    with app.app_context():
        first_item = Item.query.filter_by(name="A/V Cart").first()
        second_item = Item.query.filter_by(name="Extension Cables").first()

    assert first_item.setup_group_code == "06A"
    assert first_item.setup_group_label == "Audio Visual Support"
    assert second_item.setup_group_code == "06A"
    assert second_item.setup_group_label == "Audio Visual Support"


def test_item_create_rejects_duplicate_custom_setup_group_code(client, app):
    quick_login(client)

    response = client.post(
        "/admin/items",
        data={
            "name": "Backjacks",
            "tracking_mode": "quantity",
            "item_category": "durable",
            "parent_item_id": "",
            "setup_group_selection": "__custom__",
            "setup_group_code": "01",
            "setup_group_label": "Backjack Support",
            "active": "1",
            "unit": "each",
            "sort_order": "0",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"That setup group code already exists as" in response.data
    assert b"Item added." not in response.data

    with app.app_context():
        item = Item.query.filter_by(name="Backjacks").first()

    assert item is None


def test_item_edit_rejects_duplicate_custom_setup_group_label(client, app):
    quick_login(client)

    with app.app_context():
        create_direct_item(
            "Existing Group Seed",
            setup_group_code="06A",
            setup_group_label="Audio Visual Support",
        )
        item = create_direct_item("Backjacks")
        db.session.commit()
        item_id = item.id

    response = client.post(
        f"/admin/items/{item_id}/edit",
        data={
            "name": "Backjacks",
            "tracking_mode": "quantity",
            "item_category": "durable",
            "parent_item_id": "",
            "setup_group_selection": "__custom__",
            "setup_group_code": "07B",
            "setup_group_label": "Audio Visual Support",
            "active": "1",
            "unit": "each",
            "sort_order": "0",
            "default_par_level": "",
            "stale_threshold_days": "",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"That setup group label already exists as" in response.data
    assert b"Item updated." not in response.data

    with app.app_context():
        item = db.session.get(Item, item_id)

    assert item.setup_group_code is None
    assert item.setup_group_label is None


def test_item_edit_preserves_existing_venue_par_overrides_without_override_payload(client, app):
    quick_login(client)

    with app.app_context():
        venue = Venue(name="Editing Venue", active=True)
        item = create_direct_item("Editing Item", default_par_level=6)
        db.session.add(venue)
        db.session.flush()
        db.session.add(
            VenueItem(
                venue_id=venue.id,
                item_id=item.id,
                expected_qty=9,
                active=True,
            )
        )
        db.session.commit()
        venue_id = venue.id
        item_id = item.id

    response = client.post(
        f"/admin/items/{item_id}/edit",
        data={
            "name": "Editing Item",
            "tracking_mode": "quantity",
            "item_category": "consumable",
            "parent_item_id": "",
            "active": "1",
            "unit": "",
            "sort_order": "0",
            "default_par_level": "6",
            "stale_threshold_days": "",
            "venue_ids": [str(venue_id)],
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/items")

    with app.app_context():
        link = VenueItem.query.filter_by(
            venue_id=venue_id,
            item_id=item_id,
            active=True,
        ).first()
        tracking_event = InventoryAdminEvent.query.filter_by(
            event_type="item_tracking_updated"
        ).first()

    assert link is not None
    assert link.expected_qty == 9
    assert tracking_event is None


def test_item_create_requires_confirmation_for_similar_names(client, app):
    quick_login(client)

    with app.app_context():
        create_direct_item("Tea Towels")
        db.session.commit()

    first_response = client.post(
        "/admin/items",
        data={
            "name": "Tea Towel",
            "tracking_mode": "quantity",
            "item_category": "consumable",
            "parent_item_id": "",
            "active": "1",
            "unit": "each",
            "sort_order": "0",
        },
        follow_redirects=True,
    )

    assert first_response.status_code == 200
    assert (
        b"Similar item names already exist. Review them and submit again to confirm."
        in first_response.data
    )

    with app.app_context():
        assert Item.query.filter_by(name="Tea Towel").first() is None

    second_response = client.post(
        "/admin/items",
        data={
            "name": "Tea Towel",
            "tracking_mode": "quantity",
            "item_category": "consumable",
            "parent_item_id": "",
            "active": "1",
            "unit": "each",
            "sort_order": "0",
            "confirm_similar_name": "1",
        },
        follow_redirects=False,
    )

    assert second_response.status_code == 302

    with app.app_context():
        assert Item.query.filter_by(name="Tea Towel").first() is not None


def test_hard_delete_item_available_for_recent_item_without_history(client, app):
    quick_login(client)

    with app.app_context():
        item = create_direct_item("Disposable Setup Item")
        db.session.commit()
        item_id = item.id

    edit_response = client.get(f"/admin/items/{item_id}/edit")
    assert edit_response.status_code == 200
    assert b"Hard delete is available." in edit_response.data

    delete_response = client.post(
        f"/admin/items/{item_id}/delete",
        data={"confirm_delete_name": "Disposable Setup Item"},
        follow_redirects=False,
    )

    assert delete_response.status_code == 302
    assert delete_response.headers["Location"].endswith("/admin/items")

    with app.app_context():
        assert db.session.get(Item, item_id) is None
        assert (
            InventoryAdminEvent.query.filter_by(event_type="item_hard_deleted").first()
            is not None
        )


def test_hard_delete_item_is_blocked_outside_delete_window(client, app):
    quick_login(client)

    with app.app_context():
        item = create_direct_item(
            "Old Catalog Item",
            created_at=datetime.now(timezone.utc) - timedelta(days=31),
        )
        db.session.commit()
        item_id = item.id

    delete_response = client.post(
        f"/admin/items/{item_id}/delete",
        data={"confirm_delete_name": "Old Catalog Item"},
        follow_redirects=False,
    )

    assert delete_response.status_code == 302
    assert delete_response.headers["Location"].endswith(f"/admin/items/{item_id}/edit")

    with app.app_context():
        assert db.session.get(Item, item_id) is not None


def test_create_venue_manual_setup_assigns_items_and_stale_override(client, app):
    quick_login(client)

    with app.app_context():
        tea = create_direct_item("Tea Bags")
        towels = create_direct_item("Bath Towels")
        db.session.commit()
        tea_id = tea.id
        towels_id = towels.id

    response = client.post(
        "/venues/create",
        data={
            "name": "Crystal Hall",
            "is_core": "false",
            "stale_threshold_days": "6",
            "setup_mode": "manual",
            "item_ids": [str(tea_id), str(towels_id)],
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert "/venues/" in response.headers["Location"]
    assert "/settings" in response.headers["Location"]

    with app.app_context():
        venue = Venue.query.filter_by(name="Crystal Hall").first()
        tracked_item_ids = sorted(
            link.item_id for link in VenueItem.query.filter_by(venue_id=venue.id, active=True).all()
        )
        event_types = [
            event.event_type
            for event in InventoryAdminEvent.query.order_by(InventoryAdminEvent.id.asc()).all()
        ]

    assert venue.stale_threshold_days == 6
    assert tracked_item_ids == [tea_id, towels_id]
    assert "venue_created" in event_types
    assert "venue_tracking_updated" in event_types


def test_create_venue_copy_setup_copies_assignments_and_overrides(client, app):
    quick_login(client)

    with app.app_context():
        source = Venue(name="Source Venue", active=True)
        item = create_direct_item("Copy Me")
        db.session.add(source)
        db.session.flush()
        db.session.add(VenueItem(venue_id=source.id, item_id=item.id, expected_qty=9, active=True))
        db.session.commit()
        source_id = source.id
        item_id = item.id

    response = client.post(
        "/venues/create",
        data={
            "name": "Copied Venue",
            "is_core": "false",
            "setup_mode": "copy",
            "copy_source_venue_id": str(source_id),
        },
        follow_redirects=False,
    )

    assert response.status_code == 302

    with app.app_context():
        venue = Venue.query.filter_by(name="Copied Venue").first()
        link = VenueItem.query.filter_by(venue_id=venue.id, item_id=item_id, active=True).first()
        event = InventoryAdminEvent.query.filter_by(event_type="venue_tracking_copied").first()

    assert link is not None
    assert link.expected_qty == 9
    assert event is not None


def test_venue_settings_save_stale_override_and_tracking_overrides(client, app):
    quick_login(client)

    with app.app_context():
        venue = Venue(name="Settings Venue", active=True)
        tea = create_direct_item("Venue Tea", default_par_level=6)
        db.session.add(venue)
        db.session.commit()
        venue_id = venue.id
        tea_id = tea.id

    details_response = client.post(
        f"/venues/{venue_id}/settings",
        data={
            "action": "save",
            "name": "Settings Venue",
            "active": "true",
            "stale_threshold_days": "8",
        },
        follow_redirects=False,
    )
    assert details_response.status_code == 302

    tracking_response = client.post(
        f"/venues/{venue_id}/settings",
        data={
            "action": "save_tracking",
            "item_ids": [str(tea_id)],
            f"par_override_{tea_id}": "11",
        },
        follow_redirects=False,
    )
    assert tracking_response.status_code == 302

    with app.app_context():
        venue = db.session.get(Venue, venue_id)
        link = VenueItem.query.filter_by(venue_id=venue_id, item_id=tea_id, active=True).first()
        event_types = [
            event.event_type
            for event in InventoryAdminEvent.query.order_by(InventoryAdminEvent.id.asc()).all()
        ]

    assert venue.stale_threshold_days == 8
    assert link is not None
    assert link.expected_qty == 11
    assert "venue_updated" in event_types
    assert "venue_tracking_updated" in event_types


def test_bulk_tracking_setup_updates_relationships_and_overrides(client, app):
    quick_login(client)

    with app.app_context():
        item = create_direct_item("Bulk Managed Item", default_par_level=4)
        venue_one = Venue(name="Bulk Venue One", active=True)
        venue_two = Venue(name="Bulk Venue Two", active=True)
        db.session.add_all([venue_one, venue_two])
        db.session.commit()
        item_id = item.id
        venue_one_id = venue_one.id
        venue_two_id = venue_two.id

    response = client.post(
        "/admin/tracking-setup",
        data={
            "item_id": str(item_id),
            "venue_ids": [str(venue_one_id), str(venue_two_id)],
            f"par_override_{venue_one_id}": "7",
            f"par_override_{venue_two_id}": "",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith(f"/admin/tracking-setup?item_id={item_id}")

    with app.app_context():
        links = {
            link.venue_id: link
            for link in VenueItem.query.filter_by(item_id=item_id, active=True).all()
        }
        event = InventoryAdminEvent.query.filter_by(event_type="bulk_tracking_updated").first()

    assert set(links) == {venue_one_id, venue_two_id}
    assert links[venue_one_id].expected_qty == 7
    assert links[venue_two_id].expected_qty is None
    assert event is not None


def test_venue_profile_applies_par_and_stale_precedence(app):
    with app.app_context():
        db.session.add(InventoryPolicy(default_stale_threshold_days=7))
        venue = Venue(name="Precedence Venue", active=True, stale_threshold_days=5)
        item_with_venue_override = create_direct_item("Venue Override Item", default_par_level=10)
        item_with_item_override = create_direct_item(
            "Item Override Item",
            default_par_level=12,
            stale_threshold_days=3,
        )
        item_with_global_fallback = create_direct_item("Global Fallback Item", default_par_level=8)
        db.session.add(venue)
        db.session.flush()
        db.session.add_all(
            [
                VenueItem(
                    venue_id=venue.id,
                    item_id=item_with_venue_override.id,
                    expected_qty=14,
                    active=True,
                ),
                VenueItem(
                    venue_id=venue.id,
                    item_id=item_with_item_override.id,
                    expected_qty=None,
                    active=True,
                ),
                VenueItem(
                    venue_id=venue.id,
                    item_id=item_with_global_fallback.id,
                    expected_qty=None,
                    active=True,
                ),
            ]
        )
        db.session.commit()

        profile = build_venue_profile_view_model(venue.id)
        rows = flatten_inventory_rows(profile)

    assert rows["Venue Override Item"]["par_value"] == 14
    assert rows["Venue Override Item"]["par_source"] == "venue"
    assert rows["Venue Override Item"]["effective_stale_threshold_days"] == 5
    assert rows["Item Override Item"]["par_value"] == 12
    assert rows["Item Override Item"]["par_source"] == "item"
    assert rows["Item Override Item"]["effective_stale_threshold_days"] == 3
    assert rows["Global Fallback Item"]["effective_stale_threshold_days"] == 5


def test_admin_history_view_model_includes_inventory_admin_events(app):
    with app.app_context():
        db.session.add(
            InventoryAdminEvent(
                event_type="inventory_policy_updated",
                subject_type="inventory_policy",
                subject_label="Inventory Rules",
                details_json=json.dumps(
                    {
                        "previous_threshold_days": 2,
                        "new_threshold_days": 5,
                    }
                ),
            )
        )
        db.session.commit()

        view_model = build_admin_history_view_model()

    assert view_model["inventory_admin_events"]["preview"][0]["title"] == "Updated inventory rules"
