from datetime import datetime, timedelta, timezone

from app import db
from app.models import (
    Check,
    CheckLine,
    CountLine,
    CountSession,
    Item,
    Venue,
    VenueItem,
    VenueItemCount,
)
from app.routes.main import build_restock_rows


def quick_login(client, role="staff"):
    return client.post(
        "/login",
        data={"quick_login_role": role},
        follow_redirects=False,
    )


def create_tracked_item(
    venue,
    name,
    *,
    tracking_mode="quantity",
    item_type="consumable",
    item_category=None,
    sort_order=0,
    default_par_level=None,
    venue_par_override=None,
    setup_group_code=None,
    setup_group_label=None,
):
    item = Item(
        name=name,
        item_type=item_type,
        tracking_mode=tracking_mode,
        item_category=(
            item_category
            or ("durable" if tracking_mode == "singleton_asset" else "consumable")
        ),
        active=True,
        sort_order=sort_order,
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


def test_dashboard_restocking_defaults_to_status_mode(client, app):
    quick_login(client, "staff")

    with app.app_context():
        venue = Venue(name="Willow Hall", active=True)
        db.session.add(venue)
        db.session.flush()
        item = create_tracked_item(venue, "Tea Bags", default_par_level=8)
        add_status_check(
            venue,
            [(item, "low")],
            created_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        add_count_session(
            venue,
            [(item, 2)],
            created_at=datetime.now(timezone.utc) - timedelta(hours=12),
        )
        db.session.commit()

    response = client.get("/dashboard?tab=restocking")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'id="restockMode" value="status"' in body
    assert 'data-restock-mode-toggle="status"' in body
    assert 'id="restockModeColumnHeader">Status<' in body
    assert 'data-item-name="tea bags"' in body
    assert "ui-chip-warning ui-chip-compact restock-count-chip" not in body


def test_dashboard_restocking_counts_mode_renders_count_first_summary_and_links(client, app):
    quick_login(client, "staff")

    with app.app_context():
        venue = Venue(name="Foxfire Lodge", active=True)
        db.session.add(venue)
        db.session.flush()
        quantity_item = create_tracked_item(
            venue,
            "Lantern Fuel",
            default_par_level=8,
            sort_order=1,
        )
        asset_item = create_tracked_item(
            venue,
            "Projector",
            tracking_mode="singleton_asset",
            item_type="durable",
            item_category="durable",
            sort_order=2,
        )
        add_status_check(
            venue,
            [(quantity_item, "low"), (asset_item, "good")],
            created_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        add_count_session(
            venue,
            [(quantity_item, 2)],
            created_at=datetime.now(timezone.utc) - timedelta(hours=8),
        )
        db.session.commit()

    response = client.get("/dashboard?tab=restocking&restock_mode=counts")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    compact_body = " ".join(body.split())
    assert 'id="restockMode" value="counts"' in body
    assert 'id="restockModeColumnHeader">Count<' in compact_body
    assert "restock-count-chip" in body
    assert "2 / 8" in compact_body
    assert "mode=raw_counts" in body
    assert "mode=status" in body


def test_dashboard_restocking_counts_mode_keeps_status_filter_behavior(client, app):
    quick_login(client, "staff")

    with app.app_context():
        venue = Venue(name="Birch Studio", active=True)
        db.session.add(venue)
        db.session.flush()
        low_status_item = create_tracked_item(
            venue,
            "Candles",
            default_par_level=8,
            sort_order=1,
        )
        good_status_item = create_tracked_item(
            venue,
            "Matches",
            default_par_level=8,
            sort_order=2,
        )
        add_status_check(
            venue,
            [(low_status_item, "low"), (good_status_item, "good")],
            created_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        add_count_session(
            venue,
            [(low_status_item, 8), (good_status_item, 0)],
            created_at=datetime.now(timezone.utc) - timedelta(hours=6),
        )
        db.session.commit()

    response = client.get(
        "/dashboard?tab=restocking&restock_mode=counts&restock_status_submitted=1&restock_status=low"
    )

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'data-item-name="candles"' in body
    assert 'data-item-name="matches"' not in body


def test_build_restock_rows_priority_switches_between_status_and_count_modes(app):
    with app.app_context():
        venue = Venue(name="Maple House", active=True)
        db.session.add(venue)
        db.session.flush()
        count_priority_item = create_tracked_item(
            venue,
            "Aprons",
            default_par_level=8,
            sort_order=1,
        )
        status_priority_item = create_tracked_item(
            venue,
            "Bowls",
            default_par_level=8,
            sort_order=2,
        )
        add_status_check(
            venue,
            [(count_priority_item, "good"), (status_priority_item, "out")],
            created_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        add_count_session(
            venue,
            [(count_priority_item, 0), (status_priority_item, 7)],
            created_at=datetime.now(timezone.utc) - timedelta(hours=6),
        )
        db.session.commit()

        status_mode_rows = build_restock_rows(sort="status_priority", mode="status")["rows"]
        counts_mode_rows = build_restock_rows(sort="status_priority", mode="counts")["rows"]

    assert status_mode_rows[0]["item_name"] == "Bowls"
    assert counts_mode_rows[0]["item_name"] == "Aprons"


def test_build_restock_rows_status_priority_places_not_checked_before_good(app):
    with app.app_context():
        venue = Venue(name="Fir House", active=True)
        db.session.add(venue)
        db.session.flush()
        good_item = create_tracked_item(
            venue,
            "A - Good Item",
            default_par_level=4,
            sort_order=1,
        )
        unchecked_item = create_tracked_item(
            venue,
            "Z - Not Checked Item",
            default_par_level=4,
            sort_order=2,
        )
        add_status_check(
            venue,
            [(good_item, "good"), (unchecked_item, "not_checked")],
            created_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        db.session.commit()

        status_mode_rows = build_restock_rows(sort="status_priority", mode="status")["rows"]

    assert [row["item_name"] for row in status_mode_rows[:2]] == [
        "Z - Not Checked Item",
        "A - Good Item",
    ]


def test_build_restock_rows_counts_mode_orders_noncomparable_rows_after_comparable(app):
    with app.app_context():
        venue = Venue(name="Cedar Lodge", active=True)
        db.session.add(venue)
        db.session.flush()
        comparable_item = create_tracked_item(
            venue,
            "Candles",
            default_par_level=8,
            sort_order=1,
        )
        no_count_item = create_tracked_item(
            venue,
            "Lanterns",
            default_par_level=5,
            sort_order=2,
        )
        no_par_item = create_tracked_item(
            venue,
            "Soap",
            sort_order=3,
        )
        singleton_item = create_tracked_item(
            venue,
            "Projector",
            tracking_mode="singleton_asset",
            item_type="durable",
            item_category="durable",
            sort_order=4,
        )
        add_status_check(
            venue,
            [
                (comparable_item, "low"),
                (no_count_item, "ok"),
                (no_par_item, "good"),
                (singleton_item, "out"),
            ],
            created_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        add_count_session(
            venue,
            [(comparable_item, 1), (no_par_item, 2)],
            created_at=datetime.now(timezone.utc) - timedelta(hours=6),
        )
        db.session.commit()

        counts_mode_rows = build_restock_rows(sort="status_priority", mode="counts")["rows"]

    assert [row["item_name"] for row in counts_mode_rows[:4]] == [
        "Candles",
        "Lanterns",
        "Soap",
        "Projector",
    ]


def test_build_restock_rows_counts_mode_uses_suggested_order_and_over_par_values(app):
    with app.app_context():
        venue = Venue(name="Spruce House", active=True)
        db.session.add(venue)
        db.session.flush()
        venue_override_item = create_tracked_item(
            venue,
            "Venue Override Supply",
            default_par_level=12,
            venue_par_override=9,
            setup_group_code="02",
            setup_group_label="Standard Signature Set-up Needs",
            sort_order=1,
        )
        over_par_item = create_tracked_item(
            venue,
            "Extra Blankets",
            default_par_level=5,
            sort_order=2,
        )
        higher_suggested_item = create_tracked_item(
            venue,
            "Tea Lights",
            default_par_level=10,
            sort_order=3,
        )
        add_status_check(
            venue,
            [
                (venue_override_item, "ok"),
                (over_par_item, "good"),
                (higher_suggested_item, "ok"),
            ],
            created_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        add_count_session(
            venue,
            [
                (venue_override_item, 4),
                (over_par_item, 8),
                (higher_suggested_item, 4),
            ],
            created_at=datetime.now(timezone.utc) - timedelta(hours=4),
        )
        db.session.commit()

        counts_mode_rows = build_restock_rows(sort="status_priority", mode="counts")["rows"]
        rows_by_name = {row["item_name"]: row for row in counts_mode_rows}

    assert rows_by_name["Venue Override Supply"]["count_state"]["suggested_order_qty"] == 5
    assert rows_by_name["Venue Override Supply"]["count_state"]["over_par_qty"] == 0
    assert rows_by_name["Venue Override Supply"]["par_value"] == 9
    assert (
        rows_by_name["Venue Override Supply"]["setup_group_display"]
        == "02 - Standard Signature Set-up Needs"
    )
    assert rows_by_name["Extra Blankets"]["count_state"]["suggested_order_qty"] == 0
    assert rows_by_name["Extra Blankets"]["count_state"]["over_par_qty"] == 3
    assert [row["item_name"] for row in counts_mode_rows[:2]] == [
        "Tea Lights",
        "Venue Override Supply",
    ]


def test_dashboard_restocking_rows_api_preserves_mode_and_links(client, app):
    quick_login(client, "staff")

    with app.app_context():
        venue = Venue(name="Aspen Cabin", active=True)
        db.session.add(venue)
        db.session.flush()
        quantity_item = create_tracked_item(
            venue,
            "Napkins",
            default_par_level=12,
            sort_order=1,
        )
        singleton_item = create_tracked_item(
            venue,
            "Speaker",
            tracking_mode="singleton_asset",
            item_type="durable",
            item_category="durable",
            sort_order=2,
        )
        add_status_check(
            venue,
            [(quantity_item, "ok"), (singleton_item, "low")],
            created_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        add_count_session(
            venue,
            [(quantity_item, 9)],
            created_at=datetime.now(timezone.utc) - timedelta(hours=4),
        )
        db.session.commit()

    response = client.get("/dashboard/restocking_rows?restock_mode=counts&offset=0&limit=50")

    assert response.status_code == 200
    payload = response.get_json()
    rows_by_name = {row["item_name"]: row for row in payload["rows"]}
    assert rows_by_name["Napkins"]["count_state"]["text"] == "9 / 12"
    assert rows_by_name["Napkins"]["count_state"]["suggested_order_qty"] == 3
    assert rows_by_name["Napkins"]["count_state"]["over_par_qty"] == 0
    assert rows_by_name["Napkins"]["quick_check_mode"] == "raw_counts"
    assert "mode=raw_counts" in rows_by_name["Napkins"]["quick_check_url"]
    assert rows_by_name["Speaker"]["quick_check_mode"] == "status"
    assert "mode=status" in rows_by_name["Speaker"]["quick_check_url"]
