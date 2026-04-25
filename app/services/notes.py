NOTE_TITLE_MAX_LENGTH = 160
NOTE_BODY_MAX_LENGTH = 4000
VENUE_NOTES_PAGE_SIZE = 12
SUPPLY_NOTES_PAGE_SIZE = 8


def normalize_note_page(value):
    raw_value = str(value or "").strip()
    if not raw_value.isdigit():
        return 1
    return max(int(raw_value), 1)


def validate_note_fields(title, body):
    if not title:
        return "Note title is required."
    if len(title) > NOTE_TITLE_MAX_LENGTH:
        return f"Note title must be {NOTE_TITLE_MAX_LENGTH} characters or fewer."
    if not body:
        return "Note body is required."
    if len(body) > NOTE_BODY_MAX_LENGTH:
        return f"Note body must be {NOTE_BODY_MAX_LENGTH:,} characters or fewer."
    return None


def build_pagination(total_count, page, per_page):
    total_pages = max((total_count + per_page - 1) // per_page, 1) if total_count else 1
    current_page = min(max(int(page or 1), 1), total_pages) if total_count else 1
    offset = (current_page - 1) * per_page

    return {
        "current_page": current_page,
        "has_next": current_page < total_pages,
        "has_prev": current_page > 1,
        "limit": per_page,
        "next_page": current_page + 1 if current_page < total_pages else None,
        "offset": offset,
        "page_size": per_page,
        "prev_page": current_page - 1 if current_page > 1 else None,
        "showing_from": offset + 1 if total_count else 0,
        "showing_to": min(offset + per_page, total_count),
        "total_count": total_count,
        "total_pages": total_pages,
    }
