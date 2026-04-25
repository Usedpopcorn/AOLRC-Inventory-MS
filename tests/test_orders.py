import csv
import io
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import selectinload

from app import AUTH_SESSION_VERSION_SESSION_KEY, db
from app.models import (
    Check,
    CheckLine,
    CountLine,
    CountSession,
    Item,
    OrderBatch,
    OrderLine,
    User,
    Venue,
    VenueItem,
    VenueItemCount,
)
from app.routes.orders import build_order_detail_context
from app.services.orders import (
    ORDER_SCOPE_UNASSIGNED_VALUE,
    build_grouped_order_line_summaries,
    build_grouped_summary_support_text,
    build_order_seed_rows,
    build_purchase_summary_rows,
)


def quick_login(client, role="admin"):
    quick_role = "user" if role == "viewer" else role
    return client.post(
        "/login",
        data={"quick_login_role": quick_role},
        follow_redirects=False,
    )


def force_login(client, user):
    with client.session_transaction() as session:
        session["_user_id"] = str(user.id)
        session["_fresh"] = True
        session[AUTH_SESSION_VERSION_SESSION_KEY] = int(user.session_version or 1)


def create_tracked_item(
    venue,
    name,
    *,
    tracking_mode="quantity",
    item_type="consumable",
    item_category=None,
    default_par_level=None,
    venue_par_override=None,
    sort_order=0,
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
        default_par_level=default_par_level,
        sort_order=sort_order,
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


def create_order_line(
    batch,
    *,
    item_name="Tea Lights",
    venue_name="Willow Hall",
    setup_group_code="02",
    setup_group_label="Standard Signature Set-up Needs",
    count_snapshot=4,
    par_snapshot=9,
    suggested_order_qty_snapshot=5,
    over_par_qty_snapshot=0,
    actual_ordered_qty=None,
    status="planned",
    notes=None,
    item_id=None,
    venue_id=None,
):
    line = OrderLine(
        order_batch_id=batch.id,
        item_id=item_id,
        venue_id=venue_id,
        item_name_snapshot=item_name,
        venue_name_snapshot=venue_name,
        setup_group_code_snapshot=setup_group_code,
        setup_group_label_snapshot=setup_group_label,
        count_snapshot=count_snapshot,
        par_snapshot=par_snapshot,
        suggested_order_qty_snapshot=suggested_order_qty_snapshot,
        over_par_qty_snapshot=over_par_qty_snapshot,
        actual_ordered_qty=actual_ordered_qty,
        status=status,
        notes=notes,
    )
    db.session.add(line)
    db.session.flush()
    return line


def create_batch_with_line(*, name="Monthly Batch", status="planned"):
    batch = OrderBatch(name=name, batch_type="monthly")
    db.session.add(batch)
    db.session.flush()
    line = create_order_line(batch, status=status)
    return batch, line


def extract_csrf_token(response_data):
    match = re.search(rb'name="csrf_token" value="([^"]+)"', response_data)
    assert match is not None
    return match.group(1).decode()


def parse_csv_response(response):
    text = response.data.decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


def test_build_order_seed_rows_only_includes_actionable_comparable_rows(app):
    with app.app_context():
        venue = Venue(name="Seed Venue", active=True)
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
        at_par_item = create_tracked_item(
            venue,
            "At Par Supply",
            default_par_level=6,
            sort_order=3,
        )
        no_count_item = create_tracked_item(
            venue,
            "No Count Supply",
            default_par_level=4,
            sort_order=4,
        )
        no_par_item = create_tracked_item(
            venue,
            "No Par Supply",
            sort_order=5,
        )
        singleton_item = create_tracked_item(
            venue,
            "Projector",
            tracking_mode="singleton_asset",
            item_type="durable",
            item_category="durable",
            sort_order=6,
        )
        add_status_check(
            venue,
            [
                (venue_override_item, "ok"),
                (over_par_item, "good"),
                (at_par_item, "good"),
                (no_count_item, "low"),
                (no_par_item, "good"),
                (singleton_item, "out"),
            ],
            created_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        add_count_session(
            venue,
            [
                (venue_override_item, 4),
                (over_par_item, 8),
                (at_par_item, 6),
                (no_par_item, 2),
            ],
            created_at=datetime.now(timezone.utc) - timedelta(hours=4),
        )
        db.session.commit()

        rows = build_order_seed_rows()
        rows_by_name = {row["item_name"]: row for row in rows}

    assert set(rows_by_name) == {"Venue Override Supply", "Extra Blankets"}
    assert rows_by_name["Venue Override Supply"]["par_value"] == 9
    assert rows_by_name["Venue Override Supply"]["count_state"]["suggested_order_qty"] == 5
    assert rows_by_name["Venue Override Supply"]["count_state"]["over_par_qty"] == 0
    assert (
        rows_by_name["Venue Override Supply"]["setup_group_display"]
        == "02 - Standard Signature Set-up Needs"
    )
    assert rows_by_name["Extra Blankets"]["count_state"]["suggested_order_qty"] == 0
    assert rows_by_name["Extra Blankets"]["count_state"]["over_par_qty"] == 3


def test_build_order_seed_rows_respects_venue_and_setup_group_scopes(app):
    with app.app_context():
        venue_one = Venue(name="Venue One", active=True)
        venue_two = Venue(name="Venue Two", active=True)
        db.session.add_all([venue_one, venue_two])
        db.session.flush()

        group_one_item = create_tracked_item(
            venue_one,
            "Meditation Mats",
            default_par_level=8,
            setup_group_code="01",
            setup_group_label="Yoga/Meditation Materials",
        )
        unassigned_item = create_tracked_item(
            venue_two,
            "Unassigned Props",
            default_par_level=5,
        )
        add_status_check(
            venue_one,
            [(group_one_item, "low")],
            created_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        add_status_check(
            venue_two,
            [(unassigned_item, "low")],
            created_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        add_count_session(
            venue_one,
            [(group_one_item, 2)],
            created_at=datetime.now(timezone.utc) - timedelta(hours=3),
        )
        add_count_session(
            venue_two,
            [(unassigned_item, 1)],
            created_at=datetime.now(timezone.utc) - timedelta(hours=2),
        )
        db.session.commit()

        venue_rows = build_order_seed_rows(scope_venue_id=venue_one.id)
        grouped_rows = build_order_seed_rows(scope_setup_group="01")
        unassigned_rows = build_order_seed_rows(scope_setup_group=ORDER_SCOPE_UNASSIGNED_VALUE)

    assert [row["item_name"] for row in venue_rows] == ["Meditation Mats"]
    assert [row["item_name"] for row in grouped_rows] == ["Meditation Mats"]
    assert [row["item_name"] for row in unassigned_rows] == ["Unassigned Props"]


def test_build_purchase_summary_rows_aggregates_numeric_totals_and_mixed_statuses():
    line_rows = [
        {
            "id": 32,
            "item_id": 7,
            "item_name": "Tea Lights",
            "venue_id": 12,
            "venue_name": "Zendo",
            "setup_group_code": "02",
            "setup_group_label": "Standard Signature Set-up Needs",
            "setup_group_display": "02 - Standard Signature Set-up Needs",
            "count_snapshot": 4,
            "par_snapshot": 10,
            "suggested_order_qty_snapshot": 6,
            "actual_ordered_qty": 2,
            "over_par_qty_snapshot": 0,
            "status": "ordered",
            "notes": "Restock candles",
        },
        {
            "id": 31,
            "item_id": 7,
            "item_name": "Tea Lights",
            "venue_id": 11,
            "venue_name": "Ananda Hall",
            "setup_group_code": "02",
            "setup_group_label": "Standard Signature Set-up Needs",
            "setup_group_display": "02 - Standard Signature Set-up Needs",
            "count_snapshot": 3,
            "par_snapshot": 8,
            "suggested_order_qty_snapshot": 5,
            "actual_ordered_qty": None,
            "over_par_qty_snapshot": 1,
            "status": "planned",
            "notes": " ",
        },
        {
            "id": 33,
            "item_id": 7,
            "item_name": "Tea Lights",
            "venue_id": 11,
            "venue_name": "Ananda Hall",
            "setup_group_code": "02",
            "setup_group_label": "Standard Signature Set-up Needs",
            "setup_group_display": "02 - Standard Signature Set-up Needs",
            "count_snapshot": None,
            "par_snapshot": None,
            "suggested_order_qty_snapshot": 0,
            "actual_ordered_qty": 1,
            "over_par_qty_snapshot": 0,
            "status": "received",
            "notes": "Arrived early",
        },
        {
            "id": 40,
            "item_id": 9,
            "item_name": "Meditation Shawls",
            "venue_id": 14,
            "venue_name": "South Hall",
            "setup_group_code": "01",
            "setup_group_label": "Yoga/Meditation Materials",
            "setup_group_display": "01 - Yoga/Meditation Materials",
            "count_snapshot": 1,
            "par_snapshot": 3,
            "suggested_order_qty_snapshot": 2,
            "actual_ordered_qty": 0,
            "over_par_qty_snapshot": 0,
            "status": "planned",
            "notes": None,
        },
    ]

    rows = build_purchase_summary_rows(line_rows)

    assert [row["item_name"] for row in rows] == ["Tea Lights", "Meditation Shawls"]

    tea_lights = rows[0]
    assert tea_lights["venue_count"] == 2
    assert tea_lights["venue_count_text"] == "2 venues"
    assert tea_lights["line_count"] == 3
    assert tea_lights["total_count_snapshot"] == 7
    assert tea_lights["total_par_snapshot"] == 18
    assert tea_lights["total_suggested_order_qty"] == 11
    assert tea_lights["total_actual_ordered_qty"] == 3
    assert tea_lights["total_over_par_qty"] == 1
    assert tea_lights["note_count"] == 2
    assert tea_lights["note_summary_text"] == "2 venue notes"
    assert tea_lights["rollup_status_key"] == "mixed"
    assert tea_lights["rollup_status_label"] == "Mixed"
    assert tea_lights["status_counts"] == {
        "planned": 1,
        "ordered": 1,
        "received": 1,
        "skipped": 0,
    }
    assert tea_lights["status_breakdown_text"] == "1 Planned · 1 Ordered · 1 Received"
    assert [line["id"] for line in tea_lights["contributing_lines"]] == [31, 33, 32]


def test_build_purchase_summary_rows_groups_snapshot_fallback_without_item_ids():
    line_rows = [
        {
            "id": 1,
            "item_id": None,
            "item_name": "Tea Lights",
            "venue_id": None,
            "venue_name": "North Hall",
            "setup_group_code": "02",
            "setup_group_label": "Standard Signature Set-up Needs",
            "setup_group_display": "02 - Standard Signature Set-up Needs",
            "count_snapshot": 3,
            "par_snapshot": 8,
            "suggested_order_qty_snapshot": 5,
            "actual_ordered_qty": 0,
            "over_par_qty_snapshot": 0,
            "status": "planned",
            "notes": None,
        },
        {
            "id": 2,
            "item_id": None,
            "item_name": "Tea Lights",
            "venue_id": None,
            "venue_name": "South Hall",
            "setup_group_code": "02",
            "setup_group_label": "Standard Signature Set-up Needs",
            "setup_group_display": "02 - Standard Signature Set-up Needs",
            "count_snapshot": 1,
            "par_snapshot": 4,
            "suggested_order_qty_snapshot": 3,
            "actual_ordered_qty": 0,
            "over_par_qty_snapshot": 0,
            "status": "planned",
            "notes": None,
        },
        {
            "id": 3,
            "item_id": None,
            "item_name": "Tea Lights",
            "venue_id": None,
            "venue_name": "South Hall",
            "setup_group_code": "05",
            "setup_group_label": "Misc.",
            "setup_group_display": "05 - Misc.",
            "count_snapshot": 2,
            "par_snapshot": 2,
            "suggested_order_qty_snapshot": 0,
            "actual_ordered_qty": 0,
            "over_par_qty_snapshot": 1,
            "status": "skipped",
            "notes": "Already stocked",
        },
        {
            "id": 4,
            "item_id": None,
            "item_name": "Blankets",
            "venue_id": None,
            "venue_name": "South Hall",
            "setup_group_code": "05",
            "setup_group_label": "Misc.",
            "setup_group_display": "05 - Misc.",
            "count_snapshot": 1,
            "par_snapshot": 6,
            "suggested_order_qty_snapshot": 5,
            "actual_ordered_qty": 1,
            "over_par_qty_snapshot": 0,
            "status": "ordered",
            "notes": None,
        },
    ]

    rows = build_purchase_summary_rows(line_rows)
    grouped_rows = {
        (row["item_name"], row["setup_group_display"]): row
        for row in rows
    }

    assert set(grouped_rows) == {
        ("Tea Lights", "02 - Standard Signature Set-up Needs"),
        ("Tea Lights", "05 - Misc."),
        ("Blankets", "05 - Misc."),
    }
    assert grouped_rows[("Tea Lights", "02 - Standard Signature Set-up Needs")][
        "total_suggested_order_qty"
    ] == 8
    assert grouped_rows[("Tea Lights", "02 - Standard Signature Set-up Needs")][
        "line_count"
    ] == 2
    assert grouped_rows[("Tea Lights", "05 - Misc.")]["rollup_status_key"] == "skipped"
    assert grouped_rows[("Tea Lights", "05 - Misc.")]["note_summary_text"] == "1 venue note"


def test_build_grouped_summary_support_text_formats_preview_rollups():
    line_rows = [
        {
            "venue_name": "North Hall",
            "setup_group_display": "02 - Standard Signature Set-up Needs",
            "suggested_order_qty_snapshot": 5,
            "over_par_qty_snapshot": 1,
            "actual_ordered_qty": 0,
            "status": "planned",
        },
        {
            "venue_name": "North Hall",
            "setup_group_display": "02 - Standard Signature Set-up Needs",
            "suggested_order_qty_snapshot": 3,
            "over_par_qty_snapshot": 0,
            "actual_ordered_qty": 1,
            "status": "ordered",
        },
        {
            "venue_name": "South Hall",
            "setup_group_display": "05 - Misc.",
            "suggested_order_qty_snapshot": 0,
            "over_par_qty_snapshot": 2,
            "actual_ordered_qty": 0,
            "status": "skipped",
        },
    ]

    venue_summaries = build_grouped_order_line_summaries(line_rows, group_by="venue")
    setup_group_summaries = build_grouped_order_line_summaries(
        line_rows,
        group_by="setup_group",
    )

    assert build_grouped_summary_support_text(
        venue_summaries,
        singular_label="venue",
        plural_label="venues",
    ) == "2 venues · 8 suggested · 2 over-par lines"
    assert build_grouped_summary_support_text(
        setup_group_summaries,
        singular_label="group",
        plural_label="groups",
    ) == "2 groups · 8 suggested · 2 over-par lines"


def test_admin_can_create_order_batch_and_export_uses_historical_snapshots(client, app):
    quick_login(client, "admin")

    with app.app_context():
        venue = Venue(name="History Venue", active=True)
        db.session.add(venue)
        db.session.flush()
        item = create_tracked_item(
            venue,
            "Meditation Shawls",
            default_par_level=10,
            venue_par_override=8,
            setup_group_code="01",
            setup_group_label="Yoga/Meditation Materials",
        )
        add_status_check(
            venue,
            [(item, "low")],
            created_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        add_count_session(
            venue,
            [(item, 2)],
            created_at=datetime.now(timezone.utc) - timedelta(hours=3),
        )
        db.session.commit()
        item_id = item.id
        venue_id = venue.id

    response = client.post(
        "/orders",
        data={
            "name": "April Monthly Order",
            "batch_type": "monthly",
            "notes": "Legacy spreadsheet replacement",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert "/orders/" in response.headers["Location"]

    with app.app_context():
        batch = OrderBatch.query.filter_by(name="April Monthly Order").first()
        line = OrderLine.query.filter_by(order_batch_id=batch.id).first()

        item = db.session.get(Item, item_id)
        item.name = "Renamed Shawls"
        item.setup_group_code = "05"
        item.setup_group_label = "Misc."
        venue = db.session.get(Venue, venue_id)
        venue.name = "Renamed Venue"
        venue_link = VenueItem.query.filter_by(venue_id=venue_id, item_id=item_id).first()
        venue_link.expected_qty = 12
        current_count = VenueItemCount.query.filter_by(venue_id=venue_id, item_id=item_id).first()
        current_count.raw_count = 7
        db.session.commit()

        refreshed_line = db.session.get(OrderLine, line.id)
        batch_id = batch.id

    assert batch is not None
    assert line.item_name_snapshot == "Meditation Shawls"
    assert line.venue_name_snapshot == "History Venue"
    assert line.setup_group_code_snapshot == "01"
    assert line.setup_group_label_snapshot == "Yoga/Meditation Materials"
    assert line.count_snapshot == 2
    assert line.par_snapshot == 8
    assert line.suggested_order_qty_snapshot == 6
    assert refreshed_line.item_name_snapshot == "Meditation Shawls"
    assert refreshed_line.venue_name_snapshot == "History Venue"
    assert refreshed_line.setup_group_code_snapshot == "01"
    assert refreshed_line.par_snapshot == 8
    assert refreshed_line.count_snapshot == 2

    export_response = client.get(f"/orders/{batch_id}/export.csv")
    export_rows = parse_csv_response(export_response)

    assert export_response.status_code == 200
    assert export_rows == [
        {
            "Batch Name": "April Monthly Order",
            "Batch Type": "Monthly",
            "Batch Created Date": export_rows[0]["Batch Created Date"],
            "Batch Created By": export_rows[0]["Batch Created By"],
            "Item Name": "Meditation Shawls",
            "Venue Name": "History Venue",
            "Setup Group Code": "01",
            "Setup Group Label": "Yoga/Meditation Materials",
            "Count Snapshot": "2",
            "Par Snapshot": "8",
            "Suggested Order Qty": "6",
            "Over Par Qty": "0",
            "Actual Ordered Qty": "",
            "Status": "Planned",
            "Line Notes": "",
        }
    ]


def test_admin_can_create_empty_batch_when_nothing_is_actionable(client, app):
    quick_login(client, "admin")

    with app.app_context():
        venue = Venue(name="Quiet Venue", active=True)
        db.session.add(venue)
        db.session.flush()
        item = create_tracked_item(venue, "At Par Supply", default_par_level=4)
        add_status_check(
            venue,
            [(item, "good")],
            created_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        add_count_session(
            venue,
            [(item, 4)],
            created_at=datetime.now(timezone.utc) - timedelta(hours=2),
        )
        db.session.commit()

    response = client.post(
        "/orders",
        data={
            "name": "No Action Batch",
            "batch_type": "ad_hoc",
            "notes": "",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302

    with app.app_context():
        batch = OrderBatch.query.filter_by(name="No Action Batch").first()
        line_count = OrderLine.query.filter_by(order_batch_id=batch.id).count()

    assert batch is not None
    assert batch.batch_type == "ad_hoc"
    assert line_count == 0


def test_invalid_batch_type_and_scopes_render_validation_errors_without_creating_batch(client, app):
    quick_login(client, "admin")

    response = client.post(
        "/orders",
        data={
            "name": "Bad Batch",
            "batch_type": "tampered",
            "notes": "Should fail",
            "scope_venue_id": "999",
            "scope_setup_group": "__bogus__",
        },
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert b"Create batch couldn&#39;t be saved." in response.data
    assert b"Batch type selection is invalid." in response.data
    assert b"Venue scope selection is invalid." in response.data
    assert b"Setup group scope selection is invalid." in response.data
    assert b"Invalid selection: tampered" in response.data

    with app.app_context():
        assert OrderBatch.query.filter_by(name="Bad Batch").first() is None


def test_viewer_and_staff_can_view_and_export_orders_but_cannot_post_changes(client, app):
    with app.app_context():
        batch, line = create_batch_with_line()
        db.session.commit()
        batch_id = batch.id
        line_id = line.id

    for role in ("viewer", "staff"):
        quick_login(client, role)

        index_response = client.get("/orders")
        detail_response = client.get(f"/orders/{batch_id}")
        export_response = client.get(f"/orders/{batch_id}/export.csv")
        purchase_export_response = client.get(
            f"/orders/{batch_id}/purchase-summary-export.csv"
        )
        create_response = client.post(
            "/orders",
            data={"name": "Blocked", "batch_type": "monthly"},
            follow_redirects=False,
        )
        update_response = client.post(
            f"/orders/{batch_id}/lines/{line_id}",
            data={"actual_ordered_qty": "3", "line_status": "ordered", "notes": "Blocked"},
            follow_redirects=False,
        )
        bulk_response = client.post(
            f"/orders/{batch_id}/lines/bulk",
            data={
                "bulk_action": "set_status",
                "bulk_status": "received",
                "line_ids": [str(line_id)],
            },
            follow_redirects=False,
        )

        assert index_response.status_code == 200
        assert detail_response.status_code == 200
        assert export_response.status_code == 200
        assert purchase_export_response.status_code == 200
        assert b"Create Batch" not in index_response.data
        assert b"Save Line" not in detail_response.data
        assert b"Bulk Status Update" not in detail_response.data
        assert create_response.status_code == 302
        assert create_response.headers["Location"].endswith("/dashboard")
        assert update_response.status_code == 302
        assert update_response.headers["Location"].endswith("/dashboard")
        assert bulk_response.status_code == 302
        assert bulk_response.headers["Location"].endswith("/dashboard")


def test_unauthenticated_users_cannot_export_orders(client, app):
    with app.app_context():
        batch, _line = create_batch_with_line()
        db.session.commit()
        batch_id = batch.id

    response = client.get(f"/orders/{batch_id}/export.csv", follow_redirects=False)
    purchase_response = client.get(
        f"/orders/{batch_id}/purchase-summary-export.csv",
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]
    assert purchase_response.status_code == 302
    assert "/login" in purchase_response.headers["Location"]


def test_admin_orders_pages_render_csrf_fields_for_post_forms(client, app):
    quick_login(client, "admin")

    with app.app_context():
        batch, _line = create_batch_with_line()
        db.session.commit()
        batch_id = batch.id

    index_response = client.get("/orders")
    detail_response = client.get(f"/orders/{batch_id}")

    assert index_response.status_code == 200
    assert detail_response.status_code == 200
    assert b'name="csrf_token"' in index_response.data
    assert detail_response.data.count(b'name="csrf_token"') >= 3


def test_admin_can_create_batch_with_csrf_enabled(client, app):
    with app.app_context():
        admin_user = User(
            email="csrf-admin@example.com",
            password_hash="test-hash",
            role="admin",
            active=True,
            session_version=1,
        )
        venue = Venue(name="CSRF Venue", active=True)
        db.session.add_all([admin_user, venue])
        db.session.flush()
        item = create_tracked_item(
            venue,
            "CSRF Supply",
            default_par_level=7,
        )
        add_status_check(
            venue,
            [(item, "low")],
            created_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        add_count_session(
            venue,
            [(item, 2)],
            created_at=datetime.now(timezone.utc) - timedelta(hours=2),
        )
        db.session.commit()
        admin_user_id = admin_user.id

    app.config["WTF_CSRF_ENABLED"] = True
    try:
        with app.app_context():
            admin_user = db.session.get(User, admin_user_id)
        force_login(client, admin_user)

        get_response = client.get("/orders")
        assert get_response.status_code == 200
        csrf_token = extract_csrf_token(get_response.data)

        post_response = client.post(
            "/orders",
            data={
                "csrf_token": csrf_token,
                "name": "CSRF Batch",
                "batch_type": "monthly",
                "notes": "Created with CSRF enabled.",
            },
            follow_redirects=False,
        )
    finally:
        app.config["WTF_CSRF_ENABLED"] = False

    assert post_response.status_code == 302

    with app.app_context():
        batch = OrderBatch.query.filter_by(name="CSRF Batch").first()

    assert batch is not None


def test_admin_can_update_batch_notes_and_line_fields(client, app):
    quick_login(client, "admin")

    with app.app_context():
        batch, line = create_batch_with_line()
        db.session.commit()
        batch_id = batch.id
        line_id = line.id

    notes_response = client.post(
        f"/orders/{batch_id}",
        data={"notes": "Need quotes before ordering."},
        follow_redirects=False,
    )
    line_response = client.post(
        f"/orders/{batch_id}/lines/{line_id}",
        data={
            "actual_ordered_qty": "7",
            "line_status": "ordered",
            "notes": "Placed with supplier.",
        },
        follow_redirects=False,
    )

    assert notes_response.status_code == 302
    assert line_response.status_code == 302

    with app.app_context():
        batch = db.session.get(OrderBatch, batch_id)
        line = db.session.get(OrderLine, line_id)

    assert batch.notes == "Need quotes before ordering."
    assert line.actual_ordered_qty == 7
    assert line.status == "ordered"
    assert line.notes == "Placed with supplier."


def test_order_line_update_rejects_negative_actual_quantity(client, app):
    quick_login(client, "admin")

    with app.app_context():
        batch, line = create_batch_with_line()
        db.session.commit()
        batch_id = batch.id
        line_id = line.id

    response = client.post(
        f"/orders/{batch_id}/lines/{line_id}",
        data={
            "actual_ordered_qty": "-1",
            "line_status": "received",
            "notes": "Should not save",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302

    with app.app_context():
        line = db.session.get(OrderLine, line_id)

    assert line.actual_ordered_qty is None
    assert line.status == "planned"
    assert line.notes is None


def test_order_line_update_rejects_invalid_status(client, app):
    quick_login(client, "admin")

    with app.app_context():
        batch, line = create_batch_with_line()
        db.session.commit()
        batch_id = batch.id
        line_id = line.id

    response = client.post(
        f"/orders/{batch_id}/lines/{line_id}",
        data={
            "actual_ordered_qty": "4",
            "line_status": "tampered",
            "notes": "Should not save",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302

    with app.app_context():
        line = db.session.get(OrderLine, line_id)

    assert line.actual_ordered_qty is None
    assert line.status == "planned"
    assert line.notes is None


def test_admin_can_bulk_update_selected_order_lines(client, app):
    quick_login(client, "admin")

    with app.app_context():
        batch = OrderBatch(name="Bulk Batch", batch_type="monthly")
        db.session.add(batch)
        db.session.flush()
        line_one = create_order_line(batch, item_name="Tea Lights", status="planned")
        line_two = create_order_line(batch, item_name="Blankets", status="ordered")
        line_three = create_order_line(batch, item_name="Cushions", status="planned")
        db.session.commit()
        batch_id = batch.id
        line_one_id = line_one.id
        line_two_id = line_two.id
        line_three_id = line_three.id

    response = client.post(
        f"/orders/{batch_id}/lines/bulk",
        data={
            "bulk_action": "set_status",
            "bulk_status": "received",
            "line_ids": [str(line_one_id), str(line_two_id)],
        },
        follow_redirects=False,
    )

    assert response.status_code == 302

    with app.app_context():
        line_one = db.session.get(OrderLine, line_one_id)
        line_two = db.session.get(OrderLine, line_two_id)
        line_three = db.session.get(OrderLine, line_three_id)

    assert line_one.status == "received"
    assert line_two.status == "received"
    assert line_three.status == "planned"


def test_bulk_status_update_rejects_invalid_status_without_partial_save(client, app):
    quick_login(client, "admin")

    with app.app_context():
        batch = OrderBatch(name="Bulk Invalid Status", batch_type="monthly")
        db.session.add(batch)
        db.session.flush()
        line_one = create_order_line(batch, item_name="Tea Lights", status="planned")
        line_two = create_order_line(batch, item_name="Blankets", status="ordered")
        db.session.commit()
        batch_id = batch.id
        line_ids = [line_one.id, line_two.id]

    response = client.post(
        f"/orders/{batch_id}/lines/bulk",
        data={
            "bulk_action": "set_status",
            "bulk_status": "bad-status",
            "line_ids": [str(line_id) for line_id in line_ids],
        },
        follow_redirects=False,
    )

    assert response.status_code == 302

    with app.app_context():
        statuses = [db.session.get(OrderLine, line_id).status for line_id in line_ids]

    assert statuses == ["planned", "ordered"]


def test_bulk_status_update_rejects_cross_batch_line_ids(client, app):
    quick_login(client, "admin")

    with app.app_context():
        batch_one = OrderBatch(name="Batch One", batch_type="monthly")
        batch_two = OrderBatch(name="Batch Two", batch_type="monthly")
        db.session.add_all([batch_one, batch_two])
        db.session.flush()
        line_one = create_order_line(batch_one, item_name="Tea Lights", status="planned")
        line_two = create_order_line(batch_two, item_name="Blankets", status="ordered")
        db.session.commit()
        batch_one_id = batch_one.id
        line_one_id = line_one.id
        line_two_id = line_two.id

    response = client.post(
        f"/orders/{batch_one_id}/lines/bulk",
        data={
            "bulk_action": "set_status",
            "bulk_status": "received",
            "line_ids": [str(line_one_id), str(line_two_id)],
        },
        follow_redirects=False,
    )

    assert response.status_code == 302

    with app.app_context():
        line_one = db.session.get(OrderLine, line_one_id)
        line_two = db.session.get(OrderLine, line_two_id)

    assert line_one.status == "planned"
    assert line_two.status == "ordered"


def test_order_detail_filters_lines_and_grouped_summaries_follow_filtered_view(client, app):
    quick_login(client, "viewer")

    with app.app_context():
        batch = OrderBatch(name="Filter Batch", batch_type="quarterly")
        db.session.add(batch)
        db.session.flush()
        create_order_line(
            batch,
            item_id=101,
            item_name="Tea Lights",
            venue_name="North Hall",
            venue_id=201,
            setup_group_code="02",
            setup_group_label="Standard Signature Set-up Needs",
            suggested_order_qty_snapshot=6,
            status="ordered",
            actual_ordered_qty=1,
            notes="North hall follow-up",
        )
        create_order_line(
            batch,
            venue_name="North Hall",
            item_id=202,
            item_name="Blankets",
            venue_id=301,
            setup_group_code="05",
            setup_group_label="Misc.",
            suggested_order_qty_snapshot=0,
            over_par_qty_snapshot=3,
            status="skipped",
            notes="Already over par",
        )
        create_order_line(
            batch,
            item_id=101,
            item_name="Tea Lights",
            venue_name="South Hall",
            venue_id=202,
            setup_group_code="02",
            setup_group_label="Standard Signature Set-up Needs",
            suggested_order_qty_snapshot=2,
            status="planned",
            notes="South hall vendor note",
        )
        db.session.commit()
        batch_id = batch.id
        batch_with_lines = (
            OrderBatch.query.options(selectinload(OrderBatch.lines))
            .filter_by(id=batch_id)
            .first()
        )
        with app.test_request_context(f"/orders/{batch_id}?q=tea"):
            filtered_context = build_order_detail_context(
                batch_with_lines,
                line_filters={"q": "tea", "status": "all"},
            )

    response = client.get(f"/orders/{batch_id}?q=tea")

    assert response.status_code == 200
    assert b"Tea Lights" in response.data
    assert b"Blankets" not in response.data
    assert b"Purchase Summary By Item" in response.data
    assert b"Line Details" in response.data
    assert b"Mixed" in response.data
    assert b"2 venue notes" in response.data
    assert b"North hall follow-up" in response.data
    assert b"South hall vendor note" in response.data
    assert b"By Setup Group" in response.data
    assert b"By Venue" in response.data
    assert "1 group · 8 suggested · 0 over-par lines".encode("utf-8") in response.data
    assert "2 venues · 8 suggested · 0 over-par lines".encode("utf-8") in response.data

    assert filtered_context["batch_summary"]["line_count"] == 3
    assert filtered_context["batch_summary"]["total_over_par_qty"] == 3
    assert len(filtered_context["line_rows"]) == 2
    assert (
        filtered_context["setup_group_summary_support"]
        == "1 group · 8 suggested · 0 over-par lines"
    )
    assert filtered_context["venue_summary_support"] == "2 venues · 8 suggested · 0 over-par lines"
    assert filtered_context["setup_group_summaries"] == [
        {
            "group_key": "02 - Standard Signature Set-up Needs",
            "label": "02 - Standard Signature Set-up Needs",
            "line_count": 2,
            "total_suggested_qty": 8,
            "total_actual_ordered_qty": 1,
            "over_par_line_count": 0,
            "status_counts": {"planned": 1, "ordered": 1, "received": 0, "skipped": 0},
            "status_count_items": [
                {"key": "planned", "label": "Planned", "count": 1},
                {"key": "ordered", "label": "Ordered", "count": 1},
            ],
        }
    ]
    assert filtered_context["venue_summaries"] == [
        {
            "group_key": "North Hall",
            "label": "North Hall",
            "line_count": 1,
            "total_suggested_qty": 6,
            "total_actual_ordered_qty": 1,
            "over_par_line_count": 0,
            "status_counts": {"planned": 0, "ordered": 1, "received": 0, "skipped": 0},
            "status_count_items": [{"key": "ordered", "label": "Ordered", "count": 1}],
        },
        {
            "group_key": "South Hall",
            "label": "South Hall",
            "line_count": 1,
            "total_suggested_qty": 2,
            "total_actual_ordered_qty": 0,
            "over_par_line_count": 0,
            "status_counts": {"planned": 1, "ordered": 0, "received": 0, "skipped": 0},
            "status_count_items": [{"key": "planned", "label": "Planned", "count": 1}],
        }
    ]

    purchase_summary_rows = filtered_context["purchase_summary_rows"]
    assert len(purchase_summary_rows) == 1
    assert purchase_summary_rows[0]["item_name"] == "Tea Lights"
    assert purchase_summary_rows[0]["venue_count"] == 2
    assert purchase_summary_rows[0]["total_count_snapshot"] == 8
    assert purchase_summary_rows[0]["total_par_snapshot"] == 18
    assert purchase_summary_rows[0]["total_suggested_order_qty"] == 8
    assert purchase_summary_rows[0]["total_actual_ordered_qty"] == 1
    assert purchase_summary_rows[0]["total_over_par_qty"] == 0
    assert purchase_summary_rows[0]["note_count"] == 2
    assert purchase_summary_rows[0]["rollup_status_key"] == "mixed"
    assert purchase_summary_rows[0]["status_breakdown_text"] == "1 Planned · 1 Ordered"
    assert [line["venue_name"] for line in purchase_summary_rows[0]["contributing_lines"]] == [
        "North Hall",
        "South Hall",
    ]


def test_order_detail_partial_returns_filtered_html_and_export_url(client, app):
    quick_login(client, "viewer")

    with app.app_context():
        batch = OrderBatch(name="Partial Batch", batch_type="monthly")
        db.session.add(batch)
        db.session.flush()
        create_order_line(
            batch,
            item_name="Tea Lights",
            venue_name="North Hall",
            status="ordered",
            notes="Priority",
        )
        create_order_line(
            batch,
            item_name="Blankets",
            venue_name="South Hall",
            status="planned",
        )
        db.session.commit()
        batch_id = batch.id

    response = client.get(
        f"/orders/{batch_id}?detail_partial=1&q=tea&status=ordered",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload is not None
    assert "Tea Lights" in payload["html"]
    assert "Blankets" not in payload["html"]
    assert payload["export_url"].endswith(
        f"/orders/{batch_id}/export.csv?q=tea&status=ordered&scope=filtered"
    )


def test_csv_export_honors_line_filters_and_column_order(client, app):
    quick_login(client, "viewer")

    with app.app_context():
        batch = OrderBatch(name="Export Batch", batch_type="quarterly")
        db.session.add(batch)
        db.session.flush()
        create_order_line(
            batch,
            item_name="Tea Lights",
            venue_name="North Hall",
            setup_group_code="02",
            setup_group_label="Standard Signature Set-up Needs",
            suggested_order_qty_snapshot=6,
            status="ordered",
            notes="Priority line",
        )
        create_order_line(
            batch,
            item_name="Blankets",
            venue_name="South Hall",
            setup_group_code="05",
            setup_group_label="Misc.",
            suggested_order_qty_snapshot=0,
            over_par_qty_snapshot=3,
            actual_ordered_qty=2,
            status="skipped",
            notes="Already over par",
        )
        db.session.commit()
        batch_id = batch.id

    response = client.get(f"/orders/{batch_id}/export.csv?status=ordered&q=tea")
    header_line = response.data.decode("utf-8-sig").splitlines()[0]
    export_rows = parse_csv_response(response)

    assert response.status_code == 200
    assert header_line == (
        "Batch Name,Batch Type,Batch Created Date,Batch Created By,Item Name,Venue Name,"
        "Setup Group Code,Setup Group Label,Count Snapshot,Par Snapshot,Suggested Order Qty,"
        "Over Par Qty,Actual Ordered Qty,Status,Line Notes"
    )
    assert export_rows == [
        {
            "Batch Name": "Export Batch",
            "Batch Type": "Quarterly",
            "Batch Created Date": export_rows[0]["Batch Created Date"],
            "Batch Created By": export_rows[0]["Batch Created By"],
            "Item Name": "Tea Lights",
            "Venue Name": "North Hall",
            "Setup Group Code": "02",
            "Setup Group Label": "Standard Signature Set-up Needs",
            "Count Snapshot": "4",
            "Par Snapshot": "9",
            "Suggested Order Qty": "6",
            "Over Par Qty": "0",
            "Actual Ordered Qty": "",
            "Status": "Ordered",
            "Line Notes": "Priority line",
        }
    ]


def test_csv_export_sanitizes_formula_like_text_fields(client, app):
    quick_login(client, "viewer")

    with app.app_context():
        batch = OrderBatch(name="=Danger Batch", batch_type="monthly")
        db.session.add(batch)
        db.session.flush()
        create_order_line(
            batch,
            item_name="=Tea Lights",
            venue_name="+North Hall",
            setup_group_code="02",
            setup_group_label="Standard Signature Set-up Needs",
            status="planned",
            notes="=cmd|' /C calc'!A0",
        )
        db.session.commit()
        batch_id = batch.id

    response = client.get(f"/orders/{batch_id}/export.csv")
    export_rows = parse_csv_response(response)

    assert response.status_code == 200
    assert export_rows == [
        {
            "Batch Name": "'=Danger Batch",
            "Batch Type": "Monthly",
            "Batch Created Date": export_rows[0]["Batch Created Date"],
            "Batch Created By": export_rows[0]["Batch Created By"],
            "Item Name": "'=Tea Lights",
            "Venue Name": "'+North Hall",
            "Setup Group Code": "02",
            "Setup Group Label": "Standard Signature Set-up Needs",
            "Count Snapshot": "4",
            "Par Snapshot": "9",
            "Suggested Order Qty": "5",
            "Over Par Qty": "0",
            "Actual Ordered Qty": "",
            "Status": "Planned",
            "Line Notes": "'=cmd|' /C calc'!A0",
        }
    ]


def test_purchase_summary_export_uses_snapshot_rollups_and_honors_filters(client, app):
    quick_login(client, "viewer")

    with app.app_context():
        batch = OrderBatch(name="Summary Export Batch", batch_type="quarterly")
        db.session.add(batch)
        db.session.flush()
        create_order_line(
            batch,
            item_id=11,
            item_name="Tea Lights",
            venue_id=21,
            venue_name="North Hall",
            setup_group_code="02",
            setup_group_label="Standard Signature Set-up Needs",
            count_snapshot=4,
            par_snapshot=10,
            suggested_order_qty_snapshot=6,
            actual_ordered_qty=2,
            status="ordered",
            notes="North note",
        )
        create_order_line(
            batch,
            item_id=11,
            item_name="Tea Lights",
            venue_id=22,
            venue_name="South Hall",
            setup_group_code="02",
            setup_group_label="Standard Signature Set-up Needs",
            count_snapshot=2,
            par_snapshot=7,
            suggested_order_qty_snapshot=5,
            over_par_qty_snapshot=1,
            actual_ordered_qty=0,
            status="planned",
            notes="South note",
        )
        create_order_line(
            batch,
            item_id=12,
            item_name="Blankets",
            venue_id=23,
            venue_name="West Hall",
            setup_group_code="05",
            setup_group_label="Misc.",
            count_snapshot=8,
            par_snapshot=8,
            suggested_order_qty_snapshot=0,
            actual_ordered_qty=0,
            status="skipped",
        )
        db.session.commit()
        batch_id = batch.id

    response = client.get(
        f"/orders/{batch_id}/purchase-summary-export.csv?status=ordered&q=tea&purchase_q=tea"
    )
    export_rows = parse_csv_response(response)

    assert response.status_code == 200
    assert export_rows == [
        {
            "Batch Name": "Summary Export Batch",
            "Batch Type": "Quarterly",
            "Item Name": "Tea Lights",
            "Setup Group Code": "02",
            "Setup Group Label": "Standard Signature Set-up Needs",
            "Contributing Venue Count": "1",
            "Contributing Line Count": "1",
            "Total Count": "4",
            "Total Par": "10",
            "Total Suggested Order": "6",
            "Total Actual Ordered": "2",
            "Total Over Par": "0",
            "Status Summary": "1 Ordered",
            "Note Count": "1",
        }
    ]


def test_full_scope_line_and_purchase_exports_ignore_detail_filters(client, app):
    quick_login(client, "viewer")

    with app.app_context():
        batch = OrderBatch(name="Full Export Batch", batch_type="monthly")
        db.session.add(batch)
        db.session.flush()
        create_order_line(
            batch,
            item_id=1,
            item_name="Tea Lights",
            venue_id=1,
            venue_name="North Hall",
            status="ordered",
            notes="First",
        )
        create_order_line(
            batch,
            item_id=2,
            item_name="Blankets",
            venue_id=2,
            venue_name="South Hall",
            status="planned",
            notes="Second",
        )
        db.session.commit()
        batch_id = batch.id

    line_response = client.get(f"/orders/{batch_id}/export.csv?status=ordered&q=tea&scope=full")
    purchase_response = client.get(
        f"/orders/{batch_id}/purchase-summary-export.csv?status=ordered&q=tea&purchase_q=tea&scope=full"
    )

    assert line_response.status_code == 200
    assert len(parse_csv_response(line_response)) == 2
    assert purchase_response.status_code == 200
    assert len(parse_csv_response(purchase_response)) == 2


def test_admin_can_create_scoped_batches_from_orders_form(client, app):
    quick_login(client, "admin")

    with app.app_context():
        north_hall = Venue(name="North Hall", active=True)
        south_hall = Venue(name="South Hall", active=True)
        db.session.add_all([north_hall, south_hall])
        db.session.flush()

        north_group_item = create_tracked_item(
            north_hall,
            "North Mats",
            default_par_level=8,
            setup_group_code="01",
            setup_group_label="Yoga/Meditation Materials",
            sort_order=1,
        )
        north_unassigned_item = create_tracked_item(
            north_hall,
            "North Props",
            default_par_level=5,
            sort_order=2,
        )
        south_group_item = create_tracked_item(
            south_hall,
            "South Mats",
            default_par_level=7,
            setup_group_code="01",
            setup_group_label="Yoga/Meditation Materials",
            sort_order=3,
        )
        south_misc_item = create_tracked_item(
            south_hall,
            "South Misc",
            default_par_level=6,
            setup_group_code="05",
            setup_group_label="Misc.",
            sort_order=4,
        )

        for venue, pairs in (
            (north_hall, [(north_group_item, "low"), (north_unassigned_item, "low")]),
            (south_hall, [(south_group_item, "low"), (south_misc_item, "low")]),
        ):
            add_status_check(
                venue,
                pairs,
                created_at=datetime.now(timezone.utc) - timedelta(days=1),
            )

        add_count_session(
            north_hall,
            [(north_group_item, 2), (north_unassigned_item, 1)],
            created_at=datetime.now(timezone.utc) - timedelta(hours=4),
        )
        add_count_session(
            south_hall,
            [(south_group_item, 3), (south_misc_item, 2)],
            created_at=datetime.now(timezone.utc) - timedelta(hours=3),
        )
        db.session.commit()
        north_hall_id = north_hall.id
        south_hall_id = south_hall.id

    default_response = client.post(
        "/orders",
        data={
            "name": "Default Scope Batch",
            "batch_type": "monthly",
            "notes": "",
        },
        follow_redirects=False,
    )
    venue_response = client.post(
        "/orders",
        data={
            "name": "North Scope Batch",
            "batch_type": "monthly",
            "notes": "",
            "scope_venue_id": str(north_hall_id),
        },
        follow_redirects=False,
    )
    group_response = client.post(
        "/orders",
        data={
            "name": "Group Scope Batch",
            "batch_type": "monthly",
            "notes": "",
            "scope_setup_group": "01",
        },
        follow_redirects=False,
    )
    unassigned_response = client.post(
        "/orders",
        data={
            "name": "Unassigned Scope Batch",
            "batch_type": "monthly",
            "notes": "",
            "scope_setup_group": ORDER_SCOPE_UNASSIGNED_VALUE,
        },
        follow_redirects=False,
    )
    combined_response = client.post(
        "/orders",
        data={
            "name": "Combined Scope Batch",
            "batch_type": "monthly",
            "notes": "",
            "scope_venue_id": str(south_hall_id),
            "scope_setup_group": "05",
        },
        follow_redirects=False,
    )

    assert default_response.status_code == 302
    assert venue_response.status_code == 302
    assert group_response.status_code == 302
    assert unassigned_response.status_code == 302
    assert combined_response.status_code == 302

    with app.app_context():
        default_batch = OrderBatch.query.filter_by(name="Default Scope Batch").first()
        venue_batch = OrderBatch.query.filter_by(name="North Scope Batch").first()
        group_batch = OrderBatch.query.filter_by(name="Group Scope Batch").first()
        unassigned_batch = OrderBatch.query.filter_by(name="Unassigned Scope Batch").first()
        combined_batch = OrderBatch.query.filter_by(name="Combined Scope Batch").first()

        default_lines = OrderLine.query.filter_by(order_batch_id=default_batch.id).all()
        venue_lines = OrderLine.query.filter_by(order_batch_id=venue_batch.id).all()
        group_lines = OrderLine.query.filter_by(order_batch_id=group_batch.id).all()
        unassigned_lines = OrderLine.query.filter_by(order_batch_id=unassigned_batch.id).all()
        combined_lines = OrderLine.query.filter_by(order_batch_id=combined_batch.id).all()

    assert {line.item_name_snapshot for line in default_lines} == {
        "North Mats",
        "North Props",
        "South Mats",
        "South Misc",
    }
    assert {line.venue_name_snapshot for line in venue_lines} == {"North Hall"}
    assert {line.item_name_snapshot for line in venue_lines} == {"North Mats", "North Props"}
    assert {line.item_name_snapshot for line in group_lines} == {"North Mats", "South Mats"}
    assert {line.item_name_snapshot for line in unassigned_lines} == {"North Props"}
    assert {line.item_name_snapshot for line in combined_lines} == {"South Misc"}
