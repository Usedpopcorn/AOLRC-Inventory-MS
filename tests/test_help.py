def quick_login(client, role):
    quick_role = "user" if role == "viewer" else role
    return client.post(
        "/login",
        data={"quick_login_role": quick_role},
        follow_redirects=False,
    )


def test_help_requires_authentication(client):
    response = client.get("/help", follow_redirects=False)

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_help_route_allows_viewer_staff_and_admin(client):
    for role in ("viewer", "staff", "admin"):
        login_response = quick_login(client, role)
        assert login_response.status_code == 302

        response = client.get("/help")
        assert response.status_code == 200
        assert b"Help and Workflow Guide" in response.data
        assert b"Quick Status vs Raw Counts" in response.data
        assert b"Recommended Workflows" in response.data
        assert b"Supplies Audit Guide" in response.data
        assert b"Orders Guide" in response.data
        assert b"Admin Guide" in response.data
        assert b"Troubleshooting and FAQ" in response.data
        assert b"dashboard-venue-status.png" in response.data
        assert b"quick-check-raw-counts.png" in response.data
        assert b"admin-inventory-rules.png" in response.data
        assert b"Raw Count Data" in response.data
        assert b"Guide Topics" in response.data
        assert b"Help workbook sections" in response.data
        assert b'data-bs-toggle="pill"' in response.data

        dashboard_response = client.get("/dashboard")
        assert dashboard_response.status_code == 200
        assert b'href="/help"' in dashboard_response.data

        client.post("/logout")
