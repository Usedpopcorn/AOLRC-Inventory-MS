from datetime import datetime, timezone
from io import BytesIO

from app import db
from app.models import Venue, VenueFile


def quick_login(client, role="admin"):
    if role == "viewer":
        role = "user"
    return client.post(
        "/login",
        data={"quick_login_role": role},
        follow_redirects=False,
    )


def create_venue(name="Venue Files Hall"):
    venue = Venue(name=name, active=True, created_at=datetime.now(timezone.utc))
    db.session.add(venue)
    db.session.commit()
    return venue.id


def upload_file(
    client,
    venue_id,
    filename="setup.pdf",
    content=b"%PDF-1.4\n",
    description="",
):
    return client.post(
        f"/venues/{venue_id}/files",
        data={
            "venue_file": (BytesIO(content), filename),
            "description": description,
            "next": "/venues",
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )


def test_admin_can_upload_and_view_venue_file(client, app, tmp_path):
    app.config["VENUE_FILE_UPLOAD_DIR"] = str(tmp_path)
    quick_login(client, "admin")

    with app.app_context():
        venue_id = create_venue()

    response = upload_file(
        client,
        venue_id,
        filename="setup-guide.pdf",
        content=b"%PDF-1.4\nAOLRC setup guide",
        description="Spring setup map",
    )

    assert response.status_code == 302
    assert "profile_tab=files" in response.headers["Location"]

    with app.app_context():
        venue_file = VenueFile.query.one()
        assert venue_file.venue_id == venue_id
        assert venue_file.original_filename == "setup-guide.pdf"
        assert venue_file.preview_type == "pdf"
        assert venue_file.description == "Spring setup map"
        assert (tmp_path / str(venue_id) / venue_file.stored_filename).exists()
        file_id = venue_file.id

    profile_response = client.get(f"/venues/{venue_id}?profile_tab=files")
    profile_body = profile_response.get_data(as_text=True)
    assert profile_response.status_code == 200
    assert "setup-guide.pdf" in profile_body
    assert "Spring setup map" in profile_body
    assert 'id="venueFileUpload"' in profile_body

    preview_response = client.get(f"/venues/{venue_id}/files/{file_id}")
    assert preview_response.status_code == 200
    assert b"PDF preview" in preview_response.data

    inline_response = client.get(f"/venues/{venue_id}/files/{file_id}/view")
    assert inline_response.status_code == 200
    assert inline_response.data.startswith(b"%PDF")
    assert inline_response.headers["X-Frame-Options"] == "SAMEORIGIN"

    download_response = client.get(f"/venues/{venue_id}/files/{file_id}/download")
    assert download_response.status_code == 200
    assert "attachment" in download_response.headers["Content-Disposition"]


def test_viewer_and_staff_cannot_upload_files(app, tmp_path):
    app.config["VENUE_FILE_UPLOAD_DIR"] = str(tmp_path)

    with app.app_context():
        venue_id = create_venue("Read Only Upload Venue")

    for role in ("viewer", "staff"):
        role_client = app.test_client()
        quick_login(role_client, role)
        response = upload_file(
            role_client,
            venue_id,
            filename=f"{role}.txt",
            content=b"blocked",
        )
        assert response.status_code == 302

    with app.app_context():
        assert VenueFile.query.count() == 0


def test_viewer_can_preview_and_download_but_not_see_admin_controls(app, tmp_path):
    app.config["VENUE_FILE_UPLOAD_DIR"] = str(tmp_path)
    admin_client = app.test_client()
    quick_login(admin_client, "admin")

    with app.app_context():
        venue_id = create_venue("Viewer File Venue")

    upload_response = upload_file(
        admin_client,
        venue_id,
        filename="inventory.csv",
        content=b"Item,Count\nTea Lights,20\n",
        description="Count sheet",
    )
    assert upload_response.status_code == 302

    with app.app_context():
        file_id = VenueFile.query.one().id

    viewer_client = app.test_client()
    quick_login(viewer_client, "viewer")

    profile_response = viewer_client.get(f"/venues/{venue_id}?profile_tab=files")
    profile_body = profile_response.get_data(as_text=True)
    assert profile_response.status_code == 200
    assert "inventory.csv" in profile_body
    assert "Files are read-only for this account." in profile_body
    assert 'id="venueFileUpload"' not in profile_body
    assert "Delete</button>" not in profile_body

    preview_response = viewer_client.get(f"/venues/{venue_id}/files/{file_id}")
    preview_body = preview_response.get_data(as_text=True)
    assert preview_response.status_code == 200
    assert "Tea Lights" in preview_body
    assert "20" in preview_body

    download_response = viewer_client.get(f"/venues/{venue_id}/files/{file_id}/download")
    assert download_response.status_code == 200
    assert b"Tea Lights" in download_response.data


def test_admin_can_delete_venue_file(client, app, tmp_path):
    app.config["VENUE_FILE_UPLOAD_DIR"] = str(tmp_path)
    quick_login(client, "admin")

    with app.app_context():
        venue_id = create_venue("Delete File Venue")

    upload_response = upload_file(
        client,
        venue_id,
        filename="remove-me.txt",
        content=b"remove me",
    )
    assert upload_response.status_code == 302

    with app.app_context():
        venue_file = VenueFile.query.one()
        stored_path = tmp_path / str(venue_id) / venue_file.stored_filename
        file_id = venue_file.id
        assert stored_path.exists()

    delete_response = client.post(
        f"/venues/{venue_id}/files/{file_id}/delete",
        data={"next": "/venues"},
        follow_redirects=False,
    )

    assert delete_response.status_code == 302

    with app.app_context():
        assert VenueFile.query.count() == 0
        assert not stored_path.exists()


def test_venue_file_routes_are_scoped_to_their_venue(client, app, tmp_path):
    app.config["VENUE_FILE_UPLOAD_DIR"] = str(tmp_path)
    quick_login(client, "admin")

    with app.app_context():
        venue_id = create_venue("Correct File Venue")
        other_venue_id = create_venue("Wrong File Venue")

    upload_response = upload_file(
        client,
        venue_id,
        filename="scoped.txt",
        content=b"venue scoped",
    )
    assert upload_response.status_code == 302

    with app.app_context():
        file_id = VenueFile.query.one().id

    scoped_response = client.get(f"/venues/{other_venue_id}/files/{file_id}")
    assert scoped_response.status_code == 404


def test_unsupported_file_type_is_rejected(client, app, tmp_path):
    app.config["VENUE_FILE_UPLOAD_DIR"] = str(tmp_path)
    quick_login(client, "admin")

    with app.app_context():
        venue_id = create_venue("Unsupported File Venue")

    response = upload_file(client, venue_id, filename="script.exe", content=b"not allowed")

    assert response.status_code == 302
    with app.app_context():
        assert VenueFile.query.count() == 0
