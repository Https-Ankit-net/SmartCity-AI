"""Shared fixtures for the SmartCity AI API tests.

The app is imported once per session against a throwaway database:

* default: a temporary SQLite file;
* ``TEST_DATABASE_URL=postgresql+psycopg2://…``: an (empty) PostgreSQL database, as in CI.

Startup runs the Alembic migrations, so every test run also exercises them.
YOLO is replaced by a stub so tests are fast and need no model weights.
"""

from __future__ import annotations

import io
import os
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="smartcity-tests-"))

# Must be set before anything imports app.* (the engine and settings read them at import).
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{(_TMP / 'test.db').as_posix()}"
os.environ["JWT_SECRET"] = "test-secret-" + "x" * 40
os.environ["JWT_EXPIRE_MINUTES"] = "90"
os.environ["ENVIRONMENT"] = "test"
os.environ["UPLOAD_DIR"] = str(_TMP / "uploads")
os.environ["AUTO_MIGRATE"] = "true"
os.environ.pop("REDIS_URL", None)
os.environ["EMAIL_BACKEND"] = "memory"
os.environ["RATE_LIMIT_ENABLED"] = "false"  # tests/test_hardening.py switches it on where needed
os.environ["FRONTEND_URL"] = "https://city.example"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import delete, select, update  # noqa: E402

from app.ai import incident_classifier  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Complaint, ComplaintConfirmation, ComplaintUpdate, Department, User  # noqa: E402

PASSWORD = "Password123!"


class FakeDetector:
    """Stands in for YOLO: returns whatever the test configured."""

    def __init__(self) -> None:
        self.result: dict | None = None
        self.calls: list[str] = []

    def __call__(self, image_path: str) -> dict:
        self.calls.append(image_path)
        return self.result or {"label": "no_detection", "confidence": 0.0}


@pytest.fixture(scope="session")
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:  # runs lifespan: migrations + department seed
        yield test_client


@pytest.fixture(autouse=True)
def detector(monkeypatch: pytest.MonkeyPatch) -> FakeDetector:
    fake = FakeDetector()
    monkeypatch.setattr(incident_classifier, "detect_image", fake)
    return fake


@pytest.fixture(autouse=True)
def clean_db(client: TestClient) -> Iterator[None]:
    yield
    with SessionLocal() as db:
        for model in (ComplaintConfirmation, ComplaintUpdate, Complaint, User):
            db.execute(delete(model))
        db.commit()


@pytest.fixture(autouse=True)
def outbox():
    """Emails 'sent' during the test (memory backend)."""
    from app.services import mailer

    mailer.outbox.clear()
    yield mailer.outbox
    mailer.outbox.clear()


@pytest.fixture()
def db():
    with SessionLocal() as session:
        yield session


@pytest.fixture()
def departments() -> dict[str, int]:
    with SessionLocal() as db:
        return {d.department_name: d.department_id for d in db.scalars(select(Department))}


_counter = iter(range(1, 1_000_000))


@pytest.fixture()
def make_user(client: TestClient, departments: dict[str, int]) -> Callable[..., dict]:
    """Register + log in a user; returns {"id", "email", "token", "headers", ...}."""

    def _make(
        role: str = "citizen",
        department: str | None = None,
        name: str | None = None,
        phone: str | None = None,
        approved: bool = True,
    ) -> dict:
        """approved=False leaves a department account pending and returns it without a token."""
        n = next(_counter)
        body = {
            "full_name": name or f"{role.title()} {n}",
            "email": f"{role}{n}@example.test",
            "password": PASSWORD,
            "role": role,
        }
        if phone:
            body["phone"] = phone
        if department:
            body["department_id"] = departments[department]
        if role == "admin":
            # Admins can't self-register; create one the way operators do (python -m app.manage).
            from app.manage import create_user

            created = create_user(body["full_name"], body["email"], PASSWORD, role="admin")
            user = {"user_id": created.user_id, "full_name": created.full_name, "email": created.email, "role": "admin"}
        else:
            res = client.post("/api/users", json=body)
            assert res.status_code == 201, res.text
            user = res.json()
            if role == "department" and approved:
                # Self-registered staff start pending; approve them as an admin would.
                with SessionLocal() as db:
                    db.get(User, user["user_id"]).account_status = "active"
                    db.commit()
                user["account_status"] = "active"
        if role == "department" and not approved:
            return {**user, "id": user["user_id"]}
        token = client.post("/api/auth/login", json={"email": body["email"], "password": PASSWORD}).json()["access_token"]
        return {**user, "id": user["user_id"], "token": token, "headers": {"Authorization": f"Bearer {token}"}}

    return _make


@pytest.fixture()
def file_complaint(client: TestClient) -> Callable[..., object]:
    """POST /api/complaints as `user`; returns the response."""

    def _file(user: dict, title: str = "Issue", description: str = "Something is wrong", lat: float | None = 20.2961, lng: float | None = 85.8245, **extra):
        body = {"title": title, "description": description, "latitude": lat, "longitude": lng, **extra}
        return client.post("/api/complaints", json=body, headers=user["headers"])

    return _file


@pytest.fixture()
def backdate(db) -> Callable[[int, float], None]:
    """Move a complaint's created_at `hours` into the past."""
    from datetime import datetime, timedelta, timezone

    def _backdate(complaint_id: int, hours: float) -> None:
        db.execute(
            update(Complaint)
            .where(Complaint.complaint_id == complaint_id)
            .values(created_at=datetime.now(timezone.utc) - timedelta(hours=hours))
        )
        db.commit()

    return _backdate


def png_bytes() -> bytes:
    """A tiny valid PNG for upload tests."""
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 30, 30)).save(buffer, format="PNG")
    return buffer.getvalue()
