from datetime import datetime, timedelta, timezone


STATUS_META = {
    "good": {"text": "Good", "icon_class": "bi-check-circle-fill"},
    "ok": {"text": "OK", "icon_class": "bi-check-circle-fill"},
    "low": {"text": "Low", "icon_class": "bi-exclamation-triangle-fill"},
    "out": {"text": "Out", "icon_class": "bi-x-circle-fill"},
    "not_checked": {"text": "Not Checked", "icon_class": "bi-dash-circle"},
}

STATUS_ORDER = {
    "out": 4,
    "low": 3,
    "not_checked": 2,
    "ok": 1,
    "good": 0,
}

CONSISTENCY_META = {
    "aligned": {
        "label": "Aligned",
        "tone": "healthy",
        "icon_class": "bi-check-circle-fill",
    },
    "needs_review": {
        "label": "Needs Review",
        "tone": "warning",
        "icon_class": "bi-exclamation-triangle-fill",
    },
    "status_stale": {
        "label": "Status Stale",
        "tone": "warning",
        "icon_class": "bi-clock-history",
    },
    "count_stale": {
        "label": "Count Stale",
        "tone": "warning",
        "icon_class": "bi-clock-history",
    },
    "no_status": {
        "label": "No Status",
        "tone": "missing",
        "icon_class": "bi-dash-circle",
    },
    "no_count": {
        "label": "No Count",
        "tone": "missing",
        "icon_class": "bi-dash-circle",
    },
    "not_comparable": {
        "label": "Status Only",
        "tone": "neutral",
        "icon_class": "bi-shield-check",
    },
}

STALE_SIGNAL_THRESHOLD = timedelta(days=2)


def ensure_utc(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def format_timestamp(value, missing_text="No updates yet"):
    normalized = ensure_utc(value)
    if normalized is None:
        return missing_text
    return normalized.strftime("%Y-%m-%d %I:%M %p")


def _resolve_stale_threshold(stale_threshold=None):
    if stale_threshold is None:
        return STALE_SIGNAL_THRESHOLD
    if isinstance(stale_threshold, timedelta):
        return stale_threshold
    return timedelta(days=max(int(stale_threshold), 1))


def build_signal_freshness(value, missing_text="No updates yet", stale_threshold=None):
    updated_at = ensure_utc(value)
    if updated_at is None:
        return {
            "text": missing_text,
            "absolute_text": missing_text,
            "is_missing": True,
            "is_stale": False,
            "updated_at": None,
        }

    now = datetime.now(timezone.utc)
    delta = max(now - updated_at, timedelta(0))
    resolved_threshold = _resolve_stale_threshold(stale_threshold)
    total_seconds = int(delta.total_seconds())
    if total_seconds < 60:
        relative = "Updated just now"
    elif total_seconds < 3600:
        relative = f"Updated {total_seconds // 60}m ago"
    elif total_seconds < 86400:
        relative = f"Updated {total_seconds // 3600}h ago"
    else:
        relative = f"Updated {total_seconds // 86400}d ago"

    return {
        "text": relative,
        "absolute_text": format_timestamp(updated_at),
        "is_missing": False,
        "is_stale": delta >= resolved_threshold,
        "updated_at": updated_at,
    }


def is_signal_stale(value, stale_threshold=None):
    freshness = build_signal_freshness(value, stale_threshold=stale_threshold)
    return freshness["is_stale"]


def normalize_status(value):
    normalized = (value or "").strip().lower()
    if normalized == "-":
        normalized = "not_checked"
    if normalized not in STATUS_META:
        return "not_checked"
    return normalized


def normalize_singleton_status(value):
    normalized = (value or "not_checked").strip().lower()
    aliases = {
        "present": "good",
        "damaged": "low",
        "missing": "out",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized == "ok":
        normalized = "good"
    if normalized not in {"good", "low", "out", "not_checked"}:
        return "not_checked"
    return normalized


def status_label(status_key, tracking_mode="quantity"):
    normalized = normalize_status(status_key)
    if tracking_mode == "singleton_asset":
        return {
            "good": "Present",
            "ok": "Present",
            "low": "Damaged",
            "out": "Missing",
            "not_checked": "Not Checked",
        }.get(normalized, "Not Checked")
    return STATUS_META[normalized]["text"]


def restock_status_meta_for_item(status_key, tracking_mode):
    normalized = normalize_singleton_status(status_key) if tracking_mode == "singleton_asset" else normalize_status(status_key)
    icon_key = "good" if tracking_mode == "singleton_asset" and normalized == "good" else normalized
    return {
        "key": normalized,
        "text": status_label(normalized, tracking_mode=tracking_mode),
        "icon_class": STATUS_META[icon_key]["icon_class"],
    }


def build_status_detail_counts():
    return {
        "low_quantity": 0,
        "low_singleton": 0,
        "out_quantity": 0,
        "out_singleton": 0,
    }


def build_overall_status_badge(total_tracked, counts, detail_counts=None):
    if total_tracked <= 0:
        return {"key": "not_checked", "text": "Not Checked", "icon_class": "bi-dash-circle"}

    detail_counts = detail_counts or build_status_detail_counts()
    checked_count = total_tracked - counts["not_checked"]

    if counts["out"] > 0:
        out_count = counts["out"]
        if detail_counts["out_singleton"] > 0 and detail_counts["out_quantity"] == 0:
            label = "item missing" if out_count == 1 else "items missing"
            return {"key": "out", "text": f"{out_count} {label}", "icon_class": "bi-x-circle-fill"}
        if detail_counts["out_singleton"] > 0 and detail_counts["out_quantity"] > 0:
            label = "item needs attention" if out_count == 1 else "items need attention"
            return {"key": "out", "text": f"{out_count} {label}", "icon_class": "bi-x-circle-fill"}
        label = "item out of stock" if out_count == 1 else "items out of stock"
        return {"key": "out", "text": f"{out_count} {label}", "icon_class": "bi-x-circle-fill"}

    if counts["low"] > 0:
        low_count = counts["low"]
        if detail_counts["low_singleton"] > 0 and detail_counts["low_quantity"] == 0:
            label = "item damaged" if low_count == 1 else "items damaged"
            return {"key": "low", "text": f"{low_count} {label}", "icon_class": "bi-exclamation-triangle-fill"}
        if detail_counts["low_singleton"] > 0 and detail_counts["low_quantity"] > 0:
            label = "item needs attention" if low_count == 1 else "items need attention"
            return {"key": "low", "text": f"{low_count} {label}", "icon_class": "bi-exclamation-triangle-fill"}
        label = "item low" if low_count == 1 else "items low"
        return {"key": "low", "text": f"{low_count} {label}", "icon_class": "bi-exclamation-triangle-fill"}

    if checked_count > 0 and counts["ok"] > 0 and (counts["ok"] * 2 >= checked_count):
        return {"key": "ok", "text": "OK", "icon_class": "bi-check-circle-fill"}
    if counts["good"] > 0:
        good_has_partial_attention = counts["ok"] > 0 or counts["not_checked"] > 0
        return {
            "key": "good",
            "text": "Good*" if good_has_partial_attention else "Good",
            "icon_class": "bi-check-circle-fill",
        }
    if checked_count > 0 and counts["ok"] > 0:
        return {"key": "ok", "text": "OK", "icon_class": "bi-check-circle-fill"}
    return {"key": "not_checked", "text": "Not Checked", "icon_class": "bi-dash-circle"}


def derive_singleton_count_from_status(status_key):
    normalized = normalize_singleton_status(status_key)
    if normalized in {"good", "low"}:
        return 1
    if normalized == "out":
        return 0
    return None


def infer_singleton_status_from_count(raw_count):
    if raw_count is None:
        return "not_checked"
    return "good" if raw_count > 0 else "out"


def suggest_status_from_count(raw_count, par_value, tracking_mode):
    if tracking_mode == "singleton_asset":
        return None
    if raw_count is None or par_value is None or par_value <= 0:
        return None
    if raw_count <= 0:
        return "out"

    ratio = raw_count / par_value
    if ratio <= 0.25:
        return "low"
    if ratio <= 0.75:
        return "ok"
    return "good"


def build_consistency_signal(
    *,
    tracking_mode,
    status_key,
    raw_count,
    par_value,
    status_updated_at,
    count_updated_at,
    stale_threshold=None,
):
    normalized_status = (
        normalize_singleton_status(status_key)
        if tracking_mode == "singleton_asset"
        else normalize_status(status_key)
    )
    suggested_status = suggest_status_from_count(raw_count, par_value, tracking_mode)
    status_freshness = build_signal_freshness(
        status_updated_at,
        missing_text="No status yet",
        stale_threshold=stale_threshold,
    )
    count_freshness = build_signal_freshness(
        count_updated_at,
        missing_text="No count yet",
        stale_threshold=stale_threshold,
    )

    if tracking_mode == "singleton_asset":
        state = "not_comparable"
        detail = "Status-first asset check."
    elif normalized_status == "not_checked":
        state = "no_status"
        detail = "Add a quick status."
    elif raw_count is None:
        state = "no_count"
        detail = "Count needed."
    elif suggested_status is None:
        state = "not_comparable"
        detail = "Set par to compare."
    elif status_freshness["is_stale"] and count_freshness["is_stale"]:
        state = "needs_review"
        detail = "Status and count are stale."
    elif status_freshness["is_stale"]:
        state = "status_stale"
        detail = "Quick status is stale."
    elif count_freshness["is_stale"]:
        state = "count_stale"
        detail = "Latest count is stale."
    elif normalized_status != suggested_status:
        state = "needs_review"
        detail = "Status and count do not match."
    else:
        state = "aligned"
        detail = "Status and count align."

    meta = CONSISTENCY_META[state]
    return {
        "state": state,
        "label": meta["label"],
        "tone": meta["tone"],
        "icon_class": meta["icon_class"],
        "detail": detail,
        "status_freshness": status_freshness,
        "count_freshness": count_freshness,
        "suggested_status_key": suggested_status,
        "suggested_status_label": (
            status_label(suggested_status, tracking_mode="quantity")
            if suggested_status
            else None
        ),
        "signals_align": state == "aligned",
    }


def status_sort_value(status_key):
    return STATUS_ORDER.get(normalize_status(status_key), STATUS_ORDER["not_checked"])
