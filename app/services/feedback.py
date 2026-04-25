from __future__ import annotations

from urllib.parse import urlsplit

from sqlalchemy import func, or_
from sqlalchemy.orm import joinedload

from app.models import FEEDBACK_SUBMISSION_TYPES, FeedbackSubmission
from app.services.inventory_status import ensure_utc
from app.services.notes import NOTE_BODY_MAX_LENGTH, NOTE_TITLE_MAX_LENGTH, build_pagination

FEEDBACK_REVIEW_SESSION_KEY = "_feedback_review_admin_id"
FEEDBACK_INBOX_PAGE_SIZE = 12
FEEDBACK_SOURCE_PATH_MAX_LENGTH = 255
FEEDBACK_SOURCE_QUERY_MAX_LENGTH = 1000
FEEDBACK_USER_AGENT_MAX_LENGTH = 512

FEEDBACK_SUBMISSION_META = {
    "feedback": {
        "label": "Feedback",
        "tone": "primary",
        "icon_class": "bi-chat-dots",
    },
    "bug_report": {
        "label": "Bug Report",
        "tone": "warning",
        "icon_class": "bi-bug",
    },
}


def normalize_feedback_submission_type(value, *, allow_all=False):
    normalized = (value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if allow_all and normalized in {"", "all"}:
        return "all"
    if normalized not in FEEDBACK_SUBMISSION_TYPES:
        return None if not allow_all else "all"
    return normalized


def normalize_feedback_page(value):
    raw_value = str(value or "").strip()
    if not raw_value.isdigit():
        return 1
    return max(int(raw_value), 1)


def validate_feedback_submission(summary, body, submission_type):
    normalized_type = normalize_feedback_submission_type(submission_type)
    if normalized_type is None:
        return "Choose Feedback or Report a Bug before submitting."
    if not summary:
        return "A short summary is required."
    if len(summary) > NOTE_TITLE_MAX_LENGTH:
        return f"Summary must be {NOTE_TITLE_MAX_LENGTH} characters or fewer."
    if not body:
        return "Details are required."
    if len(body) > NOTE_BODY_MAX_LENGTH:
        return f"Details must be {NOTE_BODY_MAX_LENGTH:,} characters or fewer."
    return None


def normalize_feedback_source_context(source_path, source_query, fallback_next_path):
    fallback = urlsplit(fallback_next_path or "")
    raw_path = (source_path or "").strip()
    raw_query = (source_query or "").strip().lstrip("?")

    candidate_path = ""
    candidate_query = raw_query
    if raw_path:
        parsed = urlsplit(raw_path)
        candidate_path = (parsed.path or "").strip()
        if not candidate_query:
            candidate_query = (parsed.query or "").strip()

    if not candidate_path:
        candidate_path = (fallback.path or "").strip()
    if not candidate_query:
        candidate_query = (fallback.query or "").strip()

    if not candidate_path.startswith("/"):
        candidate_path = "/dashboard"

    normalized_path = candidate_path[:FEEDBACK_SOURCE_PATH_MAX_LENGTH] or "/dashboard"
    normalized_query = candidate_query[:FEEDBACK_SOURCE_QUERY_MAX_LENGTH] or None
    return normalized_path, normalized_query


def truncate_user_agent(value):
    user_agent = (value or "").strip()
    return user_agent[:FEEDBACK_USER_AGENT_MAX_LENGTH] or None


def build_feedback_submission_success_message(submission_type):
    if normalize_feedback_submission_type(submission_type) == "bug_report":
        return "Bug report submitted."
    return "Feedback submitted."


def build_feedback_summary_counts():
    total_submissions = FeedbackSubmission.query.count()
    feedback_count = FeedbackSubmission.query.filter(
        FeedbackSubmission.submission_type == "feedback"
    ).count()
    bug_report_count = FeedbackSubmission.query.filter(
        FeedbackSubmission.submission_type == "bug_report"
    ).count()
    anonymous_feedback_count = FeedbackSubmission.query.filter(
        FeedbackSubmission.submission_type == "feedback",
        FeedbackSubmission.is_anonymous,
    ).count()
    return {
        "total_submissions": total_submissions,
        "feedback_count": feedback_count,
        "bug_report_count": bug_report_count,
        "anonymous_feedback_count": anonymous_feedback_count,
    }


def build_feedback_inbox_view_model(*, search="", submission_type="all", page=1):
    normalized_type = normalize_feedback_submission_type(submission_type, allow_all=True)
    search_text = (search or "").strip()

    query = (
        FeedbackSubmission.query.options(joinedload(FeedbackSubmission.submitter))
        .order_by(FeedbackSubmission.created_at.desc(), FeedbackSubmission.id.desc())
    )
    if normalized_type != "all":
        query = query.filter(FeedbackSubmission.submission_type == normalized_type)

    if search_text:
        lowered_search = search_text.lower()
        query = query.filter(
            or_(
                func.lower(FeedbackSubmission.summary).contains(lowered_search),
                func.lower(FeedbackSubmission.body).contains(lowered_search),
                func.lower(FeedbackSubmission.source_path).contains(lowered_search),
                func.lower(func.coalesce(FeedbackSubmission.source_query, "")).contains(
                    lowered_search
                ),
            )
        )

    total_count = query.count()
    pagination = build_pagination(
        total_count,
        normalize_feedback_page(page),
        FEEDBACK_INBOX_PAGE_SIZE,
    )
    rows = query.offset(pagination["offset"]).limit(pagination["limit"]).all()

    return {
        "rows": [_serialize_feedback_submission_row(row) for row in rows],
        "pagination": pagination,
        "filters": {
            "search": search_text,
            "submission_type": normalized_type,
        },
        "summary": build_feedback_summary_counts(),
    }


def _serialize_feedback_submission_row(submission):
    type_meta = FEEDBACK_SUBMISSION_META[submission.submission_type]
    is_hidden_submitter = submission.submission_type == "feedback" and submission.is_anonymous
    displayed_submitter = "Anonymous" if is_hidden_submitter else _build_submitter_label(submission)
    source_label = submission.source_path or "/dashboard"
    if submission.source_query:
        source_label = f"{source_label}?{submission.source_query}"

    return {
        "id": submission.id,
        "submission_type": submission.submission_type,
        "type_meta": type_meta,
        "summary": submission.summary,
        "body": submission.body,
        "is_anonymous": bool(submission.is_anonymous),
        "displayed_submitter": displayed_submitter,
        "created_at_text": _format_feedback_timestamp(submission.created_at),
        "source_path": submission.source_path,
        "source_query": submission.source_query,
        "source_label": source_label,
        "user_agent": submission.user_agent or "Unavailable",
    }


def _build_submitter_label(submission):
    submitter = submission.submitter
    if submitter is None:
        return "Former user"
    display_name = (submitter.display_name or "").strip()
    if display_name:
        return f"{display_name} ({submitter.email})"
    return submitter.email


def _format_feedback_timestamp(value):
    normalized = ensure_utc(value)
    if normalized is None:
        return "No recorded time"
    return normalized.strftime("%Y-%m-%d %I:%M %p")
