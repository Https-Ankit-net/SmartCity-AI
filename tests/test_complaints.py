"""Complaint creation, AI routing, severity, duplicate detection and citizen ownership queries."""

from __future__ import annotations

import pytest

from conftest import png_bytes

# Far apart in the city so routing tests never trip duplicate detection.
SPOTS = iter((20.20 + i * 0.01, 85.70 + i * 0.01) for i in range(10_000))


def at_new_spot() -> dict:
    lat, lng = next(SPOTS)
    return {"lat": lat, "lng": lng}


class TestCreation:
    def test_requires_login(self, client):
        res = client.post("/api/complaints", json={"title": "x", "description": "pothole"})
        assert res.status_code in (401, 403)

    def test_created_for_the_token_owner(self, client, make_user, file_complaint):
        user = make_user()
        res = file_complaint(user, "Broken bench", "The bench in the park is broken", **at_new_spot())
        assert res.status_code == 201
        body = res.json()
        assert body["user_id"] == user["id"]
        assert body["status"] == "Pending"
        assert body["outcome"] == "created" and body["duplicate"] is False

    def test_cannot_file_as_someone_else(self, make_user, file_complaint):
        alice, bob = make_user(), make_user()
        res = file_complaint(alice, "Spoof", "garbage", user_id=bob["id"], **at_new_spot())
        assert res.status_code == 403

    @pytest.mark.parametrize("field,value", [("title", ""), ("description", ""), ("latitude", 123.0), ("longitude", -500.0)])
    def test_validation(self, client, make_user, field, value):
        user = make_user()
        body = {"title": "t", "description": "d", "latitude": 20.3, "longitude": 85.8, field: value}
        assert client.post("/api/complaints", json=body, headers=user["headers"]).status_code == 422


class TestAIRouting:
    @pytest.mark.parametrize(
        "description,category,department",
        [
            ("Fire and smoke coming out of a shop", "fire", "Fire Services"),
            ("Two cars collision at the junction", "accident", "Traffic Police"),
            ("Garbage dump overflowing for a week", "garbage", "Sanitation Department"),
            ("Sewage drain blocked near the school", "water", "Water Department"),
            ("Electric wire hanging low from the pole", "electrical", "Electrical Department"),
            ("Huge pothole on the main road", "road", "Public Works Department"),
            ("A fallen tree is blocking the lane", "road", "Public Works Department"),
            ("Streetlight not working since Monday", "electrical", "Electrical Department"),
            ("The park bench needs repainting", "general", "Municipal Control Room"),
        ],
    )
    def test_text_routing(self, make_user, file_complaint, description, category, department):
        res = file_complaint(make_user(), "Report", description, **at_new_spot())
        assert res.status_code == 201
        body = res.json()
        assert body["incident_type"] == category
        assert body["department_name"] == department

    def test_image_detection_routes_when_text_is_vague(self, client, make_user, detector):
        detector.result = {"label": "overflowing_trash", "confidence": 0.91}
        user = make_user()
        spot = at_new_spot()
        res = client.post(
            "/api/complaints/with-image",
            data={"title": "Please look", "description": "See the photo", "latitude": spot["lat"], "longitude": spot["lng"]},
            files={"image": ("photo.png", png_bytes(), "image/png")},
            headers=user["headers"],
        )
        assert res.status_code == 201, res.text
        body = res.json()
        assert detector.calls, "YOLO stub was not called"
        assert body["incident_type"] == "garbage"
        assert body["department_name"] == "Sanitation Department"
        assert body["detection_label"] == "overflowing_trash"
        assert body["image_filename"].endswith(".png")
        assert any(f["signal"] == "image" for f in body["severity_factors"])

    def test_non_image_upload_rejected(self, client, make_user):
        user = make_user()
        res = client.post(
            "/api/complaints/with-image",
            data={"title": "x", "description": "y"},
            files={"image": ("notes.txt", b"hello", "text/plain")},
            headers=user["headers"],
        )
        assert res.status_code == 415


class TestSeverity:
    def test_every_new_complaint_is_scored(self, make_user, file_complaint):
        body = file_complaint(make_user(), "Bench", "Paint peeling off a bench", **at_new_spot()).json()
        assert 1 <= body["severity_score"] <= 10
        assert body["severity_factors"][0]["signal"] == "category"

    def test_dangerous_description_scores_higher_and_raises_priority(self, make_user, file_complaint):
        user = make_user()
        calm = file_complaint(user, "Litter", "A little litter near the gate", **at_new_spot()).json()
        urgent = file_complaint(user, "Fire", "Fire and smoke, people trapped, danger!", **at_new_spot()).json()
        assert urgent["severity_score"] > calm["severity_score"]
        assert urgent["severity_score"] >= 8
        assert urgent["priority"] == "High"


class TestDuplicates:
    def test_nearby_same_category_report_confirms_existing(self, client, make_user, file_complaint):
        first, second = make_user(), make_user()
        original = file_complaint(first, "Garbage pile", "Garbage overflowing at the corner", lat=20.30000, lng=85.80000).json()
        # ~22 m north of the original.
        res = file_complaint(second, "Trash everywhere", "trash dump near the corner", lat=20.30020, lng=85.80000)
        assert res.status_code == 200
        body = res.json()
        assert body["outcome"] == "confirmed" and body["duplicate"] is True
        assert body["complaint_id"] == original["complaint_id"]
        assert body["confirmation_count"] == 1
        assert 15 < body["distance_m"] < 30

        mine = client.get("/api/complaints/my", headers=second["headers"]).json()
        assert [c["complaint_id"] for c in mine] == [original["complaint_id"]]
        assert client.get(f"/api/complaints/{original['complaint_id']}/updates", headers=second["headers"]).status_code == 200

    def test_same_citizen_reporting_again_is_not_double_counted(self, make_user, file_complaint):
        citizen, other = make_user(), make_user()
        original = file_complaint(citizen, "Garbage", "garbage pile", lat=20.31, lng=85.81).json()
        again = file_complaint(citizen, "Garbage", "garbage pile again", lat=20.31001, lng=85.81).json()
        assert again["outcome"] == "already_reported" and again["confirmation_count"] == 0

        file_complaint(other, "Garbage", "garbage", lat=20.31, lng=85.81001)
        repeat = file_complaint(other, "Garbage", "still garbage", lat=20.31, lng=85.81).json()
        assert repeat["outcome"] == "already_reported"
        assert repeat["confirmation_count"] == 1
        assert repeat["complaint_id"] == original["complaint_id"]

    @pytest.mark.parametrize(
        "second_description,lat_offset,why",
        [
            ("garbage pile", 0.0006, "about 67 m away"),
            ("huge pothole here", 0.0, "different category"),
        ],
    )
    def test_not_a_duplicate(self, make_user, file_complaint, second_description, lat_offset, why):
        file_complaint(make_user(), "Garbage", "garbage pile", lat=20.32, lng=85.82)
        res = file_complaint(make_user(), "Report", second_description, lat=20.32 + lat_offset, lng=85.82)
        assert res.status_code == 201, why
        assert res.json()["outcome"] == "created"

    def test_older_than_24_hours_is_not_a_duplicate(self, make_user, file_complaint, backdate):
        original = file_complaint(make_user(), "Garbage", "garbage pile", lat=20.33, lng=85.83).json()
        backdate(original["complaint_id"], 25)
        assert file_complaint(make_user(), "Garbage", "garbage", lat=20.33, lng=85.83).status_code == 201

    def test_resolved_complaint_is_not_a_duplicate(self, client, make_user, file_complaint):
        admin = make_user("admin")
        original = file_complaint(make_user(), "Garbage", "garbage pile", lat=20.34, lng=85.84).json()
        client.patch(f"/api/admin/complaints/{original['complaint_id']}/status", json={"status": "Resolved"}, headers=admin["headers"])
        assert file_complaint(make_user(), "Garbage", "garbage", lat=20.34, lng=85.84).status_code == 201

    def test_reports_without_location_are_never_merged(self, make_user, file_complaint):
        file_complaint(make_user(), "Garbage", "garbage pile", lat=None, lng=None)
        assert file_complaint(make_user(), "Garbage", "garbage pile", lat=None, lng=None).status_code == 201

    def test_three_confirmations_bump_severity(self, client, make_user, file_complaint):
        owner = make_user()
        original = file_complaint(owner, "Garbage", "garbage pile", lat=20.35, lng=85.85).json()
        last = None
        for _ in range(3):
            last = file_complaint(make_user(), "Garbage", "garbage", lat=20.35, lng=85.85).json()
        assert last["confirmation_count"] == 3
        assert last["severity_score"] == min(10, original["severity_score"] + 1)
        # The reasons (which can quote the original text) are only shown to the owner and staff.
        assert last["severity_factors"] is None
        [mine] = client.get("/api/complaints/my", headers=owner["headers"]).json()
        assert any(f["signal"] == "crowd" for f in mine["severity_factors"])


class TestOwnership:
    def test_my_complaints_only_returns_own(self, client, make_user, file_complaint):
        alice, bob = make_user(), make_user()
        a1 = file_complaint(alice, "A1", "garbage", **at_new_spot()).json()
        a2 = file_complaint(alice, "A2", "pothole", **at_new_spot()).json()
        file_complaint(bob, "B1", "garbage", **at_new_spot())

        mine = client.get("/api/complaints/my", headers=alice["headers"])
        assert mine.status_code == 200
        assert [c["complaint_id"] for c in mine.json()] == [a2["complaint_id"], a1["complaint_id"]]  # newest first

    def test_my_complaints_requires_login(self, client):
        assert client.get("/api/complaints/my").status_code in (401, 403)

    def test_other_citizens_cannot_read_history(self, client, make_user, file_complaint):
        alice, bob = make_user(), make_user()
        complaint = file_complaint(alice, "A", "garbage", **at_new_spot()).json()
        assert client.get(f"/api/complaints/{complaint['complaint_id']}/updates", headers=alice["headers"]).status_code == 200
        assert client.get(f"/api/complaints/{complaint['complaint_id']}/updates", headers=bob["headers"]).status_code == 404
