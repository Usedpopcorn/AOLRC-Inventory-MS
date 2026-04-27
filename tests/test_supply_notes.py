from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from app import db
from app.models import Item, SupplyNote, User, Venue, VenueItem, VenueItemCount
from app.routes.supplies import build_supply_audit_rows
from app.services.notes import NOTE_BODY_MAX_LENGTH, SUPPLY_NOTES_PAGE_SIZE


def quick_login(client, role="staff"):
    if role == "viewer":
        role = "user"
    return client.post(
        "/login",
        data={"quick_login_role": role},
        follow_redirects=False,
    )


def create_supply_item(
    name,
    *,
    active=True,
    is_group_parent=False,
    parent_item=None,
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
        parent_item_id=parent_item.id if parent_item else None,
        sort_order=0,
        created_at=datetime.now(timezone.utc),
    )
    db.session.add(item)
    db.session.flush()
    return item


def get_seed_user(email, role):
    user = User.query.filter_by(email=email).first()
    if user is None:
        user = User(
            email=email,
            display_name=email.split("@", 1)[0].replace(".", " ").title(),
            password_hash="hash",
            role=role,
            active=True,
            created_at=datetime.now(timezone.utc),
            password_changed_at=datetime.now(timezone.utc),
        )
        db.session.add(user)
        db.session.flush()
    return user


def test_supplies_can_create_global_note_for_active_item(client, app):
    quick_login(client, "staff")

    with app.app_context():
        item = create_supply_item("Supply Note Candles")
        db.session.commit()
        item_id = item.id

    response = client.post(
        "/supplies",
        data={
            "action": "create_note",
            "item_id": str(item_id),
            "title": "Storage reminder",
            "body": "Keep extra cartons in the upstairs closet.",
            "q": "candles",
            "sort": "item_name",
            "quick_filter": "consumable",
            "note_item_id": str(item_id),
            "note_focus": "compose",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    params = parse_qs(urlparse(response.headers["Location"]).query)
    assert params["q"] == ["candles"]
    assert params["sort"] == ["item_name"]
    assert params["quick_filter"] == ["consumable"]
    assert params["note_item_id"] == [str(item_id)]
    assert params["note_focus"] == ["list"]

    with app.app_context():
        note = SupplyNote.query.one()
        assert note.item_id == item_id
        assert note.title == "Storage reminder"
        assert note.body == "Keep extra cartons in the upstairs closet."


def test_viewer_cannot_create_supply_note(client, app):
    quick_login(client, "viewer")

    with app.app_context():
        item = create_supply_item("Viewer Supply Note Block")
        db.session.commit()
        item_id = item.id

    response = client.post(
        "/supplies",
        data={
            "action": "create_note",
            "item_id": str(item_id),
            "title": "Blocked",
            "body": "Viewer should not be able to add this.",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Only staff and admins can manage notes." in response.data

    with app.app_context():
        assert SupplyNote.query.count() == 0


def test_staff_cannot_edit_or_delete_another_users_supply_note(client, app):
    quick_login(client, "staff")

    with app.app_context():
        item = create_supply_item("Staff Guardrail Supply Note")
        admin_author = get_seed_user("admin@example.com", "admin")
        note = SupplyNote(
            item_id=item.id,
            author_user_id=admin_author.id,
            title="Admin-authored supply note",
            body="Do not overwrite this.",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db.session.add(note)
        db.session.commit()
        note_id = note.id
        item_id = item.id

    edit_response = client.post(
        "/supplies",
        data={
            "action": "edit_note",
            "note_id": str(note_id),
            "title": "Staff takeover",
            "body": "This should not stick.",
            "note_item_id": str(item_id),
            "note_focus": "list",
        },
        follow_redirects=True,
    )

    assert edit_response.status_code == 200
    assert b"You can only edit or delete your own notes." in edit_response.data

    delete_response = client.post(
        "/supplies",
        data={
            "action": "delete_note",
            "note_id": str(note_id),
            "note_item_id": str(item_id),
            "note_focus": "list",
        },
        follow_redirects=True,
    )

    assert delete_response.status_code == 200
    assert b"You can only edit or delete your own notes." in delete_response.data

    with app.app_context():
        note = db.session.get(SupplyNote, note_id)
        assert note is not None
        assert note.title == "Admin-authored supply note"
        assert note.body == "Do not overwrite this."


def test_admin_can_edit_and_delete_any_supply_note(client, app):
    quick_login(client, "admin")

    with app.app_context():
        item = create_supply_item("Admin Control Supply Note")
        staff_author = get_seed_user("staff@example.com", "staff")
        note = SupplyNote(
            item_id=item.id,
            author_user_id=staff_author.id,
            title="Staff-authored note",
            body="Original body",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db.session.add(note)
        db.session.commit()
        note_id = note.id
        item_id = item.id

    edit_response = client.post(
        "/supplies",
        data={
            "action": "edit_note",
            "note_id": str(note_id),
            "title": "Admin updated note",
            "body": "Admin can edit any supply note.",
            "q": "control",
            "sort": "item_name",
            "quick_filter": "consumable",
            "note_item_id": str(item_id),
            "note_focus": "list",
        },
        follow_redirects=False,
    )

    assert edit_response.status_code == 302
    edit_params = parse_qs(urlparse(edit_response.headers["Location"]).query)
    assert edit_params["q"] == ["control"]
    assert edit_params["sort"] == ["item_name"]
    assert edit_params["quick_filter"] == ["consumable"]
    assert edit_params["note_item_id"] == [str(item_id)]
    assert edit_params["note_focus"] == ["list"]

    with app.app_context():
        note = db.session.get(SupplyNote, note_id)
        assert note.title == "Admin updated note"
        assert note.body == "Admin can edit any supply note."

    delete_response = client.post(
        "/supplies",
        data={
            "action": "delete_note",
            "note_id": str(note_id),
            "q": "control",
            "sort": "item_name",
            "quick_filter": "consumable",
            "note_item_id": str(item_id),
            "note_focus": "list",
        },
        follow_redirects=False,
    )

    assert delete_response.status_code == 302
    delete_params = parse_qs(urlparse(delete_response.headers["Location"]).query)
    assert delete_params["q"] == ["control"]
    assert delete_params["sort"] == ["item_name"]
    assert delete_params["quick_filter"] == ["consumable"]
    assert delete_params["note_item_id"] == [str(item_id)]
    assert delete_params["note_focus"] == ["list"]

    with app.app_context():
        assert SupplyNote.query.count() == 0


@pytest.mark.parametrize("invalid_selector", ["not_a_number", "group_parent", "inactive"])
def test_supplies_reject_invalid_note_item_ids(client, app, invalid_selector):
    quick_login(client, "staff")

    with app.app_context():
        group_parent = create_supply_item(
            f"Supply Note Parent {invalid_selector}",
            is_group_parent=True,
        )
        inactive_item = create_supply_item(
            f"Supply Note Inactive {invalid_selector}",
            active=False,
        )
        active_item = create_supply_item(f"Supply Note Active {invalid_selector}")
        db.session.commit()
        invalid_value_map = {
            "not_a_number": "not-a-number",
            "group_parent": str(group_parent.id),
            "inactive": str(inactive_item.id),
        }
        active_item_id = active_item.id

    response = client.post(
        "/supplies",
        data={
            "action": "create_note",
            "item_id": invalid_value_map[invalid_selector],
            "title": "Bad target",
            "body": "This should fail.",
            "note_item_id": str(active_item_id),
            "note_focus": "compose",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Select an active supply item before adding a note." in response.data

    with app.app_context():
        assert SupplyNote.query.count() == 0


def test_supplies_page_renders_note_affordances_and_modal_shell(client, app):
    quick_login(client, "staff")

    with app.app_context():
        family_parent = create_supply_item("Supply Note Family Parent", is_group_parent=True)
        child_with_note = create_supply_item(
            "Supply Note Family Child One",
            parent_item=family_parent,
        )
        child_without_note = create_supply_item(
            "Supply Note Family Child Two",
            parent_item=family_parent,
        )
        standalone_item = create_supply_item("Supply Note Standalone")
        author = get_seed_user("staff@example.com", "staff")
        db.session.add(
            SupplyNote(
                item_id=child_with_note.id,
                author_user_id=author.id,
                title="Child note",
                body="Child-specific note body.",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        )
        db.session.add(
            SupplyNote(
                item_id=standalone_item.id,
                author_user_id=author.id,
                title="Standalone note",
                body="Standalone note body.",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        )
        db.session.commit()
        family_parent_id = family_parent.id
        child_with_note_id = child_with_note.id
        child_without_note_name = child_without_note.name

    response = client.get(f"/supplies?note_item_id={child_with_note_id}&note_focus=list")

    assert response.status_code == 200
    assert f'data-active-note-item-id="{child_with_note_id}"'.encode() in response.data
    assert f'data-supply-note-item-id="{family_parent_id}"'.encode() not in response.data
    assert f'data-supply-note-item-id="{child_with_note_id}"'.encode() in response.data
    assert f'aria-label="Add note for {child_without_note_name}"'.encode() in response.data
    assert b'id="supplyNotesModal"' in response.data
    assert b'data-modal-endpoint="/supplies/notes/modal"' in response.data
    assert b'id="supplyNotesDrawer"' not in response.data
    assert b"Child note" not in response.data
    assert b"static/js/supply_notes.js" in response.data


def test_supplies_page_renders_numeric_overflow_hooks_for_count_par_and_family_rows(client, app):
    quick_login(client, "staff")

    with app.app_context():
        venue = Venue(name="Overflow Venue", active=True)
        family_parent = create_supply_item("Overflow Family", is_group_parent=True)
        family_child = create_supply_item(
            "Overflow Family Child",
            parent_item=family_parent,
        )
        standalone_item = create_supply_item("Overflow Standalone")
        standalone_item.default_par_level = 333333333333
        db.session.add(venue)
        db.session.flush()
        db.session.add_all(
            [
                VenueItem(
                    venue_id=venue.id,
                    item_id=family_child.id,
                    expected_qty=222222222222,
                    active=True,
                ),
                VenueItem(
                    venue_id=venue.id,
                    item_id=standalone_item.id,
                    expected_qty=444444444444,
                    active=True,
                ),
                VenueItemCount(
                    venue_id=venue.id,
                    item_id=family_child.id,
                    raw_count=111111111111,
                ),
                VenueItemCount(
                    venue_id=venue.id,
                    item_id=standalone_item.id,
                    raw_count=999999999999,
                ),
            ]
        )
        db.session.commit()

    response = client.get("/supplies")

    assert response.status_code == 200
    assert (
        b'class="supply-value-strong supply-overflow-number js-supply-fit-number"'
        in response.data
    )
    assert b'data-supply-fit-kind="count"' in response.data
    assert b'data-supply-fit-kind="par"' in response.data
    assert b'data-supply-fit-kind="family-count"' in response.data
    assert b'data-supply-fit-full-value="999999999999"' in response.data
    assert b'data-supply-fit-full-value="444444444444"' in response.data
    assert b'data-supply-fit-full-value="1"' in response.data


def test_supply_notes_modal_returns_selected_item_notes(client, app):
    quick_login(client, "staff")

    with app.app_context():
        item_with_note = create_supply_item("Modal Supply Note Item")
        other_item = create_supply_item("Other Modal Supply Item")
        author = get_seed_user("staff@example.com", "staff")
        db.session.add(
            SupplyNote(
                item_id=item_with_note.id,
                author_user_id=author.id,
                title="Modal note",
                body="Only this item's notes should appear.",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        )
        db.session.add(
            SupplyNote(
                item_id=other_item.id,
                author_user_id=author.id,
                title="Other note",
                body="This should stay out of the selected modal.",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        )
        db.session.commit()
        item_with_note_id = item_with_note.id

    response = client.get(f"/supplies/notes/modal?item_id={item_with_note_id}&note_focus=list")

    assert response.status_code == 200
    assert f'data-note-item-id="{item_with_note_id}"'.encode() in response.data
    assert b"Modal note" in response.data
    assert b"Only this item" in response.data
    assert b"notes should appear." in response.data
    assert b"Other note" not in response.data
    assert b'id="supplyNoteComposerCollapse"' in response.data


def test_viewer_sees_supply_note_modal_as_read_only(client, app):
    quick_login(client, "viewer")

    with app.app_context():
        item_with_note = create_supply_item("Viewer Supply Note Item")
        item_without_note = create_supply_item("Viewer No Note Item")
        author = get_seed_user("staff@example.com", "staff")
        db.session.add(
            SupplyNote(
                item_id=item_with_note.id,
                author_user_id=author.id,
                title="Viewer-visible note",
                body="Read-only note body.",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        )
        db.session.commit()
        item_with_note_id = item_with_note.id
        item_without_note_name = item_without_note.name

    page_response = client.get(f"/supplies?note_item_id={item_with_note_id}&note_focus=list")
    modal_response = client.get(
        f"/supplies/notes/modal?item_id={item_with_note_id}&note_focus=list"
    )

    assert page_response.status_code == 200
    assert f'data-supply-note-item-id="{item_with_note_id}"'.encode() in page_response.data
    assert f'aria-label="Add note for {item_without_note_name}"'.encode() not in page_response.data

    assert modal_response.status_code == 200
    assert b"Notes are read-only on viewer accounts." in modal_response.data
    assert b"Viewer-visible note" in modal_response.data
    assert b'id="supplyNoteComposerCollapse"' not in modal_response.data


def test_supply_notes_modal_rejects_overlong_bodies(client, app):
    quick_login(client, "staff")

    with app.app_context():
        item = create_supply_item("Supply Note Length Guardrail")
        db.session.commit()
        item_id = item.id

    response = client.post(
        "/supplies/notes/modal",
        data={
            "action": "create_note",
            "item_id": str(item_id),
            "title": "Too long",
            "body": "x" * (NOTE_BODY_MAX_LENGTH + 1),
            "note_page": "1",
        },
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert (
        f"Note body must be {NOTE_BODY_MAX_LENGTH:,} characters or fewer.".encode()
        in response.data
    )

    with app.app_context():
        assert SupplyNote.query.count() == 0


def test_supply_notes_modal_paginates_large_note_histories(client, app):
    quick_login(client, "staff")

    with app.app_context():
        item = create_supply_item("Supply Note Pagination Item")
        author = get_seed_user("staff@example.com", "staff")
        for index in range(SUPPLY_NOTES_PAGE_SIZE + 3):
            db.session.add(
                SupplyNote(
                    item_id=item.id,
                    author_user_id=author.id,
                    title=f"Pagination note {index:02d}",
                    body=f"Body for pagination note {index:02d}.",
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )
            )
        db.session.commit()
        item_id = item.id

    response = client.get(f"/supplies/notes/modal?item_id={item_id}&note_page=2&note_focus=list")

    assert response.status_code == 200
    assert b"Page 2 of 2" in response.data
    assert b"Showing 9-11 of 11 notes" in response.data
    assert b'supply-note-entry-toggle' in response.data
    assert b'Pagination note 00' in response.data
    assert b'Pagination note 10' not in response.data


def test_build_supply_audit_rows_includes_note_counts(app):
    with app.app_context():
        item_with_note = create_supply_item("Supply Row Count Item")
        item_without_note = create_supply_item("Supply Row No Count Item")
        author = get_seed_user("staff@example.com", "staff")
        db.session.add(
            SupplyNote(
                item_id=item_with_note.id,
                author_user_id=author.id,
                title="Counted note",
                body="Included in aggregate count.",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        )
        db.session.commit()
        item_with_note_id = item_with_note.id
        item_without_note_id = item_without_note.id

        rows_by_id = {row["id"]: row for row in build_supply_audit_rows()}

    assert rows_by_id[item_with_note_id]["notes_count"] == 1
    assert rows_by_id[item_with_note_id]["has_notes"] is True
    assert rows_by_id[item_without_note_id]["notes_count"] == 0
    assert rows_by_id[item_without_note_id]["has_notes"] is False
