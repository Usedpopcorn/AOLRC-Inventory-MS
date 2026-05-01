from __future__ import annotations

import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage

from flask import current_app, render_template

from app.services.account_security import PASSWORD_RESET_PURPOSE, PASSWORD_SETUP_PURPOSE
from app.services.inventory_status import ensure_utc

MAIL_STATUS_DISABLED = "disabled"
MAIL_STATUS_FAILED = "failed"
MAIL_STATUS_SENT = "sent"
MAIL_STATUS_SUPPRESSED = "suppressed"


@dataclass(frozen=True)
class MailDeliveryResult:
    status: str
    message: str
    recipient: str | None = None

    @property
    def sent(self):
        return self.status == MAIL_STATUS_SENT

    @property
    def failed(self):
        return self.status == MAIL_STATUS_FAILED

    @property
    def suppressed(self):
        return self.status == MAIL_STATUS_SUPPRESSED

    @property
    def disabled(self):
        return self.status == MAIL_STATUS_DISABLED


def send_password_action_email(user, issued_link):
    if issued_link.purpose == PASSWORD_SETUP_PURPOSE:
        return send_account_setup_email(
            user=user,
            setup_url=issued_link.url,
            expires_at=issued_link.expires_at,
        )
    return send_password_reset_email(
        user=user,
        reset_url=issued_link.url,
        expires_at=issued_link.expires_at,
    )


def send_password_reset_email(*, user, reset_url, expires_at):
    return _send_password_link_email(
        user=user,
        purpose=PASSWORD_RESET_PURPOSE,
        action_url=reset_url,
        expires_at=expires_at,
    )


def send_account_setup_email(*, user, setup_url, expires_at):
    return _send_password_link_email(
        user=user,
        purpose=PASSWORD_SETUP_PURPOSE,
        action_url=setup_url,
        expires_at=expires_at,
    )


def send_login_verification_email(*, user, verification_code, expires_at):
    normalized_expiration = ensure_utc(expires_at)
    context = {
        "app_name": current_app.config["APP_NAME"],
        "user": user,
        "verification_code": verification_code,
        "expires_at": normalized_expiration,
        "expires_at_text": normalized_expiration.strftime("%Y-%m-%d %I:%M %p %Z"),
        "expires_in_minutes": int(current_app.config["LOGIN_2FA_CODE_TTL_MINUTES"]),
    }
    subject = _render_subject("email/login_verification_subject.txt", **context)
    body_text = render_template("email/login_verification.txt", **context)
    body_html = render_template("email/login_verification.html", **context)
    return send_transactional_email(
        to_address=user.email,
        subject=subject,
        body_text=body_text,
        body_html=body_html,
    )


def send_transactional_email(*, to_address, subject, body_text, body_html=None):
    config = current_app.config
    recipient = (to_address or "").strip()

    if not config.get("MAIL_ENABLED"):
        return MailDeliveryResult(
            status=MAIL_STATUS_DISABLED,
            message="Mail delivery is disabled.",
            recipient=recipient,
        )

    if config.get("MAIL_SUPPRESS_SEND"):
        current_app.logger.info("Mail delivery suppressed for recipient=%s", recipient or "-")
        return MailDeliveryResult(
            status=MAIL_STATUS_SUPPRESSED,
            message="Mail delivery is suppressed.",
            recipient=recipient,
        )

    backend = (config.get("MAIL_BACKEND") or "smtp").strip().lower()
    if backend != "smtp":
        current_app.logger.error("Mail delivery failed: unsupported backend=%s", backend or "-")
        return MailDeliveryResult(
            status=MAIL_STATUS_FAILED,
            message="Mail backend is not supported.",
            recipient=recipient,
        )

    server = (config.get("MAIL_SERVER") or "").strip()
    sender = (config.get("MAIL_DEFAULT_SENDER") or "").strip()
    if not server or not sender:
        current_app.logger.error(
            "Mail delivery failed: MAIL_SERVER and MAIL_DEFAULT_SENDER are required."
        )
        return MailDeliveryResult(
            status=MAIL_STATUS_FAILED,
            message="Mail server or sender is not configured.",
            recipient=recipient,
        )

    if not recipient:
        current_app.logger.error("Mail delivery failed: recipient address is required.")
        return MailDeliveryResult(
            status=MAIL_STATUS_FAILED,
            message="Recipient address is required.",
            recipient=recipient,
        )

    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body_text)
    if body_html:
        message.add_alternative(body_html, subtype="html")

    try:
        _send_smtp_message(message)
    except Exception as exc:  # noqa: BLE001 - SMTP libraries raise several transport errors.
        current_app.logger.error(
            "Mail delivery failed for recipient=%s via backend=smtp: %s",
            recipient,
            exc.__class__.__name__,
        )
        return MailDeliveryResult(
            status=MAIL_STATUS_FAILED,
            message="Mail delivery failed.",
            recipient=recipient,
        )

    current_app.logger.info("Mail delivered to recipient=%s via backend=smtp", recipient)
    return MailDeliveryResult(
        status=MAIL_STATUS_SENT,
        message="Mail sent.",
        recipient=recipient,
    )


def _send_smtp_message(message):
    config = current_app.config
    server = config["MAIL_SERVER"]
    port = int(config["MAIL_PORT"])
    username = (config.get("MAIL_USERNAME") or "").strip()
    password = config.get("MAIL_PASSWORD") or ""
    timeout = int(config["MAIL_TIMEOUT_SECONDS"])

    if config.get("MAIL_USE_SSL"):
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(server, port, timeout=timeout, context=context) as smtp:
            _authenticate_and_send(smtp, username, password, message)
        return

    with smtplib.SMTP(server, port, timeout=timeout) as smtp:
        if config.get("MAIL_USE_TLS"):
            smtp.starttls(context=ssl.create_default_context())
        _authenticate_and_send(smtp, username, password, message)


def _authenticate_and_send(smtp, username, password, message):
    if username:
        smtp.login(username, password)
    smtp.send_message(message)


def _render_subject(template_name, **context):
    return " ".join(render_template(template_name, **context).split())


def _send_password_link_email(*, user, purpose, action_url, expires_at):
    normalized_expiration = ensure_utc(expires_at)
    context = {
        "app_name": current_app.config["APP_NAME"],
        "action_label": _password_action_label(purpose),
        "action_url": action_url,
        "expires_at": normalized_expiration,
        "expires_at_text": normalized_expiration.strftime("%Y-%m-%d %I:%M %p %Z"),
        "purpose": purpose,
        "user": user,
    }
    subject = _render_subject("email/password_action_subject.txt", **context)
    body_text = render_template("email/password_action.txt", **context)
    body_html = render_template("email/password_action.html", **context)
    return send_transactional_email(
        to_address=user.email,
        subject=subject,
        body_text=body_text,
        body_html=body_html,
    )


def _password_action_label(purpose):
    return "set up" if purpose == PASSWORD_SETUP_PURPOSE else "reset"
