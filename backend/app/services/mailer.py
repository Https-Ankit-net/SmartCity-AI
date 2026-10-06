"""Outgoing email.

``EMAIL_BACKEND`` picks how mail leaves the app:

* ``console`` (default): log the message; nothing is sent. Good for local development.
* ``smtp``: send through ``SMTP_HOST`` (Gmail, SendGrid, SES, Mailgun, Postfix…).
* ``memory``: keep messages in ``outbox`` (tests).
* ``disabled``: drop messages silently.

Mail is queued with FastAPI ``BackgroundTasks`` so it is sent after the response, and a
mail-server problem never fails or slows down the request that triggered it. Sending is
retried a few times; a message that still fails is logged at ERROR level.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
import time
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from fastapi import BackgroundTasks

from app.core.config import settings

logger = logging.getLogger(__name__)

RETRY_DELAYS_S = (2, 5)


@dataclass(frozen=True)
class Email:
    to: str
    subject: str
    text: str
    html: str | None = None


# Messages "sent" by the memory backend (tests read and clear this).
outbox: list[EmailMessage] = []


def build_message(email: Email) -> EmailMessage:
    message = EmailMessage()
    message["From"] = formataddr((settings.email_from_name, settings.email_from))
    message["To"] = email.to
    message["Subject"] = email.subject
    message["Message-ID"] = make_msgid(domain=settings.email_from.rsplit("@", 1)[-1] or None)
    message.set_content(email.text)
    if email.html:
        message.add_alternative(email.html, subtype="html")
    return message


def _send_smtp(message: EmailMessage) -> None:
    security = settings.smtp_security.lower()
    context = ssl.create_default_context()
    if security == "ssl":
        client: smtplib.SMTP = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=20, context=context)
    else:
        client = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20)
    with client:
        client.ehlo()
        if security == "starttls":
            client.starttls(context=context)
            client.ehlo()
        if settings.smtp_username:
            client.login(settings.smtp_username, settings.smtp_password or "")
        client.send_message(message)


def deliver(email: Email) -> bool:
    """Send one email now, with retries. Returns True when delivered (or intentionally not sent)."""
    backend = settings.email_backend.lower()
    if backend == "disabled":
        return True
    try:
        message = build_message(email)
    except ValueError as exc:  # e.g. a malformed address
        logger.error("Email to %r not sent: %s", email.to, exc)
        return False
    if backend == "memory":
        outbox.append(message)
        return True
    if backend == "console":
        logger.info("Email (console backend, not sent)\nTo: %s\nSubject: %s\n\n%s", email.to, email.subject, email.text)
        return True
    if backend != "smtp":
        logger.error("Unknown EMAIL_BACKEND %r; email to %s not sent", backend, email.to)
        return False
    if not settings.smtp_host:
        logger.error("EMAIL_BACKEND=smtp but SMTP_HOST is not set; email to %s not sent", email.to)
        return False

    for attempt, delay in enumerate((*RETRY_DELAYS_S, None), start=1):
        try:
            _send_smtp(message)
            logger.info("Email sent to %s: %s", email.to, email.subject)
            return True
        except (smtplib.SMTPException, OSError) as exc:
            if delay is None:
                logger.error("Email to %s failed after %d attempts: %s", email.to, attempt, exc)
                return False
            logger.warning("Email to %s failed (attempt %d): %s; retrying in %ss", email.to, attempt, exc, delay)
            time.sleep(delay)
    return False


def queue(background_tasks: BackgroundTasks, *emails: Email) -> None:
    """Send after the response has been returned."""
    for email in emails:
        if email.to:
            background_tasks.add_task(deliver, email)
