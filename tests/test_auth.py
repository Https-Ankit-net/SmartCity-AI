"""Registration, login, JWT issuance and password hashing."""

from __future__ import annotations

import time

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.core import security
from app.core.config import DEFAULT_SECRET, Settings, settings
from app.core.security import create_access_token, decode_access_token, hash_password, verify_password
from app.models import User
from conftest import PASSWORD


def register(client, **overrides):
    body = {"full_name": "Asha Rao", "email": "asha@example.test", "password": PASSWORD, "role": "citizen", **overrides}
    return client.post("/api/users", json=body)


class TestRegistration:
    def test_register_returns_user_without_password(self, client):
        res = register(client, phone="9000000001")
        assert res.status_code == 201
        body = res.json()
        assert body["email"] == "asha@example.test"
        assert body["role"] == "citizen"
        assert "password" not in body and "password_hash" not in body

    def test_password_is_stored_as_bcrypt_hash(self, client, db):
        register(client)
        stored = db.scalar(select(User.password_hash).where(User.email == "asha@example.test"))
        assert stored != PASSWORD
        assert stored.startswith("$2")
        assert verify_password(PASSWORD, stored)

    def test_duplicate_email_rejected(self, client):
        assert register(client).status_code == 201
        res = register(client, full_name="Someone Else")
        assert res.status_code == 409
        assert "Email" in res.json()["detail"]

    def test_duplicate_phone_rejected(self, client):
        assert register(client, phone="9000000002").status_code == 201
        assert register(client, email="other@example.test", phone="9000000002").status_code == 409

    def test_cannot_self_register_as_admin(self, client):
        res = register(client, role="admin")
        assert res.status_code == 403

    def test_admin_can_create_another_admin(self, client, make_user):
        admin = make_user("admin")
        body = {"full_name": "Second Admin", "email": "admin2@example.test", "password": PASSWORD, "role": "admin"}
        assert client.post("/api/users", json=body, headers=admin["headers"]).status_code == 201

    def test_citizen_token_cannot_create_admin(self, client, make_user):
        citizen = make_user()
        body = {"full_name": "Sneaky", "email": "sneaky@example.test", "password": PASSWORD, "role": "admin"}
        assert client.post("/api/users", json=body, headers=citizen["headers"]).status_code == 403

    def test_unknown_role_rejected(self, client):
        assert register(client, role="superuser").status_code == 422

    def test_user_list_is_admin_only(self, client, make_user):
        assert client.get("/api/users").status_code in (401, 403)
        assert client.get("/api/users", headers=make_user()["headers"]).status_code == 403
        assert client.get("/api/users", headers=make_user("admin")["headers"]).status_code == 200

    def test_full_complaint_list_is_admin_only(self, client, make_user):
        assert client.get("/api/complaints").status_code in (401, 403)
        assert client.get("/api/complaints", headers=make_user()["headers"]).status_code == 403

    @pytest.mark.parametrize(
        "field,value",
        [("password", "short"), ("email", "x"), ("full_name", "")],
    )
    def test_invalid_payload_rejected(self, client, field, value):
        assert register(client, **{field: value}).status_code == 422


class TestLogin:
    def test_login_issues_signed_jwt_with_role(self, client, make_user):
        user = make_user("citizen")
        res = client.post("/api/auth/login", json={"email": user["email"], "password": PASSWORD})
        assert res.status_code == 200
        body = res.json()
        assert body["token_type"] == "bearer"
        assert body["role"] == "citizen"

        payload = decode_access_token(body["access_token"])
        assert payload["sub"] == str(user["id"])
        assert payload["role"] == "citizen"

    def test_token_lifetime_comes_from_jwt_expire_minutes(self, client, make_user):
        # conftest sets JWT_EXPIRE_MINUTES=90; this used to be silently ignored.
        user = make_user()
        payload = decode_access_token(user["token"])
        assert abs((payload["exp"] - payload["iat"]) - 90 * 60) <= 1

    def test_token_signed_with_configured_secret(self):
        assert settings.secret_key.startswith("test-secret-")
        assert settings.secret_key != DEFAULT_SECRET

    @pytest.mark.parametrize("email,password", [("citizen-nobody@example.test", PASSWORD), (None, "wrong-password")])
    def test_bad_credentials_rejected(self, client, make_user, email, password):
        user = make_user()
        res = client.post("/api/auth/login", json={"email": email or user["email"], "password": password})
        assert res.status_code == 401

    def test_legacy_scrypt_hash_upgraded_to_bcrypt_on_login(self, client, make_user, db):
        import hashlib

        user = make_user()
        salt = b"0123456789abcdef"
        digest = hashlib.scrypt(PASSWORD.encode(), salt=salt, n=2**14, r=8, p=1).hex()
        db_user = db.get(User, user["id"])
        db_user.password_hash = f"scrypt${salt.hex()}${digest}"
        db.commit()

        assert client.post("/api/auth/login", json={"email": user["email"], "password": PASSWORD}).status_code == 200
        db.expire_all()
        assert db.get(User, user["id"]).password_hash.startswith("$2")


class TestTokens:
    def test_me_returns_current_user(self, client, make_user):
        user = make_user("citizen", name="Ravi Kumar")
        res = client.get("/api/auth/me", headers=user["headers"])
        assert res.status_code == 200
        assert res.json()["full_name"] == "Ravi Kumar"

    def test_me_requires_token(self, client):
        assert client.get("/api/auth/me").status_code in (401, 403)

    def test_tampered_token_rejected(self, client, make_user):
        user = make_user()
        header, payload, signature = user["token"].split(".")
        forged = f"{header}.{payload}.{signature[:-2]}AA"
        assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {forged}"}).status_code == 401

    def test_role_escalation_by_editing_payload_rejected(self, client, make_user):
        import base64
        import json

        user = make_user("citizen")
        header, payload, signature = user["token"].split(".")
        claims = json.loads(base64.urlsafe_b64decode(payload + "=="))
        claims["role"] = "admin"
        forged_payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
        res = client.get("/api/admin/dashboard", headers={"Authorization": f"Bearer {header}.{forged_payload}.{signature}"})
        assert res.status_code == 401

    def test_expired_token_rejected(self, client, make_user, monkeypatch):
        user = make_user()
        monkeypatch.setattr(security.settings, "access_token_expire_minutes", -1)
        expired = create_access_token(str(user["id"]), "citizen")
        assert decode_access_token(expired) is None
        assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {expired}"}).status_code == 401


class TestHashing:
    def test_hash_is_salted(self):
        assert hash_password("same-password") != hash_password("same-password")

    def test_verify(self):
        hashed = hash_password("correct horse")
        assert verify_password("correct horse", hashed)
        assert not verify_password("battery staple", hashed)

    def test_garbage_hash_does_not_crash(self):
        assert not verify_password("anything", "not-a-hash")


class TestProductionGuard:
    def test_default_secret_refused_in_production(self):
        with pytest.raises(ValidationError):
            Settings(ENVIRONMENT="production", JWT_SECRET=DEFAULT_SECRET)

    def test_short_secret_refused_in_production(self):
        with pytest.raises(ValidationError):
            Settings(ENVIRONMENT="production", JWT_SECRET="too-short")

    def test_strong_secret_accepted(self):
        assert Settings(ENVIRONMENT="production", JWT_SECRET="s" * 48).secret_key == "s" * 48


def test_iat_is_current(make_user):
    payload = decode_access_token(make_user()["token"])
    assert abs(payload["iat"] - time.time()) < 60
