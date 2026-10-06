"""Account emails for the department staff approval flow, and the mailer itself."""

from __future__ import annotations

import smtplib

import pytest

from app.core.config import settings
from app.services import mailer
from conftest import PASSWORD


def by_recipient(outbox) -> dict[str, list]:
    grouped: dict[str, list] = {}
    for message in outbox:
        grouped.setdefault(message["To"], []).append(message)
    return grouped


def text_of(message) -> str:
    return message.get_body(preferencelist=("plain",)).get_content()


def html_of(message) -> str:
    return message.get_body(preferencelist=("html",)).get_content()


def register_staff(client, departments, name="Sunita Patra", email="sunita@example.test", dept="Sanitation Department"):
    res = client.post("/api/users", json={
        "full_name": name, "email": email, "password": PASSWORD, "role": "department", "department_id": departments[dept],
    })
    assert res.status_code == 201
    return res.json()


class TestApprovalEmails:
    def test_registration_emails_applicant_and_every_active_admin(self, client, make_user, departments, outbox):
        admin_a = make_user("admin")
        admin_b = make_user("admin")
        make_user()  # citizens get nothing
        register_staff(client, departments)

        mail = by_recipient(outbox)
        assert set(mail) == {"sunita@example.test", admin_a["email"], admin_b["email"]}

        receipt = mail["sunita@example.test"][0]
        assert "received" in receipt["Subject"]
        assert "Sanitation Department" in text_of(receipt)

        alert = mail[admin_a["email"]][0]
        assert alert["Subject"].endswith("new staff access request")
        assert "Sunita Patra" in text_of(alert)
        assert "https://city.example/admin_dashboard.html" in html_of(alert)

    def test_citizen_registration_sends_nothing(self, make_user, outbox):
        make_user()
        assert outbox == []

    def test_approval_email_with_corrected_department_and_note(self, client, make_user, departments, outbox):
        admin = make_user("admin")
        applicant = register_staff(client, departments)
        outbox.clear()
        client.post(
            f"/api/admin/staff/{applicant['user_id']}/approve",
            json={"department_id": departments["Water Department"], "note": "Welcome aboard"},
            headers=admin["headers"],
        )
        [message] = outbox
        assert message["To"] == "sunita@example.test"
        assert message["Subject"].endswith("your staff account is approved")
        body = text_of(message)
        assert "Water Department" in body and "Welcome aboard" in body
        assert "https://city.example/index.html" in body

    def test_rejection_email_carries_the_reason(self, client, make_user, departments, outbox):
        admin = make_user("admin")
        applicant = register_staff(client, departments)
        outbox.clear()
        client.post(f"/api/admin/staff/{applicant['user_id']}/reject", json={"reason": "Not on the roster"}, headers=admin["headers"])
        [message] = outbox
        assert message["Subject"].endswith("your staff access request")
        assert "was not approved" in text_of(message)
        assert "Not on the roster" in text_of(message)

    def test_revoke_and_restore_emails(self, client, make_user, outbox):
        admin = make_user("admin")
        officer = make_user("department", "Fire Services")
        outbox.clear()
        client.post(f"/api/admin/staff/{officer['id']}/reject", json={"reason": "Transferred"}, headers=admin["headers"])
        client.post(f"/api/admin/staff/{officer['id']}/approve", json={}, headers=admin["headers"])
        revoked, restored = outbox
        assert revoked["Subject"].endswith("your staff access was revoked")
        assert "revoked" in text_of(revoked) and "Transferred" in text_of(revoked)
        assert restored["Subject"].endswith("access restored")
        assert "restored" in text_of(restored)

    def test_department_change_on_active_account_sends_nothing(self, client, make_user, departments, outbox):
        admin = make_user("admin")
        officer = make_user("department", "Fire Services")
        outbox.clear()
        res = client.post(
            f"/api/admin/staff/{officer['id']}/approve", json={"department_id": departments["Traffic Police"]}, headers=admin["headers"]
        )
        assert res.status_code == 200
        assert outbox == []

    def test_user_text_is_escaped_in_html(self, client, make_user, departments, outbox):
        make_user("admin")
        register_staff(client, departments, name='<script>alert(1)</script> Evil', email="evil@example.test")
        for message in outbox:
            html = html_of(message)
            assert "<script>" not in html
            assert "&lt;script&gt;" in html

    def test_reason_with_markup_is_escaped(self, client, make_user, departments, outbox):
        admin = make_user("admin")
        applicant = register_staff(client, departments)
        outbox.clear()
        client.post(f"/api/admin/staff/{applicant['user_id']}/reject", json={"reason": '<a href="http://phish">click</a>'}, headers=admin["headers"])
        html = html_of(outbox[0])
        assert '<a href="http://phish">' not in html
        assert "&lt;a href=" in html


class TestMailer:
    def test_mail_failure_never_breaks_the_request(self, client, make_user, departments, monkeypatch, caplog):
        make_user("admin")
        monkeypatch.setattr(settings, "email_backend", "smtp")
        monkeypatch.setattr(settings, "smtp_host", "127.0.0.1")
        monkeypatch.setattr(settings, "smtp_port", 1)  # nothing listens here
        monkeypatch.setattr(mailer, "RETRY_DELAYS_S", (0, 0))
        res = client.post("/api/users", json={
            "full_name": "Offline Mail", "email": "offline@example.test", "password": PASSWORD,
            "role": "department", "department_id": departments["Water Department"],
        })
        assert res.status_code == 201
        assert any("failed after 3 attempts" in r.getMessage() for r in caplog.records)

    def test_smtp_flow_uses_starttls_and_login(self, monkeypatch):
        calls: list[str] = []

        class FakeSMTP:
            def __init__(self, host, port, timeout):
                calls.append(f"connect {host}:{port}")

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                calls.append("quit")

            def ehlo(self):
                calls.append("ehlo")

            def starttls(self, context):
                calls.append("starttls")

            def login(self, user, password):
                calls.append(f"login {user}")

            def send_message(self, message):
                calls.append(f"send {message['To']}")

        monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
        for key, value in {"email_backend": "smtp", "smtp_host": "smtp.example", "smtp_port": 587,
                           "smtp_security": "starttls", "smtp_username": "apikey", "smtp_password": "secret"}.items():
            monkeypatch.setattr(settings, key, value)
        assert mailer.deliver(mailer.Email(to="a@example.test", subject="Hi", text="Hello"))
        assert calls == ["connect smtp.example:587", "ehlo", "starttls", "ehlo", "login apikey", "send a@example.test", "quit"]

    def test_transient_failure_is_retried(self, monkeypatch):
        attempts = []

        def flaky(message):
            attempts.append(1)
            if len(attempts) < 3:
                raise smtplib.SMTPServerDisconnected("try again")

        monkeypatch.setattr(mailer, "_send_smtp", flaky)
        monkeypatch.setattr(mailer, "RETRY_DELAYS_S", (0, 0))
        monkeypatch.setattr(settings, "email_backend", "smtp")
        monkeypatch.setattr(settings, "smtp_host", "smtp.example")
        assert mailer.deliver(mailer.Email(to="a@example.test", subject="Hi", text="Hello"))
        assert len(attempts) == 3

    @pytest.mark.parametrize("backend", ["console", "disabled"])
    def test_non_sending_backends(self, monkeypatch, backend, outbox):
        monkeypatch.setattr(settings, "email_backend", backend)
        assert mailer.deliver(mailer.Email(to="a@example.test", subject="Hi", text="Hello"))
        assert outbox == []

    def test_header_injection_is_refused(self, outbox):
        assert not mailer.deliver(mailer.Email(to="a@example.test\r\nBcc: victim@example.test", subject="Hi", text="x"))
        assert outbox == []
