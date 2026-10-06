"""Emails about department staff accounts: request received, approved, rejected, revoked, restored.

Every value that came from a user (names, departments, an admin's reason) is HTML-escaped
in the HTML part, and none of it goes into a header except the recipient address.
"""

from __future__ import annotations

from html import escape

from app.core.config import settings
from app.services.mailer import Email

BRAND = "SmartCity AI"
ACCENT = "#6366f1"


def _link(path: str) -> str | None:
    base = (settings.frontend_url or "").rstrip("/")
    return f"{base}/{path}" if base else None


def _paragraphs(fragments: list[str]) -> str:
    return "".join(f'<p style="margin:0 0 14px;line-height:1.6;color:#334155">{p}</p>' for p in fragments)


def _html(
    heading: str,
    paragraphs: list[str],
    button: tuple[str, str] | None = None,
    note: tuple[str, str] | None = None,
    closing: list[str] | None = None,
) -> str:
    """paragraphs/closing are already-escaped HTML fragments; note text is escaped here."""
    body = _paragraphs(paragraphs)
    if note:
        label, text = note
        body += (
            '<div style="margin:6px 0 18px;padding:12px 14px;border-left:3px solid #f59e0b;background:#fffbeb;'
            f'color:#78350f;border-radius:6px"><strong>{escape(label)}</strong><br>{escape(text)}</div>'
        )
    if button:
        label, href = button
        body += (
            f'<p style="margin:22px 0"><a href="{escape(href, quote=True)}" style="background:{ACCENT};color:#ffffff;'
            f'text-decoration:none;padding:11px 20px;border-radius:8px;font-weight:600;display:inline-block">{escape(label)}</a></p>'
        )
    body += _paragraphs(closing or [])
    return f"""<!doctype html>
<html><body style="margin:0;padding:24px;background:#f1f5f9;font-family:Segoe UI,Helvetica,Arial,sans-serif">
  <div style="max-width:560px;margin:0 auto;background:#ffffff;border-radius:12px;overflow:hidden;border:1px solid #e2e8f0">
    <div style="background:#0f172a;color:#ffffff;padding:16px 24px;font-weight:700;font-size:16px">{BRAND}</div>
    <div style="padding:24px">
      <h1 style="margin:0 0 16px;font-size:20px;color:#0f172a">{escape(heading)}</h1>
      {body}
      <p style="margin:24px 0 0;font-size:12px;color:#94a3b8">This is an automated message from {BRAND}. Please don't reply.</p>
    </div>
  </div>
</body></html>"""


def _text(lines: list[str]) -> str:
    return "\n\n".join(lines + [f"— {BRAND} (automated message, please don't reply)"])


def request_received(name: str, email: str, department: str) -> Email:
    return Email(
        to=email,
        subject=f"{BRAND}: your staff access request was received",
        text=_text([
            f"Hello {name},",
            f"We received your request for staff access to {department}.",
            "A city administrator will review it. We'll email you as soon as it is approved; "
            "until then you won't be able to sign in.",
        ]),
        html=_html(
            "Request received",
            [
                f"Hello {escape(name)},",
                f"We received your request for staff access to <strong>{escape(department)}</strong>.",
                "A city administrator will review it. We'll email you as soon as it is approved; "
                "until then you won't be able to sign in.",
            ],
        ),
    )


def new_request_for_admin(admin_email: str, applicant_name: str, applicant_email: str, department: str) -> Email:
    portal = _link("admin_dashboard.html")
    return Email(
        to=admin_email,
        subject=f"{BRAND}: new staff access request",
        text=_text([
            f"{applicant_name} <{applicant_email}> requested staff access to {department}.",
            f"Review it in the Staff Access panel{f': {portal}' if portal else ' of the staff portal'}.",
        ]),
        html=_html(
            "New staff access request",
            [
                f"<strong>{escape(applicant_name)}</strong> ({escape(applicant_email)}) requested staff access to "
                f"<strong>{escape(department)}</strong>.",
                "Approve or reject it in the <strong>Staff Access</strong> panel of the staff portal.",
            ],
            button=("Review request", portal) if portal else None,
        ),
    )


def approved(name: str, email: str, department: str, note: str | None, restored: bool) -> Email:
    login = _link("index.html")
    heading = "Your access has been restored" if restored else "Your staff account is approved"
    lead = (
        f"Your staff access to {department} has been restored."
        if restored
        else f"Your request for staff access to {department} has been approved."
    )
    return Email(
        to=email,
        subject=f"{BRAND}: {'access restored' if restored else 'your staff account is approved'}",
        text=_text(
            [f"Hello {name},", lead + " You can now sign in to the Department Portal."]
            + ([f"Note from the administrator: {note}"] if note else [])
            + ([f"Sign in: {login}"] if login else [])
        ),
        html=_html(
            heading,
            [
                f"Hello {escape(name)},",
                escape(lead) + " You can now sign in to the Department Portal.",
            ],
            button=("Sign in", login) if login else None,
            note=("Note from the administrator", note) if note else None,
        ),
    )


def rejected(name: str, email: str, department: str, reason: str | None, revoked: bool) -> Email:
    lead = (
        f"Your staff access to {department} has been revoked, and you can no longer sign in to the Department Portal."
        if revoked
        else f"Your request for staff access to {department} was not approved."
    )
    return Email(
        to=email,
        subject=f"{BRAND}: {'your staff access was revoked' if revoked else 'your staff access request'}",
        text=_text(
            [f"Hello {name},", lead]
            + ([f"Reason: {reason}"] if reason else [])
            + ["If you think this is a mistake, please contact the city administration."]
        ),
        html=_html(
            "Access revoked" if revoked else "Request not approved",
            [f"Hello {escape(name)},", escape(lead)],
            note=("Reason", reason) if reason else None,
            closing=["If you think this is a mistake, please contact the city administration."],
        ),
    )
