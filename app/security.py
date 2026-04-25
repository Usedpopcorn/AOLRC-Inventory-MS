from __future__ import annotations

import secrets
from urllib.parse import urljoin, urlparse

from flask import current_app, g, request

LOCAL_TRUSTED_HOSTS = {"localhost", "127.0.0.1", "::1"}


def get_csp_nonce():
    nonce = getattr(g, "_csp_nonce", None)
    if nonce is None:
        nonce = secrets.token_urlsafe(24)
        g._csp_nonce = nonce
    return nonce


def _normalize_host_entry(raw_value):
    raw = (raw_value or "").strip()
    if not raw:
        return None
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    hostname = (parsed.hostname or "").strip().lower()
    if not hostname:
        return None
    return hostname, parsed.port


def get_trusted_host_entries():
    trusted_entries = set()
    for host in LOCAL_TRUSTED_HOSTS:
        trusted_entries.add((host, None))

    configured_hosts = current_app.config.get("TRUSTED_HOSTS") or ()
    for host in configured_hosts:
        normalized = _normalize_host_entry(host)
        if normalized is not None:
            trusted_entries.add(normalized)

    app_base_url = (current_app.config.get("APP_BASE_URL") or "").strip()
    if app_base_url:
        normalized = _normalize_host_entry(app_base_url)
        if normalized is not None:
            trusted_entries.add(normalized)

    return trusted_entries


def is_trusted_url(target):
    parsed = urlparse(target)
    hostname = (parsed.hostname or "").strip().lower()
    if not hostname or parsed.scheme not in {"http", "https"}:
        return False
    for trusted_host, trusted_port in get_trusted_host_entries():
        if hostname != trusted_host:
            continue
        if trusted_port is None or trusted_port == parsed.port:
            return True
    return False


def is_safe_redirect_target(target):
    if not target:
        return False
    redirect_url = urljoin(request.host_url, target)
    return is_trusted_url(redirect_url)


def normalize_safe_redirect_path(target, fallback_path):
    if not target:
        return fallback_path

    redirect_url = urlparse(urljoin(request.host_url, target))
    if not is_trusted_url(redirect_url.geturl()):
        return fallback_path

    current_url = urlparse(urljoin(request.host_url, request.full_path))
    if redirect_url.path == current_url.path and redirect_url.query == current_url.query:
        return fallback_path

    return (
        f"{redirect_url.path}?{redirect_url.query}"
        if redirect_url.query
        else redirect_url.path
    )


def build_external_url(path):
    relative_path = f"/{(path or '').lstrip('/')}"
    app_base_url = (current_app.config.get("APP_BASE_URL") or "").strip()
    if app_base_url:
        return urljoin(f"{app_base_url.rstrip('/')}/", relative_path.lstrip("/"))

    host_url = request.host_url or ""
    candidate = urljoin(host_url, relative_path.lstrip("/"))
    if is_trusted_url(candidate):
        return candidate
    return relative_path


def get_client_ip():
    return (request.remote_addr or "unknown").strip() or "unknown"
