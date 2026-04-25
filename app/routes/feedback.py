from flask import Blueprint, current_app, flash, redirect, request, url_for
from flask_login import current_user

from app import db
from app.authz import roles_required
from app.models import FeedbackSubmission
from app.security import normalize_safe_redirect_path
from app.services.feedback import (
    build_feedback_submission_success_message,
    normalize_feedback_source_context,
    normalize_feedback_submission_type,
    truncate_user_agent,
    validate_feedback_submission,
)
from app.services.rate_limits import rate_limiter

feedback_bp = Blueprint("feedback", __name__)


def _feedback_submission_throttle_key(user_id):
    return f"user:{int(user_id)}"


def _peek_feedback_submission_throttle(user_id):
    return rate_limiter.peek(
        "feedback_submission",
        _feedback_submission_throttle_key(user_id),
        limit=current_app.config["FEEDBACK_SUBMISSION_LIMIT"],
        window_seconds=current_app.config["FEEDBACK_SUBMISSION_WINDOW_SECONDS"],
    )


def _record_feedback_submission_attempt(user_id):
    return rate_limiter.record(
        "feedback_submission",
        _feedback_submission_throttle_key(user_id),
        limit=current_app.config["FEEDBACK_SUBMISSION_LIMIT"],
        window_seconds=current_app.config["FEEDBACK_SUBMISSION_WINDOW_SECONDS"],
    )


@feedback_bp.post("/feedback-submissions")
@roles_required("viewer", "staff", "admin")
def submit():
    fallback_next = url_for("main.dashboard")
    next_path = normalize_safe_redirect_path(request.form.get("next"), fallback_next)

    submission_type = normalize_feedback_submission_type(request.form.get("submission_type"))
    summary = (request.form.get("summary") or "").strip()
    body = (request.form.get("body") or "").strip()
    validation_error = validate_feedback_submission(summary, body, submission_type)
    if validation_error:
        flash(validation_error, "error")
        return redirect(next_path)

    throttle_decision = _peek_feedback_submission_throttle(current_user.id)
    if throttle_decision.limited:
        flash("Please wait a few minutes before sending another message.", "error")
        return redirect(next_path)

    source_path, source_query = normalize_feedback_source_context(
        request.form.get("source_path"),
        request.form.get("source_query"),
        next_path,
    )
    is_anonymous = (
        submission_type == "feedback"
        and str(request.form.get("is_anonymous") or "").strip().lower()
        in {"1", "true", "on", "yes"}
    )

    _record_feedback_submission_attempt(current_user.id)
    db.session.add(
        FeedbackSubmission(
            submission_type=submission_type,
            summary=summary,
            body=body,
            is_anonymous=is_anonymous,
            source_path=source_path,
            source_query=source_query,
            user_agent=truncate_user_agent(request.headers.get("User-Agent")),
            submitter_user_id=current_user.id,
        )
    )
    db.session.commit()
    flash(build_feedback_submission_success_message(submission_type), "success")
    return redirect(next_path)
