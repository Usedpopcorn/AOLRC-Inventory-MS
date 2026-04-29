import csv
import mimetypes
import secrets
from io import StringIO
from pathlib import Path

from flask import current_app
from werkzeug.utils import secure_filename

from app.models import VenueFile

PDF_EXTENSIONS = {"pdf"}
IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp", "bmp"}
TEXT_EXTENSIONS = {"txt", "text", "md", "markdown", "log", "json", "xml", "yaml", "yml"}
CSV_EXTENSIONS = {"csv", "tsv"}
SPREADSHEET_EXTENSIONS = {"xls", "xlsx", "ods"}
DOCUMENT_EXTENSIONS = {"doc", "docx", "odt", "rtf"}
PRESENTATION_EXTENSIONS = {"ppt", "pptx", "odp"}
AUDIO_EXTENSIONS = {"mp3", "m4a", "ogg", "wav"}
VIDEO_EXTENSIONS = {"mp4", "mov", "webm"}
ARCHIVE_EXTENSIONS = {"zip"}

ALLOWED_VENUE_FILE_EXTENSIONS = tuple(
    sorted(
        PDF_EXTENSIONS
        | IMAGE_EXTENSIONS
        | TEXT_EXTENSIONS
        | CSV_EXTENSIONS
        | SPREADSHEET_EXTENSIONS
        | DOCUMENT_EXTENSIONS
        | PRESENTATION_EXTENSIONS
        | AUDIO_EXTENSIONS
        | VIDEO_EXTENSIONS
        | ARCHIVE_EXTENSIONS
    )
)
VENUE_FILE_ACCEPT = ",".join(f".{extension}" for extension in ALLOWED_VENUE_FILE_EXTENSIONS)
TEXT_PREVIEW_MAX_BYTES = 64 * 1024
CSV_PREVIEW_MAX_ROWS = 25


class VenueFileError(ValueError):
    pass


def normalize_extension(filename):
    if "." not in (filename or ""):
        return ""
    return filename.rsplit(".", 1)[1].strip().lower()


def classify_extension(extension):
    extension = (extension or "").lower()
    if extension in PDF_EXTENSIONS:
        return {"category": "PDF", "preview_type": "pdf", "icon_class": "bi-file-earmark-pdf"}
    if extension in IMAGE_EXTENSIONS:
        return {"category": "Image", "preview_type": "image", "icon_class": "bi-file-earmark-image"}
    if extension in CSV_EXTENSIONS:
        return {"category": "Spreadsheet", "preview_type": "csv", "icon_class": "bi-file-earmark-spreadsheet"}
    if extension in SPREADSHEET_EXTENSIONS:
        return {"category": "Spreadsheet", "preview_type": "download", "icon_class": "bi-file-earmark-spreadsheet"}
    if extension in TEXT_EXTENSIONS:
        return {"category": "Text", "preview_type": "text", "icon_class": "bi-file-earmark-text"}
    if extension in DOCUMENT_EXTENSIONS:
        return {"category": "Document", "preview_type": "download", "icon_class": "bi-file-earmark-word"}
    if extension in PRESENTATION_EXTENSIONS:
        return {"category": "Presentation", "preview_type": "download", "icon_class": "bi-file-earmark-slides"}
    if extension in AUDIO_EXTENSIONS:
        return {"category": "Audio", "preview_type": "audio", "icon_class": "bi-file-earmark-music"}
    if extension in VIDEO_EXTENSIONS:
        return {"category": "Video", "preview_type": "video", "icon_class": "bi-file-earmark-play"}
    if extension in ARCHIVE_EXTENSIONS:
        return {"category": "Archive", "preview_type": "download", "icon_class": "bi-file-earmark-zip"}
    return {"category": "Other", "preview_type": "download", "icon_class": "bi-file-earmark"}


def allowed_extensions_label():
    return ", ".join(f".{extension}" for extension in ALLOWED_VENUE_FILE_EXTENSIONS)


def format_file_size(size_bytes):
    size = int(size_bytes or 0)
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def venue_file_upload_root():
    configured = current_app.config.get("VENUE_FILE_UPLOAD_DIR")
    if configured:
        return Path(configured)
    return Path(current_app.instance_path) / "venue_files"


def venue_file_directory(venue_id):
    return venue_file_upload_root() / str(int(venue_id))


def _path_is_relative_to(path, root):
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def stored_file_path(venue_file):
    root = venue_file_upload_root().resolve()
    candidate = (root / str(int(venue_file.venue_id)) / venue_file.stored_filename).resolve()
    if not _path_is_relative_to(candidate, root):
        raise VenueFileError("Stored file path is outside the configured venue upload directory.")
    return candidate


def build_stored_filename(venue_id, extension):
    return f"venue_{int(venue_id)}_{secrets.token_hex(16)}.{extension}"


def save_uploaded_venue_file(file_storage, *, venue_id, uploader_user_id, description=None):
    if file_storage is None or not (file_storage.filename or "").strip():
        raise VenueFileError("Please choose a file to upload.")

    original_filename = secure_filename(file_storage.filename or "")
    if not original_filename:
        raise VenueFileError("Please choose a file with a valid filename.")

    extension = normalize_extension(original_filename)
    if extension not in ALLOWED_VENUE_FILE_EXTENSIONS:
        raise VenueFileError(
            f"Files of type .{extension or 'unknown'} are not supported. "
            f"Allowed types: {allowed_extensions_label()}."
        )

    max_bytes = int(current_app.config.get("VENUE_FILE_MAX_BYTES") or 0)
    if file_storage.content_length and max_bytes and file_storage.content_length > max_bytes:
        raise VenueFileError(f"File is too large. Maximum size is {format_file_size(max_bytes)}.")

    upload_dir = venue_file_directory(venue_id)
    upload_dir.mkdir(parents=True, exist_ok=True)
    stored_filename = build_stored_filename(venue_id, extension)
    destination = upload_dir / stored_filename
    file_storage.save(destination)
    size_bytes = destination.stat().st_size
    if max_bytes and size_bytes > max_bytes:
        destination.unlink(missing_ok=True)
        raise VenueFileError(f"File is too large. Maximum size is {format_file_size(max_bytes)}.")

    classification = classify_extension(extension)
    mime_type = file_storage.mimetype or mimetypes.guess_type(original_filename)[0] or "application/octet-stream"
    normalized_description = (description or "").strip()
    if len(normalized_description) > 255:
        normalized_description = normalized_description[:255]

    return VenueFile(
        venue_id=venue_id,
        uploaded_by_user_id=uploader_user_id,
        original_filename=original_filename,
        stored_filename=stored_filename,
        mime_type=mime_type,
        extension=extension,
        size_bytes=size_bytes,
        category=classification["category"],
        preview_type=classification["preview_type"],
        description=normalized_description or None,
    )


def delete_stored_venue_file(venue_file):
    try:
        path = stored_file_path(venue_file)
    except VenueFileError:
        return False
    if path.exists():
        path.unlink()
        return True
    return False


def build_venue_file_row(venue_file):
    classification = classify_extension(venue_file.extension)
    uploader = venue_file.uploaded_by
    uploader_name = ""
    if uploader:
        uploader_name = (uploader.display_name or "").strip() or uploader.email
    return {
        "file": venue_file,
        "id": venue_file.id,
        "original_filename": venue_file.original_filename,
        "description": venue_file.description,
        "category": venue_file.category or classification["category"],
        "preview_type": venue_file.preview_type or classification["preview_type"],
        "icon_class": classification["icon_class"],
        "size_text": format_file_size(venue_file.size_bytes),
        "size_bytes": int(venue_file.size_bytes or 0),
        "extension_label": f".{venue_file.extension}",
        "uploaded_by": uploader_name or "Unknown user",
        "uploaded_at_text": venue_file.created_at.strftime("%Y-%m-%d %I:%M %p") if venue_file.created_at else "Unknown time",
        "created_timestamp": int(venue_file.created_at.timestamp()) if venue_file.created_at else 0,
        "can_preview_inline": (venue_file.preview_type or classification["preview_type"]) in {"pdf", "image", "text", "csv", "audio", "video"},
    }


def build_venue_file_rows(files):
    return [build_venue_file_row(venue_file) for venue_file in files]


def read_text_preview(venue_file):
    path = stored_file_path(venue_file)
    raw = path.read_bytes()
    truncated = len(raw) > TEXT_PREVIEW_MAX_BYTES
    if truncated:
        raw = raw[:TEXT_PREVIEW_MAX_BYTES]
    return {
        "text": raw.decode("utf-8", errors="replace"),
        "truncated": truncated,
    }


def read_csv_preview(venue_file):
    text_preview = read_text_preview(venue_file)
    delimiter = "\t" if venue_file.extension == "tsv" else ","
    reader = csv.reader(StringIO(text_preview["text"]), delimiter=delimiter)
    rows = []
    for row in reader:
        rows.append(row)
        if len(rows) >= CSV_PREVIEW_MAX_ROWS:
            break
    return {
        "rows": rows,
        "truncated": text_preview["truncated"] or len(rows) >= CSV_PREVIEW_MAX_ROWS,
    }
