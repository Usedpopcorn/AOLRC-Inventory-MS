from __future__ import annotations

import csv
import io
import re
from datetime import datetime

from flask import Response

from app.services.inventory_status import get_app_timezone

EXPORT_SCOPE_FILTERED = "filtered"
EXPORT_SCOPE_FULL = "full"
VALID_EXPORT_SCOPES = {EXPORT_SCOPE_FILTERED, EXPORT_SCOPE_FULL}
CSV_FORMULA_PREFIX_CHARACTERS = ("=", "+", "-", "@")


def normalize_export_scope(raw_value, *, default=EXPORT_SCOPE_FILTERED):
    value = (raw_value or "").strip().lower()
    if value not in VALID_EXPORT_SCOPES:
        return default
    return value


def sanitize_csv_cell(value):
    if not isinstance(value, str):
        return value
    if value.startswith(CSV_FORMULA_PREFIX_CHARACTERS):
        return f"'{value}"
    return value


def sanitize_csv_rows(rows):
    sanitized_rows = []
    for row in rows:
        sanitized_rows.append(
            {
                key: sanitize_csv_cell(value)
                for key, value in row.items()
            }
        )
    return sanitized_rows


def build_csv_response(fieldnames, rows, filename):
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=list(fieldnames))
    writer.writeheader()
    for row in sanitize_csv_rows(rows):
        writer.writerow(row)

    response = Response(
        output.getvalue().encode("utf-8-sig"),
        mimetype="text/csv; charset=utf-8",
    )
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def slugify_export_token(value, *, fallback="export"):
    raw_value = (value or "").strip().lower()
    if not raw_value:
        return fallback
    slug = re.sub(r"[^a-z0-9]+", "-", raw_value)
    slug = slug.strip("-")
    return slug or fallback


def build_dated_csv_filename(prefix, *tokens):
    date_stamp = datetime.now(get_app_timezone()).strftime("%Y-%m-%d")
    filename_tokens = [slugify_export_token(prefix, fallback="export")]
    for token in tokens:
        normalized = slugify_export_token(token, fallback="")
        if normalized:
            filename_tokens.append(normalized)
    filename_tokens.append(date_stamp)
    return "_".join(filename_tokens) + ".csv"
