from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from app import db
from app.models import Item, User, Venue, VenueItem, VenueNote
from app.services.admin_hub import build_admin_history_view_model
from app.services.notes import NOTE_BODY_MAX_LENGTH, VENUE_NOTES_PAGE_SIZE


def quick_login(client, role="staff"):
    if role == "viewer":
        role = "user"
    return client.post(
        "/login",
        data={"quick_login_role": role},
        follow_redirects=False,
    )


def create_tracked_item(
    venue,
    name,
    *,
    active=True,
    is_group_parent=False,
    tracking_mode="quantity",
    item_category="consumable",
):
    item = Item(
        name=name,
        item_type="consumable",
        tracking_mode=tracking_mode,
        item_category=item_category,
        active=active,
        is_group_parent=is_group_parent,
        sort_order=0,
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


def test_venue_detail_can_create_general_note(client, app):
    quick_login(client, "staff")

    with app.app_context():
        venue = Venue(name="General Notes Hall", active=True, created_at=datetime.now(timezone.utc))
        db.session.add(venue)
        db.session.commit()
        venue_id = venue.id

    response = client.post(
        f"/venues/{venue_id}",
        data={
            "action": "create_note",
            "title": "General setup",
            "body": "Remember the hallway lanterns.",
            "item_id": "",
            "next": "/venues",
            "profile_tab": "notes",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    location = response.headers["Location"]
    params = parse_qs(urlparse(location).query)
    assert params["profile_tab"] == ["notes"]
    assert "note_item_id" not in params

    with app.app_context():
        note = VenueNote.query.one()
        assert note.venue_id == venue_id
        assert note.item_id is None
        assert note.title == "General setup"


def test_venue_detail_can_create_item_note_for_tracked_item(client, app):
    quick_login(client, "staff")

    with app.app_context():
        venue = Venue(name="Item Notes Hall", active=True, created_at=datetime.now(timezone.utc))
        db.session.add(venue)
        db.session.flush()
        item = create_tracked_item(venue, "Meditation Shawls")
        db.session.commit()
        venue_id = venue.id
        item_id = item.id

    response = client.post(
        f"/venues/{venue_id}",
        data={
            "action": "create_note",
            "title": "Shawl restock",
            "body": "Check folded extras in the closet.",
            "item_id": str(item_id),
            "next": "/venues",
            "profile_tab": "notes",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    location = response.headers["Location"]
    params = parse_qs(urlparse(location).query)
    assert params["profile_tab"] == ["notes"]
    assert params["note_item_id"] == [str(item_id)]
    assert params["note_focus"] == ["list"]

    with app.app_context():
        note = VenueNote.query.one()
        assert note.item_id == item_id
        assert note.title == "Shawl restock"


def test_view_only_user_sees_read_only_message_and_no_composer(client, app):
    quick_login(client, "viewer")

    with app.app_context():
        venue = Venue(name="Viewer Notes Hall", active=True, created_at=datetime.now(timezone.utc))
        db.session.add(venue)
        db.session.commit()
        venue_id = venue.id

    response = client.get(f"/venues/{venue_id}?profile_tab=notes")

    assert response.status_code == 200
    assert b'id="venueNoteComposer"' not in response.data
    assert b"Notes are read-only on viewer accounts." in response.data
    assert (
        b"Viewers can browse existing notes but cannot create, edit, or delete them."
        in response.data
    )


def test_view_only_user_cannot_create_note(client, app):
    quick_login(client, "viewer")

    with app.app_context():
        venue = Venue(
            name="Viewer Create Block Hall",
            active=True,
            created_at=datetime.now(timezone.utc),
        )
        db.session.add(venue)
        db.session.commit()
        venue_id = venue.id

    response = client.post(
        f"/venues/{venue_id}",
        data={
            "action": "create_note",
            "title": "Viewer note",
            "body": "Guests asked for extra blankets near the door.",
            "item_id": "",
            "next": "/venues",
            "profile_tab": "notes",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Only staff and admins can manage notes." in response.data

    with app.app_context():
        assert VenueNote.query.count() == 0


def test_venue_detail_rejects_overlong_note_bodies(client, app):
    quick_login(client, "staff")

    with app.app_context():
        venue = Venue(
            name="Venue Note Length Guardrail",
            active=True,
            created_at=datetime.now(timezone.utc),
        )
        db.session.add(venue)
        db.session.commit()
        venue_id = venue.id

    response = client.post(
        f"/venues/{venue_id}",
        data={
            "action": "create_note",
            "title": "Too long body",
            "body": "x" * (NOTE_BODY_MAX_LENGTH + 1),
            "item_id": "",
            "next": "/venues",
            "profile_tab": "notes",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert (
        f"Note body must be {NOTE_BODY_MAX_LENGTH:,} characters or fewer.".encode()
        in response.data
    )

    with app.app_context():
        assert VenueNote.query.count() == 0


def test_staff_cannot_edit_or_delete_someone_elses_note(client, app):
    quick_login(client, "staff")

    with app.app_context():
        venue = Venue(
            name="Staff Guardrail Hall",
            active=True,
            created_at=datetime.now(timezone.utc),
        )
        db.session.add(venue)
        db.session.flush()
        admin_author = User.query.filter_by(email="admin@example.com").first()
        if admin_author is None:
            admin_author = User(
                email="admin@example.com",
                display_name="Admin Author",
                password_hash="hash",
                role="admin",
                active=True,
                created_at=datetime.now(timezone.utc),
                password_changed_at=datetime.now(timezone.utc),
            )
            db.session.add(admin_author)
            db.session.flush()
        note = VenueNote(
            venue_id=venue.id,
            author_user_id=admin_author.id,
            title="Admin-authored note",
            body="Admin-authored body",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db.session.add(note)
        db.session.commit()
        venue_id = venue.id
        note_id = note.id

    response = client.post(
        f"/venues/{venue_id}",
        data={
            "action": "edit_note",
            "note_id": str(note_id),
            "title": "Staff takeover",
            "body": "This should not stick.",
            "item_id": "",
            "next": "/venues",
            "profile_tab": "notes",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"You can only edit or delete your own notes." in response.data

    with app.app_context():
        note = db.session.get(VenueNote, note_id)
        assert note.title == "Admin-authored note"
        assert note.body == "Admin-authored body"


def test_admin_can_edit_someone_elses_note(client, app):
    quick_login(client, "admin")

    with app.app_context():
        venue = Venue(
            name="Admin Note Control Hall",
            active=True,
            created_at=datetime.now(timezone.utc),
        )
        db.session.add(venue)
        db.session.flush()
        staff_author = User.query.filter_by(email="staff@example.com").first()
        if staff_author is None:
            staff_author = User(
                email="staff@example.com",
                display_name="Staff Author",
                password_hash="hash",
                role="staff",
                active=True,
                created_at=datetime.now(timezone.utc),
                password_changed_at=datetime.now(timezone.utc),
            )
            db.session.add(staff_author)
            db.session.flush()
        note = VenueNote(
            venue_id=venue.id,
            author_user_id=staff_author.id,
            title="Staff note",
            body="Staff-authored body",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db.session.add(note)
        db.session.commit()
        venue_id = venue.id
        note_id = note.id

    response = client.post(
        f"/venues/{venue_id}",
        data={
            "action": "edit_note",
            "note_id": str(note_id),
            "title": "Admin updated note",
            "body": "Admin can edit any note.",
            "item_id": "",
            "next": "/venues",
            "profile_tab": "notes",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302

    with app.app_context():
        note = db.session.get(VenueNote, note_id)
        assert note.title == "Admin updated note"
        assert note.body == "Admin can edit any note."


def test_view_only_user_cannot_edit_someone_elses_note(client, app):
    quick_login(client, "viewer")

    with app.app_context():
        venue = Venue(
            name="Viewer Guardrail Hall",
            active=True,
            created_at=datetime.now(timezone.utc),
        )
        db.session.add(venue)
        db.session.flush()
        staff_author = User.query.filter_by(email="staff@example.com").first()
        if staff_author is None:
            staff_author = User(
                email="staff@example.com",
                display_name="Staff Author",
                password_hash="hash",
                role="staff",
                active=True,
                created_at=datetime.now(timezone.utc),
                password_changed_at=datetime.now(timezone.utc),
            )
            db.session.add(staff_author)
            db.session.flush()
        note = VenueNote(
            venue_id=venue.id,
            author_user_id=staff_author.id,
            title="Staff note",
            body="Staff-only authored note",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db.session.add(note)
        db.session.commit()
        venue_id = venue.id
        note_id = note.id

    response = client.post(
        f"/venues/{venue_id}",
        data={
            "action": "edit_note",
            "note_id": str(note_id),
            "title": "Viewer takeover",
            "body": "This should not stick.",
            "item_id": "",
            "next": "/venues",
            "profile_tab": "notes",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Only staff and admins can manage notes." in response.data

    with app.app_context():
        note = db.session.get(VenueNote, note_id)
        assert note.title == "Staff note"
        assert note.body == "Staff-only authored note"


@pytest.mark.parametrize(
    "invalid_selector",
    ["not_a_number", "untracked", "group_parent", "inactive"],
)
def test_venue_detail_rejects_invalid_item_tag_values(client, app, invalid_selector):
    quick_login(client, "staff")

    with app.app_context():
        venue = Venue(
            name=f"Invalid Item Venue {invalid_selector}",
            active=True,
            created_at=datetime.now(timezone.utc),
        )
        db.session.add(venue)
        db.session.flush()
        create_tracked_item(venue, f"Tracked Supply {invalid_selector}")
        untracked_item = Item(
            name=f"Untracked Supply {invalid_selector}",
            item_type="consumable",
            tracking_mode="quantity",
            item_category="consumable",
            active=True,
            created_at=datetime.now(timezone.utc),
        )
        db.session.add(untracked_item)
        db.session.flush()
        group_parent = create_tracked_item(
            venue,
            f"Family Group {invalid_selector}",
            is_group_parent=True,
        )
        inactive_item = create_tracked_item(
            venue,
            f"Inactive Supply {invalid_selector}",
            active=False,
        )
        db.session.commit()
        venue_id = venue.id
        invalid_value_map = {
            "not_a_number": "not-a-number",
            "untracked": str(untracked_item.id),
            "group_parent": str(group_parent.id),
            "inactive": str(inactive_item.id),
        }

    response = client.post(
        f"/venues/{venue_id}",
        data={
            "action": "create_note",
            "title": "Bad target",
            "body": "This should fail.",
            "item_id": invalid_value_map[invalid_selector],
            "next": "/venues",
            "profile_tab": "notes",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert (
        b"Select a tracked venue item or leave the note as a general venue note."
        in response.data
    )

    with app.app_context():
        assert VenueNote.query.count() == 0


def test_venue_detail_edit_note_can_change_and_clear_item_tag(client, app):
    quick_login(client, "staff")

    with app.app_context():
        venue = Venue(name="Edit Note Hall", active=True, created_at=datetime.now(timezone.utc))
        db.session.add(venue)
        db.session.flush()
        item_one = create_tracked_item(venue, "Bolsters")
        item_two = create_tracked_item(venue, "Blankets")
        author = User.query.filter_by(email="staff@example.com").first()
        note = VenueNote(
            venue_id=venue.id,
            author_user_id=author.id,
            item_id=item_one.id,
            title="Initial tagged note",
            body="Original body",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db.session.add(note)
        db.session.commit()
        venue_id = venue.id
        note_id = note.id
        item_two_id = item_two.id

    response = client.post(
        f"/venues/{venue_id}",
        data={
            "action": "edit_note",
            "note_id": str(note_id),
            "title": "Retagged note",
            "body": "Now aimed at blankets.",
            "item_id": str(item_two_id),
            "next": "/venues",
            "profile_tab": "notes",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302

    with app.app_context():
        note = db.session.get(VenueNote, note_id)
        assert note.item_id == item_two_id
        assert note.title == "Retagged note"

    clear_response = client.post(
        f"/venues/{venue_id}",
        data={
            "action": "edit_note",
            "note_id": str(note_id),
            "title": "Generalized note",
            "body": "No item tag now.",
            "item_id": "",
            "next": "/venues",
            "profile_tab": "notes",
        },
        follow_redirects=False,
    )

    assert clear_response.status_code == 302

    with app.app_context():
        note = db.session.get(VenueNote, note_id)
        assert note.item_id is None
        assert note.title == "Generalized note"


def test_venue_detail_renders_note_chips_counts_and_deep_links(client, app):
    quick_login(client, "staff")

    with app.app_context():
        venue = Venue(
            name="Rendered Notes Hall",
            active=True,
            created_at=datetime.now(timezone.utc),
        )
        db.session.add(venue)
        db.session.flush()
        item = create_tracked_item(venue, "Tea Lights")
        author = User.query.filter_by(email="staff@example.com").first()
        db.session.add(
            VenueNote(
                venue_id=venue.id,
                author_user_id=author.id,
                title="Venue walkthrough",
                body="General setup reminder.",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        )
        db.session.add(
            VenueNote(
                venue_id=venue.id,
                author_user_id=author.id,
                item_id=item.id,
                title="Tea light note",
                body="Store backups in the lower drawer.",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        )
        db.session.commit()
        venue_id = venue.id
        item_id = item.id

    response = client.get(
        f"/venues/{venue_id}?profile_tab=notes&note_item_id={item_id}&note_focus=list"
    )

    assert response.status_code == 200
    assert b'id="venueNoteSearch"' in response.data
    assert b'id="venueNoteItemFilter"' in response.data
    assert b'id="venueNoteComposerCollapse"' in response.data
    assert b"Add Note" in response.data
    assert b"Item note" in response.data
    assert b"Tea Lights" in response.data
    assert b"1 note" in response.data
    assert b'aria-label="View note' not in response.data
    assert f'data-active-note-item-id="{item_id}"'.encode() in response.data
    assert f"note_item_id={item_id}".encode() in response.data
    assert b"note_focus=compose" in response.data
    assert b"note_focus=list" in response.data


def test_venue_notes_are_paginated_for_large_histories(client, app):
    quick_login(client, "staff")

    with app.app_context():
        venue = Venue(
            name="Venue Notes Pagination Hall",
            active=True,
            created_at=datetime.now(timezone.utc),
        )
        db.session.add(venue)
        db.session.flush()
        author = User.query.filter_by(email="staff@example.com").first()
        for index in range(VENUE_NOTES_PAGE_SIZE + 2):
            db.session.add(
                VenueNote(
                    venue_id=venue.id,
                    author_user_id=author.id,
                    title=f"Venue pagination note {index:02d}",
                    body=f"Venue note body {index:02d}",
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )
            )
        db.session.commit()
        venue_id = venue.id

    response = client.get(f"/venues/{venue_id}?profile_tab=notes&note_page=2")

    assert response.status_code == 200
    assert b"Page 2 of 2" in response.data
    assert b"Showing 13-14 of 14 notes" in response.data
    assert b"Venue pagination note 00" in response.data
    assert b"Venue pagination note 13" not in response.data


def test_venue_note_search_filters_before_pagination(client, app):
    quick_login(client, "staff")

    with app.app_context():
        venue = Venue(
            name="Venue Note Search Hall",
            active=True,
            created_at=datetime.now(timezone.utc),
        )
        db.session.add(venue)
        db.session.flush()
        author = User.query.filter_by(email="staff@example.com").first()
        for index in range(VENUE_NOTES_PAGE_SIZE + 1):
            db.session.add(
                VenueNote(
                    venue_id=venue.id,
                    author_user_id=author.id,
                    title=f"Routine note {index:02d}",
                    body="General venue housekeeping.",
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )
            )
        db.session.add(
            VenueNote(
                venue_id=venue.id,
                author_user_id=author.id,
                title="Special candle follow-up",
                body="Check the candle cart after events.",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        )
        db.session.commit()
        venue_id = venue.id

    response = client.get(f"/venues/{venue_id}?profile_tab=notes&note_q=candle")

    assert response.status_code == 200
    assert b"Special candle follow-up" in response.data
    assert b"Routine note 00" not in response.data
    assert b"Showing 1-1 of 1 note" in response.data


def test_admin_history_labels_item_note_updates(app):
    with app.app_context():
        venue = Venue(name="History Item Venue", active=True, created_at=datetime.now(timezone.utc))
        db.session.add(venue)
        db.session.flush()
        item = Item(
            name="History Candles",
            item_type="consumable",
            tracking_mode="quantity",
            item_category="consumable",
            active=True,
            created_at=datetime.now(timezone.utc),
        )
        db.session.add(item)
        db.session.flush()
        author = User(
            email="item-history@example.com",
            display_name="Item History",
            password_hash="hash",
            role="admin",
            active=True,
            created_at=datetime.now(timezone.utc),
            password_changed_at=datetime.now(timezone.utc),
        )
        db.session.add(author)
        db.session.flush()
        db.session.add(
            VenueNote(
                venue_id=venue.id,
                author_user_id=author.id,
                item_id=item.id,
                title="Item-specific history",
                body="Retained detail",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        )
        db.session.commit()

        view_model = build_admin_history_view_model()

    assert view_model["note_updates"]["preview"][0]["title"].startswith("Added item note")
    assert "History Candles" in view_model["note_updates"]["preview"][0]["detail"]
