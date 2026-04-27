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
from app.routes.main import build_venue_rows


def quick_login(client, role="viewer"):
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
    default_par_level=None,
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
        default_par_level=default_par_level,
        created_at=datetime.now(timezone.utc),
    )
    db.session.add(item)
    db.session.flush()
    db.session.add(
        VenueItem(
            venue_id=venue.id,
            item_id=item.id,
            active=True,
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


def get_venue_row(rows, venue_id):
    return next(row for row in rows if row["venue"].id == venue_id)


def test_build_venue_rows_uses_latest_status_per_item_across_partial_checks(app):
    with app.app_context():
        venue = Venue(name="Mismatch Hall", active=True)
        db.session.add(venue)
        db.session.flush()
        items = [
            create_tracked_item(venue, f"Mismatch Item {index}")
            for index in range(1, 6)
        ]
        earlier = datetime.now(timezone.utc) - timedelta(days=2)
        later = earlier + timedelta(hours=8)
        add_status_check(
            venue,
            [
                (items[0], "good"),
                (items[1], "good"),
                (items[2], "ok"),
                (items[3], "low"),
            ],
            created_at=earlier,
        )
        add_status_check(
            venue,
            [(items[0], "good")],
            created_at=later,
        )
        db.session.commit()

        row = get_venue_row(build_venue_rows(), venue.id)

    assert row["counts"] == {
        "good": 2,
        "ok": 1,
        "low": 1,
        "out": 0,
        "not_checked": 1,
    }
    assert row["badge"]["key"] == "low"
    assert row["badge"]["text"] == "1 item low"


def test_build_venue_rows_keeps_latest_count_per_item_across_partial_count_sessions(app):
    with app.app_context():
        venue = Venue(name="Count Rollup Hall", active=True)
        db.session.add(venue)
        db.session.flush()
        items = [
            create_tracked_item(venue, f"Count Item {index}", default_par_level=10)
            for index in range(1, 4)
        ]
        earlier = datetime.now(timezone.utc) - timedelta(days=2)
        later = earlier + timedelta(hours=6)
        add_count_session(
            venue,
            [
                (items[0], 4),
                (items[1], 3),
            ],
            created_at=earlier,
        )
        add_count_session(
            venue,
            [(items[0], 5)],
            created_at=later,
        )
        db.session.commit()

        row = get_venue_row(build_venue_rows(), venue.id)

    assert row["current_total_count"] == 8
    assert row["total_par_count"] == 30


def test_dashboard_and_venues_pages_render_status_popover_with_per_item_rollups(client, app):
    quick_login(client, "staff")

    with app.app_context():
        venue = Venue(name="Popover Hall", active=True)
        db.session.add(venue)
        db.session.flush()
        items = [
            create_tracked_item(venue, f"Popover Item {index}")
            for index in range(1, 6)
        ]
        earlier = datetime.now(timezone.utc) - timedelta(days=2)
        later = earlier + timedelta(hours=8)
        add_status_check(
            venue,
            [
                (items[0], "good"),
                (items[1], "good"),
                (items[2], "ok"),
                (items[3], "low"),
            ],
            created_at=earlier,
        )
        add_status_check(
            venue,
            [(items[0], "good")],
            created_at=later,
        )
        db.session.commit()

    dashboard_response = client.get("/dashboard?tab=venues")
    venues_response = client.get("/venues")

    for response in (dashboard_response, venues_response):
        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert 'data-status-venue="Popover Hall"' in body
        assert 'data-status-summary="1 item low"' in body
        assert 'data-count-good="2"' in body
        assert 'data-count-ok="1"' in body
        assert 'data-count-low="1"' in body
        assert 'data-count-out="0"' in body
        assert 'data-count-not-checked="1"' in body
