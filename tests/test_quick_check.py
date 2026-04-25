from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

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
from app.routes.main import build_recent_venue_activity_rows
from app.services.venue_profile import build_venue_profile_view_model


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
    unit=None,
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
        unit=unit,
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


def latest_status_timestamp(venue_id, item_id):
    row = (
        db.session.query(Check.created_at)
        .join(CheckLine, CheckLine.check_id == Check.id)
        .filter(Check.venue_id == venue_id, CheckLine.item_id == item_id)
        .order_by(Check.created_at.desc(), Check.id.desc())
        .first()
    )
    if not row:
        return None
    value = row[0]
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def latest_count_timestamp(venue_id, item_id):
    row = VenueItemCount.query.filter_by(venue_id=venue_id, item_id=item_id).first()
    if not row:
        return None
    value = row.updated_at
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def flatten_inventory_rows(view_model):
    rows = {}
    for group in view_model["inventory_groups"]:
        if group["kind"] == "family":
            for child in group["children"]:
                rows[child["name"]] = child
        else:
            rows[group["row"]["name"]] = group["row"]
    return rows


def test_quick_check_get_shows_saved_status_chip_and_blank_pending_selection(client, app):
    quick_login(client, "staff")

    with app.app_context():
        venue = Venue(name="Willow Hall", active=True)
        db.session.add(venue)
        db.session.flush()
        item = create_tracked_item(venue, "Tea Bags")
        add_status_check(
            venue,
            [(item, "low")],
            created_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        db.session.commit()
        venue_id = venue.id
        item_id = item.id

    response = client.get(f"/venues/{venue_id}/check")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Review the saved status on each row" in body
    assert "Current saved status" not in body
    assert "Update status" in body
    assert 'data-mode-toggle="status"' in body
    assert 'data-mode-toggle="raw_counts"' in body
    assert 'id="quickCheckUnsavedBanner"' in body
    assert 'id="quickCheckUnsavedModal"' in body
    assert "No pending changes" in body
    assert "Continue Without Saving" in body
    assert "Save and Continue" not in body
    assert "quick-check-current-status-pill" in body
    assert f'data-item-id="{item_id}"' in body
    assert 'data-current-status="low"' in body
    assert 'data-pending-status=""' in body
    assert f'name="status_{item_id}" id="status_{item_id}" value=""' in body


def test_quick_check_counts_mode_shows_last_par_and_blank_new_count(client, app):
    quick_login(client, "staff")

    with app.app_context():
        venue = Venue(name="Foxfire Lodge", active=True)
        db.session.add(venue)
        db.session.flush()
        counted_item = create_tracked_item(
            venue,
            "Lantern Fuel",
            sort_order=1,
            default_par_level=12,
            venue_par_override=8,
            unit="boxes",
        )
        fallback_item = create_tracked_item(
            venue,
            "Dish Soap",
            sort_order=2,
        )
        add_count_session(
            venue,
            [(counted_item, 5)],
            created_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        db.session.commit()
        venue_id = venue.id
        counted_item_id = counted_item.id
        fallback_item_id = fallback_item.id

    response = client.get(f"/venues/{venue_id}/check?mode=raw_counts")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    body_compact = " ".join(body.split())
    assert "Review the saved last count and par on each row" in body
    assert "New count (boxes)" in body_compact
    assert "New count (units)" in body_compact
    assert f'data-item-id="{counted_item_id}"' in body
    assert 'data-current-count="5"' in body
    assert 'data-par-count="8"' in body
    assert 'data-unit-label="boxes"' in body
    assert f'id="count_{counted_item_id}"' in body
    assert 'value=""' in body
    assert "5 boxes" in body_compact
    assert "8 boxes" in body_compact
    assert f'data-item-id="{fallback_item_id}"' in body
    assert "No prior count" in body
    assert "No par set" in body


def test_quick_check_same_status_recheck_creates_single_selected_line(client, app):
    quick_login(client, "staff")

    with app.app_context():
        venue = Venue(name="Cedar Lodge", active=True)
        db.session.add(venue)
        db.session.flush()
        selected_item = create_tracked_item(venue, "Cups", sort_order=1)
        untouched_item = create_tracked_item(venue, "Napkins", sort_order=2)
        original_checked_at = datetime.now(timezone.utc) - timedelta(days=2)
        add_status_check(
            venue,
            [(selected_item, "good"), (untouched_item, "low")],
            created_at=original_checked_at,
        )
        db.session.commit()
        venue_id = venue.id
        selected_item_id = selected_item.id
        untouched_item_id = untouched_item.id

    response = client.post(
        f"/venues/{venue_id}/check",
        data={
            "check_mode": "status",
            f"status_{selected_item_id}": "good",
            f"status_{untouched_item_id}": "",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302

    with app.app_context():
        checks = Check.query.filter_by(venue_id=venue_id).order_by(Check.id.asc()).all()
        latest_check = checks[-1]
        latest_lines = (
            CheckLine.query.filter_by(check_id=latest_check.id)
            .order_by(CheckLine.item_id.asc())
            .all()
        )
        untouched_timestamp = latest_status_timestamp(venue_id, untouched_item_id)

    assert len(checks) == 2
    assert len(latest_lines) == 1
    assert latest_lines[0].item_id == selected_item_id
    assert latest_lines[0].status == "good"
    assert untouched_timestamp == original_checked_at


def test_quick_check_status_post_only_refreshes_selected_items(client, app):
    quick_login(client, "staff")

    with app.app_context():
        venue = Venue(name="Birch Studio", active=True)
        db.session.add(venue)
        db.session.flush()
        first_item = create_tracked_item(venue, "Candles", sort_order=1)
        second_item = create_tracked_item(venue, "Matches", sort_order=2)
        third_item = create_tracked_item(venue, "Blankets", sort_order=3)
        original_checked_at = datetime.now(timezone.utc) - timedelta(days=3)
        add_status_check(
            venue,
            [(first_item, "good"), (second_item, "low"), (third_item, "out")],
            created_at=original_checked_at,
        )
        db.session.commit()
        venue_id = venue.id
        first_item_id = first_item.id
        second_item_id = second_item.id
        third_item_id = third_item.id

    response = client.post(
        f"/venues/{venue_id}/check",
        data={
            "check_mode": "status",
            f"status_{first_item_id}": "good",
            f"status_{second_item_id}": "ok",
            f"status_{third_item_id}": "",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302

    with app.app_context():
        refreshed_first = latest_status_timestamp(venue_id, first_item_id)
        refreshed_second = latest_status_timestamp(venue_id, second_item_id)
        untouched_third = latest_status_timestamp(venue_id, third_item_id)
        latest_check = Check.query.filter_by(venue_id=venue_id).order_by(Check.id.desc()).first()
        latest_line_items = {
            line.item_id
            for line in CheckLine.query.filter_by(check_id=latest_check.id).all()
        }

    assert refreshed_first > original_checked_at
    assert refreshed_second > original_checked_at
    assert untouched_third == original_checked_at
    assert latest_line_items == {first_item_id, second_item_id}


def test_quick_check_status_post_requires_at_least_one_selection(client, app):
    quick_login(client, "staff")

    with app.app_context():
        venue = Venue(name="Maple House", active=True)
        db.session.add(venue)
        db.session.flush()
        item = create_tracked_item(venue, "Soap")
        db.session.commit()
        venue_id = venue.id
        item_id = item.id

    response = client.post(
        f"/venues/{venue_id}/check",
        data={
            "check_mode": "status",
            f"status_{item_id}": "",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "Select at least one status to record a fresh quick check." in response.get_data(
        as_text=True
    )

    with app.app_context():
        check_count = Check.query.filter_by(venue_id=venue_id).count()

    assert check_count == 0


def test_quick_check_status_post_redirects_to_requested_mode_after_save(client, app):
    quick_login(client, "staff")

    with app.app_context():
        venue = Venue(name="Maple Grove", active=True)
        db.session.add(venue)
        db.session.flush()
        item = create_tracked_item(venue, "Mugs")
        add_status_check(
            venue,
            [(item, "good")],
            created_at=datetime.now(timezone.utc) - timedelta(days=2),
        )
        db.session.commit()
        venue_id = venue.id
        item_id = item.id

    response = client.post(
        f"/venues/{venue_id}/check",
        data={
            "check_mode": "status",
            "after_save_mode": "raw_counts",
            f"status_{item_id}": "ok",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    location = urlparse(response.headers["Location"])
    query = parse_qs(location.query)
    assert location.path == f"/venues/{venue_id}/check"
    assert query["mode"] == ["raw_counts"]


def test_quick_check_raw_counts_post_redirects_to_requested_next_after_save(client, app):
    quick_login(client, "staff")

    with app.app_context():
        venue = Venue(name="Oak Retreat", active=True)
        db.session.add(venue)
        db.session.flush()
        item = create_tracked_item(venue, "Napkins")
        add_count_session(
            venue,
            [(item, 10)],
            created_at=datetime.now(timezone.utc) - timedelta(days=2),
        )
        db.session.commit()
        venue_id = venue.id
        item_id = item.id

    response = client.post(
        f"/venues/{venue_id}/check",
        data={
            "check_mode": "raw_counts",
            "after_save_next": "/venues",
            f"count_{item_id}": "12",
            f"status_{item_id}": "",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    location = urlparse(response.headers["Location"])
    assert location.path == "/venues"


def test_quick_check_singleton_recheck_updates_compat_count_only_for_selected_asset(client, app):
    quick_login(client, "staff")

    with app.app_context():
        venue = Venue(name="Aspen Cabin", active=True)
        db.session.add(venue)
        db.session.flush()
        selected_asset = create_tracked_item(
            venue,
            "Projector",
            tracking_mode="singleton_asset",
            item_type="durable",
            item_category="durable",
            sort_order=1,
        )
        untouched_asset = create_tracked_item(
            venue,
            "Speaker",
            tracking_mode="singleton_asset",
            item_type="durable",
            item_category="durable",
            sort_order=2,
        )
        original_checked_at = datetime.now(timezone.utc) - timedelta(days=2)
        add_status_check(
            venue,
            [(selected_asset, "low"), (untouched_asset, "good")],
            created_at=original_checked_at,
        )
        db.session.add_all(
            [
                VenueItemCount(venue_id=venue.id, item_id=selected_asset.id, raw_count=1),
                VenueItemCount(venue_id=venue.id, item_id=untouched_asset.id, raw_count=1),
            ]
        )
        db.session.commit()
        venue_id = venue.id
        selected_asset_id = selected_asset.id
        untouched_asset_id = untouched_asset.id

    response = client.post(
        f"/venues/{venue_id}/check",
        data={
            "check_mode": "status",
            f"status_{selected_asset_id}": "out",
            f"status_{untouched_asset_id}": "",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302

    with app.app_context():
        selected_count = VenueItemCount.query.filter_by(
            venue_id=venue_id,
            item_id=selected_asset_id,
        ).first()
        untouched_count = VenueItemCount.query.filter_by(
            venue_id=venue_id,
            item_id=untouched_asset_id,
        ).first()

    assert selected_count is not None
    assert selected_count.raw_count == 0
    assert untouched_count is not None
    assert untouched_count.raw_count == 1


def test_quick_check_raw_counts_post_saves_only_entered_quantity_rows(client, app):
    quick_login(client, "staff")

    with app.app_context():
        venue = Venue(name="Pine Commons", active=True)
        db.session.add(venue)
        db.session.flush()
        selected_item = create_tracked_item(venue, "Plates", sort_order=1)
        untouched_item = create_tracked_item(venue, "Bowls", sort_order=2)
        original_counted_at = datetime.now(timezone.utc) - timedelta(days=3)
        add_count_session(
            venue,
            [(selected_item, 4), (untouched_item, 9)],
            created_at=original_counted_at,
        )
        db.session.commit()
        venue_id = venue.id
        selected_item_id = selected_item.id
        untouched_item_id = untouched_item.id

    response = client.post(
        f"/venues/{venue_id}/check",
        data={
            "check_mode": "raw_counts",
            f"count_{selected_item_id}": "7",
            f"count_{untouched_item_id}": "",
            f"status_{selected_item_id}": "",
            f"status_{untouched_item_id}": "",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302

    with app.app_context():
        sessions = (
            CountSession.query.filter_by(venue_id=venue_id)
            .order_by(CountSession.id.asc())
            .all()
        )
        latest_session = sessions[-1]
        latest_lines = (
            CountLine.query.filter_by(count_session_id=latest_session.id)
            .order_by(CountLine.item_id.asc())
            .all()
        )
        selected_count = VenueItemCount.query.filter_by(
            venue_id=venue_id,
            item_id=selected_item_id,
        ).first()
        untouched_count = VenueItemCount.query.filter_by(
            venue_id=venue_id,
            item_id=untouched_item_id,
        ).first()
        selected_counted_at = latest_count_timestamp(venue_id, selected_item_id)
        untouched_counted_at = latest_count_timestamp(venue_id, untouched_item_id)

    assert len(sessions) == 2
    assert len(latest_lines) == 1
    assert latest_lines[0].item_id == selected_item_id
    assert latest_lines[0].raw_count == 7
    assert selected_count.raw_count == 7
    assert untouched_count.raw_count == 9
    assert selected_counted_at > original_counted_at
    assert untouched_counted_at == original_counted_at


def test_quick_check_raw_counts_requires_pending_entry(client, app):
    quick_login(client, "staff")

    with app.app_context():
        venue = Venue(name="Spruce Hall", active=True)
        db.session.add(venue)
        db.session.flush()
        item = create_tracked_item(venue, "Trash Bags")
        db.session.commit()
        venue_id = venue.id
        item_id = item.id

    response = client.post(
        f"/venues/{venue_id}/check",
        data={
            "check_mode": "raw_counts",
            f"count_{item_id}": "",
            f"status_{item_id}": "",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "Add at least one count or status update before saving." in response.get_data(
        as_text=True
    )

    with app.app_context():
        count_session_count = CountSession.query.filter_by(venue_id=venue_id).count()
        check_count = Check.query.filter_by(venue_id=venue_id).count()

    assert count_session_count == 0
    assert check_count == 0


def test_quick_check_raw_counts_singleton_status_only_updates_selected_asset(client, app):
    quick_login(client, "staff")

    with app.app_context():
        venue = Venue(name="Redwood House", active=True)
        db.session.add(venue)
        db.session.flush()
        selected_asset = create_tracked_item(
            venue,
            "Extension Cord",
            tracking_mode="singleton_asset",
            item_type="durable",
            item_category="durable",
            sort_order=1,
        )
        untouched_asset = create_tracked_item(
            venue,
            "Microphone",
            tracking_mode="singleton_asset",
            item_type="durable",
            item_category="durable",
            sort_order=2,
        )
        original_checked_at = datetime.now(timezone.utc) - timedelta(days=2)
        add_status_check(
            venue,
            [(selected_asset, "good"), (untouched_asset, "low")],
            created_at=original_checked_at,
        )
        db.session.add_all(
            [
                VenueItemCount(venue_id=venue.id, item_id=selected_asset.id, raw_count=1),
                VenueItemCount(venue_id=venue.id, item_id=untouched_asset.id, raw_count=1),
            ]
        )
        db.session.commit()
        venue_id = venue.id
        selected_asset_id = selected_asset.id
        untouched_asset_id = untouched_asset.id

    response = client.post(
        f"/venues/{venue_id}/check",
        data={
            "check_mode": "raw_counts",
            f"status_{selected_asset_id}": "out",
            f"status_{untouched_asset_id}": "",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302

    with app.app_context():
        checks = Check.query.filter_by(venue_id=venue_id).order_by(Check.id.asc()).all()
        latest_check = checks[-1]
        latest_lines = (
            CheckLine.query.filter_by(check_id=latest_check.id)
            .order_by(CheckLine.item_id.asc())
            .all()
        )
        selected_count = VenueItemCount.query.filter_by(
            venue_id=venue_id,
            item_id=selected_asset_id,
        ).first()
        untouched_count = VenueItemCount.query.filter_by(
            venue_id=venue_id,
            item_id=untouched_asset_id,
        ).first()

    assert len(checks) == 2
    assert len(latest_lines) == 1
    assert latest_lines[0].item_id == selected_asset_id
    assert latest_lines[0].status == "out"
    assert selected_count is not None
    assert selected_count.raw_count == 0
    assert untouched_count is not None
    assert untouched_count.raw_count == 1


def test_same_status_recheck_updates_profile_freshness_without_new_activity_event(client, app):
    quick_login(client, "staff")

    with app.app_context():
        venue = Venue(name="Juniper Hall", active=True)
        db.session.add(venue)
        db.session.flush()
        item = create_tracked_item(venue, "Water Pitchers")
        original_checked_at = datetime.now(timezone.utc) - timedelta(days=4)
        add_status_check(
            venue,
            [(item, "good")],
            created_at=original_checked_at,
        )
        db.session.commit()
        venue_id = venue.id
        item_name = item.name
        item_id = item.id

        initial_activity = build_recent_venue_activity_rows(venue_id)
        initial_profile = build_venue_profile_view_model(venue_id)

    response = client.post(
        f"/venues/{venue_id}/check",
        data={
            "check_mode": "status",
            f"status_{item_id}": "good",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302

    with app.app_context():
        activity_rows = build_recent_venue_activity_rows(venue_id)
        profile = build_venue_profile_view_model(venue_id)
        row = flatten_inventory_rows(profile)[item_name]
        initial_row = flatten_inventory_rows(initial_profile)[item_name]

    assert len([row for row in initial_activity if row["type_key"] == "status"]) == 1
    assert len([row for row in activity_rows if row["type_key"] == "status"]) == 1
    assert row["status_freshness"]["updated_at"] > initial_row["status_freshness"]["updated_at"]


def test_same_value_recount_updates_profile_freshness_without_new_count_activity_event(client, app):
    quick_login(client, "staff")

    with app.app_context():
        venue = Venue(name="Hemlock Studio", active=True)
        db.session.add(venue)
        db.session.flush()
        item = create_tracked_item(
            venue,
            "Hand Towels",
            default_par_level=6,
        )
        original_counted_at = datetime.now(timezone.utc) - timedelta(days=4)
        add_count_session(
            venue,
            [(item, 6)],
            created_at=original_counted_at,
        )
        db.session.commit()
        venue_id = venue.id
        item_id = item.id
        item_name = item.name

        initial_activity = build_recent_venue_activity_rows(venue_id)
        initial_profile = build_venue_profile_view_model(venue_id)

    response = client.post(
        f"/venues/{venue_id}/check",
        data={
            "check_mode": "raw_counts",
            f"count_{item_id}": "6",
            f"status_{item_id}": "",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302

    with app.app_context():
        activity_rows = build_recent_venue_activity_rows(venue_id)
        profile = build_venue_profile_view_model(venue_id)
        row = flatten_inventory_rows(profile)[item_name]
        initial_row = flatten_inventory_rows(initial_profile)[item_name]
        sessions = (
            CountSession.query.filter_by(venue_id=venue_id)
            .order_by(CountSession.id.asc())
            .all()
        )
        count_line_count = CountLine.query.join(
            CountSession,
            CountSession.id == CountLine.count_session_id,
        ).filter(CountSession.venue_id == venue_id).count()

    assert len([row for row in initial_activity if row["type_key"] == "raw_count"]) == 1
    assert len([row for row in activity_rows if row["type_key"] == "raw_count"]) == 1
    assert len(sessions) == 2
    assert count_line_count == 2
    assert row["raw_count"] == 6
    assert row["count_freshness"]["updated_at"] > initial_row["count_freshness"]["updated_at"]
