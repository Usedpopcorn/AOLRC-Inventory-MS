from app.models import FeedbackSubmission
from app.services.admin_hub import build_admin_overview_view_model


def quick_login(client, role):
    return client.post(
        "/login",
        data={"quick_login_role": role},
        follow_redirects=False,
    )


def submit_feedback(
    client,
    *,
    submission_type,
    summary,
    body,
    next_path="/dashboard",
    source_path="/dashboard",
    source_query="",
    is_anonymous=False,
    follow_redirects=True,
):
    payload = {
        "submission_type": submission_type,
        "summary": summary,
        "body": body,
        "next": next_path,
        "source_path": source_path,
        "source_query": source_query,
    }
    if is_anonymous:
        payload["is_anonymous"] = "1"
    return client.post(
        "/feedback-submissions",
        data=payload,
        follow_redirects=follow_redirects,
    )


def unlock_feedback_inbox(client, pin, *, follow_redirects=True):
    return client.post(
        "/admin/feedback/pin",
        data={"review_pin": pin},
        follow_redirects=follow_redirects,
    )


def test_footer_feedback_links_render_only_for_authenticated_users(client):
    signed_out_response = client.get("/login")

    assert signed_out_response.status_code == 200
    assert b"feedbackSubmitModal" not in signed_out_response.data
    assert b"data-feedback-trigger" not in signed_out_response.data

    quick_login(client, "user")
    signed_in_response = client.get("/dashboard")

    assert signed_in_response.status_code == 200
    assert b"feedbackSubmitModal" in signed_in_response.data
    assert b'data-submission-type="feedback"' in signed_in_response.data
    assert b'data-submission-type="bug_report"' in signed_in_response.data
    assert b"feedbackAnonymousInput" in signed_in_response.data


def test_feedback_submission_validation_and_anonymous_storage(client, app):
    quick_login(client, "user")

    invalid_response = submit_feedback(
        client,
        submission_type="feedback",
        summary="",
        body="Missing summary",
    )

    assert invalid_response.status_code == 200
    assert b"A short summary is required." in invalid_response.data
    with app.app_context():
        assert FeedbackSubmission.query.count() == 0

    valid_response = submit_feedback(
        client,
        submission_type="feedback",
        summary="Lighting layout note",
        body="The page feels good, but the footer links could be more visible.",
        source_path="/venues/4",
        source_query="profile_tab=notes",
        is_anonymous=True,
    )

    assert valid_response.status_code == 200
    assert b"Feedback submitted." in valid_response.data
    with app.app_context():
        submission = FeedbackSubmission.query.one()
        assert submission.submission_type == "feedback"
        assert submission.summary == "Lighting layout note"
        assert submission.is_anonymous is True
        assert submission.submitter_user_id is not None
        assert submission.source_path == "/venues/4"
        assert submission.source_query == "profile_tab=notes"


def test_bug_report_submission_ignores_anonymous_toggle(client, app):
    quick_login(client, "staff")

    response = submit_feedback(
        client,
        submission_type="bug_report",
        summary="Quick check save bug",
        body="The save button becomes disabled after the first edit.",
        source_path="/venues/2/quick-check",
        is_anonymous=True,
    )

    assert response.status_code == 200
    assert b"Bug report submitted." in response.data
    with app.app_context():
        submission = FeedbackSubmission.query.one()
        assert submission.submission_type == "bug_report"
        assert submission.is_anonymous is False


def test_feedback_submission_throttle_blocks_repeat_posts(client, app):
    app.config["FEEDBACK_SUBMISSION_LIMIT"] = 1
    app.config["FEEDBACK_SUBMISSION_WINDOW_SECONDS"] = 300
    quick_login(client, "user")

    first_response = submit_feedback(
        client,
        submission_type="feedback",
        summary="One",
        body="First message",
    )
    second_response = submit_feedback(
        client,
        submission_type="feedback",
        summary="Two",
        body="Second message",
    )

    assert first_response.status_code == 200
    assert b"Feedback submitted." in first_response.data
    assert second_response.status_code == 200
    assert b"Please wait a few minutes before sending another message." in second_response.data
    with app.app_context():
        assert FeedbackSubmission.query.count() == 1


def test_admin_feedback_inbox_requires_pin_and_hides_anonymous_submitters(app):
    app.config["FEEDBACK_REVIEW_PIN"] = "2468"
    viewer_client = app.test_client()
    staff_client = app.test_client()
    admin_client = app.test_client()

    quick_login(viewer_client, "user")
    quick_login(staff_client, "staff")
    quick_login(admin_client, "admin")

    submit_feedback(
        viewer_client,
        submission_type="feedback",
        summary="Anonymous room note",
        body="The room context should stay private.",
        source_path="/venues/3",
        is_anonymous=True,
    )
    submit_feedback(
        staff_client,
        submission_type="bug_report",
        summary="Visible bug report",
        body="This one should keep the submitter name visible.",
        source_path="/dashboard",
    )

    locked_response = admin_client.get("/admin/feedback")

    assert locked_response.status_code == 200
    assert b"Review PIN required." in locked_response.data
    assert b"Anonymous room note" not in locked_response.data
    assert b"The room context should stay private." not in locked_response.data
    assert b"viewer@example.com" not in locked_response.data

    invalid_pin_response = unlock_feedback_inbox(admin_client, "9999")

    assert invalid_pin_response.status_code == 200
    assert b"Review PIN is incorrect." in invalid_pin_response.data
    assert b"Visible bug report" not in invalid_pin_response.data

    unlocked_response = unlock_feedback_inbox(admin_client, "2468")

    assert unlocked_response.status_code == 200
    assert b"Feedback inbox unlocked for this session." in unlocked_response.data
    assert b"Anonymous room note" in unlocked_response.data
    assert b"Visible bug report" in unlocked_response.data
    assert b"Anonymous" in unlocked_response.data
    assert b"viewer@example.com" not in unlocked_response.data
    assert b"staff@example.com" in unlocked_response.data


def test_feedback_inbox_paginates_and_admin_overview_counts_submissions(app):
    app.config["FEEDBACK_REVIEW_PIN"] = "2468"
    app.config["FEEDBACK_SUBMISSION_LIMIT"] = 20
    admin_client = app.test_client()
    viewer_client = app.test_client()

    quick_login(admin_client, "admin")
    quick_login(viewer_client, "user")

    for index in range(13):
        submit_feedback(
            viewer_client,
            submission_type="feedback" if index % 2 == 0 else "bug_report",
            summary=f"Submission {index}",
            body=f"Body {index}",
            source_path="/dashboard",
        )

    with app.app_context():
        overview = build_admin_overview_view_model()

    assert overview["module_summary"]["feedback"]["primary_value"] == 13
    assert overview["module_summary"]["feedback"]["secondary_value"] == 6

    unlock_feedback_inbox(admin_client, "2468", follow_redirects=False)
    paged_response = admin_client.get("/admin/feedback?page=2")

    assert paged_response.status_code == 200
    assert b"Page 2 of 2" in paged_response.data
    assert b"Submission 0" in paged_response.data
    overview_response = admin_client.get("/admin")
    assert b"Feedback Inbox" in overview_response.data
