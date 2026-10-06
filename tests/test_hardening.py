"""Regression tests for the security/robustness review findings and the slide-5 routing example."""

from __future__ import annotations

import asyncio
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from sqlalchemy import func, select, update
from starlette.websockets import WebSocketDisconnect

from app.core.security import verify_password
from app.db.session import SessionLocal
from app.models import Complaint, ComplaintConfirmation
from conftest import PASSWORD, png_bytes

UPLOADS = Path(os.environ["UPLOAD_DIR"])
SPOT = {"lat": 20.3333, "lng": 85.8111}
PRIVATE_FIELDS = ("title", "description", "user_id", "image_filename", "severity_factors", "detection_label")


def upload_count() -> int:
    return len(list(UPLOADS.glob("*"))) if UPLOADS.exists() else 0


class TestLiveFeeds:
    def test_feeds_require_login(self, client):
        for path in ("/ws/complaints", "/ws/notifications"):
            with pytest.raises(WebSocketDisconnect):
                with client.websocket_connect(path) as ws:
                    ws.receive_json()

    def test_each_viewer_gets_only_what_they_may_see(self, client, make_user, file_complaint):
        reporter = make_user()
        other_citizen = make_user()
        sanitation = make_user("department", "Sanitation Department")
        fire = make_user("department", "Fire Services")
        admin = make_user("admin")
        sockets = {}
        with client.websocket_connect(f"/ws/complaints?token={other_citizen['token']}") as cit_ws, \
             client.websocket_connect(f"/ws/complaints?token={sanitation['token']}") as san_ws, \
             client.websocket_connect(f"/ws/complaints?token={fire['token']}") as fire_ws, \
             client.websocket_connect(f"/ws/complaints?token={admin['token']}") as admin_ws:
            file_complaint(reporter, "Garbage at 12 Elm St", "garbage pile, call me on 99999", **SPOT)
            for name, ws in (("citizen", cit_ws), ("sanitation", san_ws), ("fire", fire_ws), ("admin", admin_ws)):
                sockets[name] = ws.receive_json()["data"]

        for name in ("citizen", "fire"):
            data = sockets[name]
            assert all(field not in data for field in PRIVATE_FIELDS), (name, data)
            assert data["type"] == "garbage" and data["lat"] == pytest.approx(SPOT["lat"])
        for name in ("sanitation", "admin"):
            assert sockets[name]["description"] == "garbage pile, call me on 99999"

    def test_broadcast_alerts_carry_no_titles(self, client, make_user, file_complaint):
        listener = make_user()
        with client.websocket_connect(f"/ws/notifications?token={listener['token']}") as ws:
            file_complaint(make_user(), "Garbage at 12 Elm St", "garbage pile", **SPOT)
            message = ws.receive_json()
        assert "Elm" not in message["message"] and "title" not in message["data"]

    def test_map_data_requires_login(self, client, make_user, file_complaint):
        file_complaint(make_user(), "Garbage", "garbage pile", **SPOT)
        assert client.get("/complaints/map").status_code in (401, 403)
        items = client.get("/complaints/map", headers=make_user()["headers"]).json()
        assert len(items) == 1 and set(items[0]) == {"id", "lat", "lng", "type", "status"}

    def test_revoking_access_closes_open_sockets(self, client, make_user):
        admin = make_user("admin")
        officer = make_user("department", "Fire Services")
        with client.websocket_connect(f"/ws/notifications/{officer['id']}?token={officer['token']}") as ws:
            client.post(f"/api/admin/staff/{officer['id']}/reject", json={}, headers=admin["headers"])
            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"/ws/notifications/{officer['id']}?token={officer['token']}") as ws:
                ws.receive_json()

    def test_stalled_client_does_not_block_others(self, monkeypatch):
        from app.core import websocket as ws_module

        monkeypatch.setattr(ws_module, "SEND_TIMEOUT_S", 0.2)
        received = []

        class Stalled:
            async def send_json(self, message):
                await asyncio.sleep(3600)

            async def close(self, code=1000):
                pass

        class Healthy:
            async def send_json(self, message):
                received.append(message)

        async def scenario():
            manager = ws_module.ConnectionManager()
            manager.active_connections[Stalled()] = ws_module.Viewer(1, "admin", None)
            manager.active_connections[Healthy()] = ws_module.Viewer(2, "admin", None)
            started = time.monotonic()
            await manager.broadcast({"type": "new_complaint", "data": {"complaint_id": 1}})
            return time.monotonic() - started, len(manager.active_connections)

        elapsed, remaining = asyncio.run(scenario())
        assert elapsed < 1.5 and received and remaining == 1  # stalled client dropped


class TestUploads:
    def test_json_body_cannot_reference_files(self, client, make_user):
        res = client.post(
            "/api/complaints",
            json={"title": "x", "description": "garbage", "image_filename": "../../../../Windows/win.ini"},
            headers=make_user()["headers"],
        )
        assert res.status_code == 422

    def test_detect_image_requires_login_and_keeps_nothing(self, client, make_user, detector, monkeypatch):
        from app.api.routes import ai as ai_routes

        monkeypatch.setattr(ai_routes, "detect_image", detector)
        files = {"image": ("p.png", png_bytes(), "image/png")}
        assert client.post("/detect-image", files=files).status_code in (401, 403)
        before = upload_count()
        detector.result = {"label": "pothole", "confidence": 0.8}
        res = client.post("/detect-image", files={"image": ("p.png", png_bytes(), "image/png")}, headers=make_user()["headers"])
        assert res.status_code == 200 and res.json()["prediction"] == "pothole"
        assert upload_count() == before

    @pytest.mark.parametrize("field,value", [("latitude", "100"), ("title", "t" * 201)])
    def test_bad_form_fields_are_422_and_leave_no_file(self, client, make_user, field, value):
        before = upload_count()
        data = {"title": "Pothole", "description": "pothole", field: value}
        res = client.post("/api/complaints/with-image", data=data, files={"image": ("p.png", png_bytes(), "image/png")},
                          headers=make_user()["headers"])
        assert res.status_code == 422
        assert upload_count() == before

    def test_repeat_report_photo_is_not_kept(self, client, make_user, file_complaint):
        citizen = make_user()
        file_complaint(citizen, "Garbage", "garbage pile", **SPOT)
        before = upload_count()
        res = client.post(
            "/api/complaints/with-image",
            data={"title": "Garbage", "description": "garbage again", "latitude": SPOT["lat"], "longitude": SPOT["lng"]},
            files={"image": ("p.png", png_bytes(), "image/png")},
            headers=citizen["headers"],
        )
        assert res.json()["outcome"] == "already_reported"
        assert upload_count() == before


class TestConfirmerPrivacy:
    def test_confirmers_never_see_the_original_report(self, client, make_user, file_complaint):
        owner, confirmer = make_user(), make_user()
        original = file_complaint(owner, "Garbage at 12 Elm St", "garbage pile, call me on 99999", **SPOT).json()
        res = file_complaint(confirmer, "Trash by the park", "garbage everywhere near park", **SPOT).json()
        assert res["outcome"] == "confirmed" and res["complaint_id"] == original["complaint_id"]
        assert res["relation"] == "confirmer"
        assert res["title"] == "Trash by the park" and res["description"] == "garbage everywhere near park"
        assert res["user_id"] == confirmer["id"] and "99999" not in str(res)

        [mine] = client.get("/api/complaints/my", headers=confirmer["headers"]).json()
        assert mine["relation"] == "confirmer" and "Elm" not in str(mine) and "99999" not in str(mine)

        admin = make_user("admin")
        with client.websocket_connect(f"/ws/notifications/{confirmer['id']}?token={confirmer['token']}") as ws:
            client.patch(f"/api/admin/complaints/{original['complaint_id']}/status", json={"status": "In Progress"},
                         headers=admin["headers"])
            pushed = ws.receive_json()
        assert pushed["data"]["relation"] == "confirmer" and "99999" not in str(pushed) and "Elm" not in str(pushed)


class TestDepartmentScope:
    def test_history_is_scoped_to_the_department(self, client, make_user, file_complaint):
        complaint = file_complaint(make_user(), "Garbage", "garbage pile", **SPOT).json()  # Sanitation
        path = f"/api/complaints/{complaint['complaint_id']}/updates"
        assert client.get(path, headers=make_user("department", "Fire Services")["headers"]).status_code == 403
        assert client.get(path, headers=make_user("department", "Sanitation Department")["headers"]).status_code == 200


class TestConcurrency:
    def test_simultaneous_reports_of_one_incident_make_one_complaint(self, make_user):
        from app.schemas.complaint import ComplaintCreate
        from app.services.complaint_service import submit_complaint

        users = [make_user() for _ in range(6)]

        def report(user):
            with SessionLocal() as db:
                data = ComplaintCreate(user_id=user["id"], title="Garbage", description="garbage pile",
                                       latitude=SPOT["lat"], longitude=SPOT["lng"])
                return submit_complaint(db, data).outcome

        with ThreadPoolExecutor(max_workers=6) as pool:
            outcomes = list(pool.map(report, users))

        with SessionLocal() as db:
            complaints = db.scalars(select(Complaint)).all()
            confirmations = db.scalar(select(func.count()).select_from(ComplaintConfirmation))
        assert outcomes.count("created") == 1 and outcomes.count("confirmed") == 5
        assert len(complaints) == 1
        assert complaints[0].confirmation_count == confirmations == 5


class TestInputHandling:
    def test_unknown_status_filter_is_422(self, client, make_user):
        admin = make_user("admin")
        assert client.get("/api/admin/complaints?status=bogus", headers=admin["headers"]).status_code == 422
        assert client.get("/api/admin/complaints?status=in_progress", headers=admin["headers"]).status_code == 200

    def test_date_filter_honours_timezone_offsets(self, client, make_user, file_complaint, db):
        from datetime import datetime, timedelta, timezone

        complaint = file_complaint(make_user(), "Garbage", "garbage pile", **SPOT).json()
        created = datetime.now(timezone.utc) - timedelta(hours=3)
        db.execute(update(Complaint).where(Complaint.complaint_id == complaint["complaint_id"]).values(created_at=created))
        db.commit()
        ist = timezone(timedelta(hours=5, minutes=30))
        one_hour_before = (created - timedelta(hours=1)).astimezone(ist).isoformat()
        res = client.get("/api/admin/complaints", params={"date_from": one_hour_before}, headers=make_user("admin")["headers"])
        assert [c["complaint_id"] for c in res.json()["items"]] == [complaint["complaint_id"]]


class TestCredentials:
    def test_unknown_hash_formats_never_match(self):
        assert verify_password("a$b$c", "a$b$c") is False
        assert verify_password("pbkdf2_sha256$600000$salt$hash", "pbkdf2_sha256$600000$salt$hash") is False

    def test_passwords_over_72_bytes_are_refused(self, client):
        body = {"full_name": "Long", "email": "long@example.test", "password": "x" * 73, "role": "citizen"}
        assert client.post("/api/users", json=body).status_code == 422

    def test_emails_are_case_insensitive(self, client):
        body = {"full_name": "Mixed", "email": " Mixed.Case@Example.TEST ", "password": PASSWORD, "role": "citizen"}
        res = client.post("/api/users", json=body)
        assert res.status_code == 201 and res.json()["email"] == "mixed.case@example.test"
        assert client.post("/api/users", json={**body, "email": "MIXED.CASE@example.test"}).status_code == 409
        assert client.post("/api/auth/login", json={"email": "mixed.CASE@EXAMPLE.test", "password": PASSWORD}).status_code == 200


class TestRateLimits:
    @pytest.fixture(autouse=True)
    def enabled(self, monkeypatch):
        from app.core.config import settings
        from app.core.rate_limit import limiter

        monkeypatch.setattr(settings, "rate_limit_enabled", True)
        limiter.reset()
        yield
        limiter.reset()

    def test_password_guessing_is_throttled(self, client, make_user):
        from app.core.rate_limit import limiter

        user = make_user()
        limiter.reset()  # the fixture's own login counted
        codes = [client.post("/api/auth/login", json={"email": user["email"], "password": "wrong-guess"}).status_code
                 for _ in range(11)]
        assert codes[:10] == [401] * 10 and codes[10] == 429
        res = client.post("/api/auth/login", json={"email": user["email"], "password": PASSWORD})
        assert res.status_code == 429 and int(res.headers["Retry-After"]) > 0

    def test_registration_is_throttled_per_address(self, client):
        codes = [client.post("/api/users", json={"full_name": f"U{i}", "email": f"u{i}@example.test",
                                                 "password": PASSWORD, "role": "citizen"}).status_code for i in range(21)]
        assert codes[:20] == [201] * 20 and codes[20] == 429


class TestSlideFiveExample:
    def test_streetlight_example_is_high_priority_electrical(self, make_user, file_complaint):
        body = file_complaint(
            make_user(),
            "Street lights not working",
            "Street lights in our area have stopped working. Please fix them urgently.",
            **SPOT,
        ).json()
        assert body["incident_type"] == "electrical"
        assert body["department_name"] == "Electrical Department"
        assert body["priority"] == "High"

    def test_urgency_raises_priority_one_level_only(self, make_user, file_complaint):
        # No location, so proximity can't change the baseline between the two.
        calm = file_complaint(make_user(), "Bench", "The park bench paint is peeling", lat=None, lng=None).json()
        urgent = file_complaint(make_user(), "Bench", "The park bench paint is peeling, please fix urgently", lat=None, lng=None).json()
        order = ["Low", "Medium", "High"]
        assert order.index(urgent["priority"]) == order.index(calm["priority"]) + 1


def test_backfill_scores_legacy_complaints(make_user, file_complaint, db):
    from app.manage import backfill_severity

    complaint = file_complaint(make_user(), "Fire", "fire and smoke near the market", **SPOT).json()
    db.execute(update(Complaint).values(severity_score=None, severity_factors=None, priority="Low"))
    db.commit()
    assert backfill_severity() == 1
    db.expire_all()
    stored = db.get(Complaint, complaint["complaint_id"])
    assert stored.severity_score >= 7 and stored.priority == "High" and stored.severity_factors
