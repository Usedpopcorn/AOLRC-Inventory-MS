import csv
import io
from datetime import datetime, timedelta, timezone

from app import db
from app.models import (
    CountLine,
    CountSession,
    Item,
    SupplyNote,
    User,
    Venue,
    VenueItem,
    VenueItemCount,
    VenueNote,
)


def quick_login(client, role="admin"):
    quick_role = "user" if role == "viewer" else role
    return client.post(
        "/login",
        data={"quick_login_role": quick_role},
        follow_redirects=False,
    )


def parse_csv_response(response):
    text = response.data.decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


def create_tracked_item(
    venue,
    name,
    *,
    tracking_mode="quantity",
    item_type="consumable",
    item_category=None,
    default_par_level=None,
    venue_par_override=None,
    setup_group_code=None,
    setup_group_label=None,
    active=True,
):
    item = Item(
        name=name,
        item_type=item_type,
        tracking_mode=tracking_mode,
        item_category=(
            item_category
            or ("durable" if tracking_mode == "singleton_asset" else "consumable")
        ),
        active=active,
        default_par_level=default_par_level,
        setup_group_code=setup_group_code,
        setup_group_label=setup_group_label,
        created_at=datetime.now(timezone.utc),
    )
    db.session.add(item)
    db.session.flush()
    db.session.add(
        VenueItem(
            venue_id=venue.id,
            item_id=item.id,
            active=True,
            expected_qty=venue_par_override,
        )
    )
    db.session.flush()
    return item


def add_status_check(venue, item_status_pairs, *, created_at):
    from app.models import Check, CheckLine

    check = Check(venue_id=venue.id, user_id=None, created_at=created_at)
    db.session.add(check)
    db.session.flush()
    for item, status in item_status_pairs:
        db.session.add(CheckLine(check_id=check.id, item_id=item.id, status=status))
    db.session.flush()
    return check


def add_count_session(venue, item_count_pairs, *, created_at):
    session = CountSession(venue_id=venue.id, user_id=None, created_at=created_at)
    db.session.add(session)
    db.session.flush()
    for item, raw_count in item_count_pairs:
        db.session.add(
            CountLine(
                count_session_id=session.id,
                item_id=item.id,
                raw_count=raw_count,
            )
        )
        current = VenueItemCount.query.filter_by(venue_id=venue.id, item_id=item.id).first()
        if current:
            current.raw_count = raw_count
            current.updated_at = created_at
        else:
            db.session.add(
                VenueItemCount(
                    venue_id=venue.id,
                    item_id=item.id,
                    raw_count=raw_count,
                    updated_at=created_at,
                )
            )
    db.session.flush()
    return session


def test_viewer_can_export_live_venue_inventory_filtered_and_full(client, app):
    quick_login(client, "viewer")

    with app.app_context():
        note_author = User.query.filter_by(role="viewer").order_by(User.id.asc()).first()
        assert note_author is not None
        venue = Venue(name="Ananda Hall", active=True)
        db.session.add(venue)
        db.session.flush()

        tea_lights = create_tracked_item(
            venue,
            "Tea Lights",
            default_par_level=9,
            setup_group_code="02",
            setup_group_label="Standard Signature Set-up Needs",
        )
        projector = create_tracked_item(
            venue,
            "Projector",
            tracking_mode="singleton_asset",
            item_type="durable",
            item_category="durable",
        )
        add_status_check(
            venue,
            [
                (tea_lights, "low"),
                (projector, "out"),
            ],
            created_at=datetime.now(timezone.utc) - timedelta(hours=4),
        )
        add_count_session(
            venue,
            [(tea_lights, 4)],
            created_at=datetime.now(timezone.utc) - timedelta(hours=2),
        )
        db.session.add(
            VenueNote(
                venue_id=venue.id,
                author_user_id=note_author.id,
                item_id=tea_lights.id,
                title="Tea note",
                body="Counted low",
            )
        )
        db.session.commit()
        venue_id = venue.id

    filtered_response = client.get(
        f"/venues/{venue_id}/inventory/export.csv?inventory_q=tea&inventory_filter=quantity"
    )
    full_response = client.get(
        f"/venues/{venue_id}/inventory/export.csv?inventory_q=tea&inventory_filter=quantity&scope=full"
    )

    filtered_rows = parse_csv_response(filtered_response)
    full_rows = parse_csv_response(full_response)

    assert filtered_response.status_code == 200
    assert filtered_rows == [
        {
            "Venue Name": "Ananda Hall",
            "Item Name": "Tea Lights",
            "Setup Group Code": "02",
            "Setup Group Label": "Standard Signature Set-up Needs",
            "Tracking Mode": "Quantity",
            "Category": "consumable",
            "Current Count": "4",
            "Effective Par": "9",
            "Suggested Order Qty": "5",
            "Over Par Qty": "0",
            "Current Quick Status / Last Saved Status": "Low",
            "Last Updated": filtered_rows[0]["Last Updated"],
            "Checked By": "",
            "Effective Stale Threshold": "2",
            "Is Stale": "No",
            "Note Count": "1",
        }
    ]
    assert full_response.status_code == 200
    assert len(full_rows) == 2


def test_viewer_can_export_filtered_and_full_supplies_audit(client, app):
    quick_login(client, "viewer")

    with app.app_context():
        note_author = User.query.filter_by(role="viewer").order_by(User.id.asc()).first()
        assert note_author is not None
        north_hall = Venue(name="North Hall", active=True)
        south_hall = Venue(name="South Hall", active=True)
        db.session.add_all([north_hall, south_hall])
        db.session.flush()

        tea_lights = create_tracked_item(
            north_hall,
            "Tea Lights",
            default_par_level=10,
            venue_par_override=10,
            setup_group_code="02",
            setup_group_label="Standard Signature Set-up Needs",
        )
        db.session.add(
            VenueItem(
                venue_id=south_hall.id,
                item_id=tea_lights.id,
                active=True,
                expected_qty=8,
            )
        )

        blankets = create_tracked_item(
            north_hall,
            "Blankets",
            default_par_level=6,
            setup_group_code="05",
            setup_group_label="Misc.",
        )
        add_status_check(
            north_hall,
            [(tea_lights, "low"), (blankets, "good")],
            created_at=datetime.now(timezone.utc) - timedelta(hours=5),
        )
        add_status_check(
            south_hall,
            [(tea_lights, "good")],
            created_at=datetime.now(timezone.utc) - timedelta(hours=4),
        )
        add_count_session(
            north_hall,
            [(tea_lights, 4), (blankets, 6)],
            created_at=datetime.now(timezone.utc) - timedelta(hours=2),
        )
        add_count_session(
            south_hall,
            [(tea_lights, 1)],
            created_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        db.session.add(
            SupplyNote(
                item_id=tea_lights.id,
                author_user_id=note_author.id,
                title="Tea supply note",
                body="Order more",
            )
        )
        db.session.commit()

    filtered_response = client.get("/supplies/export.csv?q=tea")
    full_response = client.get("/supplies/export.csv?q=tea&scope=full")

    filtered_rows = parse_csv_response(filtered_response)
    full_rows = parse_csv_response(full_response)

    assert filtered_response.status_code == 200
    assert len(filtered_rows) == 2
    assert {row["Venue Name"] for row in filtered_rows} == {"North Hall", "South Hall"}
    assert {row["Item Name"] for row in filtered_rows} == {"Tea Lights"}
    assert {row["Suggested Order Qty"] for row in filtered_rows} == {"6", "7"}
    assert {row["Note Count"] for row in filtered_rows} == {"1"}

    assert full_response.status_code == 200
    assert len(full_rows) == 3
    assert {row["Item Name"] for row in full_rows} == {"Tea Lights", "Blankets"}


def test_item_catalog_export_supports_filtered_and_full_views(client, app):
    quick_login(client, "admin")

    with app.app_context():
        backjacks = Item(
            name="Backjacks",
            item_type="durable",
            item_category="durable",
            tracking_mode="quantity",
            active=True,
            setup_group_code="01",
            setup_group_label="Yoga/Meditation Materials",
            default_par_level=12,
            stale_threshold_days=3,
            created_at=datetime.now(timezone.utc),
        )
        projector = Item(
            name="Projector",
            item_type="durable",
            item_category="durable",
            tracking_mode="singleton_asset",
            active=False,
            created_at=datetime.now(timezone.utc),
        )
        db.session.add_all([backjacks, projector])
        db.session.commit()

    filtered_response = client.get(
        "/admin/items/export.csv?q=back&status=active&structure=direct"
    )
    full_response = client.get(
        "/admin/items/export.csv?q=back&status=active&structure=direct&scope=full"
    )

    filtered_rows = parse_csv_response(filtered_response)
    full_rows = parse_csv_response(full_response)

    assert filtered_response.status_code == 200
    assert filtered_rows == [
        {
            "Item Name": "Backjacks",
            "Active": "Yes",
            "Tracking Mode": "Quantity",
            "Category": "durable",
            "Parent Item": "",
            "Setup Group Code": "01",
            "Setup Group Label": "Yoga/Meditation Materials",
            "Default Par": "12",
            "Item Stale Override": "3",
            "Created At": filtered_rows[0]["Created At"],
        }
    ]
    assert full_response.status_code == 200
    assert len(full_rows) == 2


def test_item_catalog_export_is_admin_only(client):
    quick_login(client, "viewer")

    response = client.get("/admin/items/export.csv", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard")
