"""Admin / department RBAC and the status-update workflow."""

from __future__ import annotations

import pytest

ADMIN_GETS = ["/api/admin/dashboard", "/api/admin/complaints"]


@pytest.fixture()
def garbage(make_user, file_complaint):
    """A Pending garbage complaint (routed to Sanitation) and its citizen."""
    citizen = make_user()
    complaint = file_complaint(citizen, "Garbage pile", "Garbage overflowing near the market", lat=20.28, lng=85.83).json()
    return citizen, complaint


class TestRBAC:
    @pytest.mark.parametrize("path", ADMIN_GETS)
    def test_anonymous_blocked(self, client, path):
        assert client.get(path).status_code in (401, 403)

    @pytest.mark.parametrize("path", ADMIN_GETS)
    def test_citizen_blocked(self, client, make_user, path):
        assert client.get(path, headers=make_user()["headers"]).status_code == 403

    def test_citizen_cannot_change_status(self, client, garbage):
        citizen, complaint = garbage
        res = client.patch(f"/api/admin/complaints/{complaint['complaint_id']}/status", json={"status": "Resolved"}, headers=citizen["headers"])
        assert res.status_code == 403

    @pytest.mark.parametrize("path", ADMIN_GETS)
    def test_admin_allowed(self, client, make_user, path):
        assert client.get(path, headers=make_user("admin")["headers"]).status_code == 200

    def test_department_staff_scoped_to_own_department(self, client, make_user, garbage, file_complaint):
        _, complaint = garbage
        file_complaint(make_user(), "Pothole", "pothole on the road", lat=20.25, lng=85.75)
        sanitation = make_user("department", "Sanitation Department")

        page = client.get("/api/admin/complaints", headers=sanitation["headers"]).json()
        assert [c["complaint_id"] for c in page["items"]] == [complaint["complaint_id"]]

        dashboard = client.get("/api/admin/dashboard", headers=sanitation["headers"]).json()
        assert dashboard["scope"]["department_name"] == "Sanitation Department"
        assert dashboard["total_complaints"] == 1
        assert dashboard["total_users"] is None  # city-wide numbers are admin-only

    def test_department_cannot_touch_other_departments(self, client, make_user, garbage):
        _, complaint = garbage
        fire = make_user("department", "Fire Services")
        path = f"/api/admin/complaints/{complaint['complaint_id']}"
        assert client.get(path, headers=fire["headers"]).status_code == 403
        assert client.patch(f"{path}/status", json={"status": "Resolved"}, headers=fire["headers"]).status_code == 403

    def test_department_account_without_department_blocked(self, client):
        # Registration now requires a department; legacy accounts may still lack one.
        from app.manage import create_user
        from conftest import PASSWORD

        create_user("Legacy Officer", "legacy@example.test", PASSWORD, role="department")
        token = client.post("/api/auth/login", json={"email": "legacy@example.test", "password": PASSWORD}).json()["access_token"]
        assert client.get("/api/admin/complaints", headers={"Authorization": f"Bearer {token}"}).status_code == 403


class TestStatusUpdate:
    def url(self, complaint):
        return f"/api/admin/complaints/{complaint['complaint_id']}/status"

    def test_status_change_records_history_and_notes(self, client, make_user, garbage):
        citizen, complaint = garbage
        staff = make_user("department", "Sanitation Department")
        res = client.patch(
            self.url(complaint),
            json={"status": "in_progress", "internal_note": "Crew 4", "public_response": "Crew on the way"},
            headers=staff["headers"],
        )
        assert res.status_code == 200
        body = res.json()
        assert body["status"] == "In Progress"  # normalised
        update = body["updates"][-1]
        assert (update["from_status"], update["to_status"]) == ("Pending", "In Progress")
        assert update["internal_note"] == "Crew 4"
        assert update["actor_name"] == staff["full_name"]

        public = client.get(f"/api/complaints/{complaint['complaint_id']}/updates", headers=citizen["headers"]).json()
        assert public[-1]["public_response"] == "Crew on the way"
        assert "internal_note" not in public[-1]

    @pytest.mark.parametrize("status", ["Closed", "", "DONE"])
    def test_unknown_status_rejected(self, client, make_user, garbage, status):
        _, complaint = garbage
        res = client.patch(self.url(complaint), json={"status": status}, headers=make_user("admin")["headers"])
        assert res.status_code == 422

    def test_noop_change_without_note_rejected(self, client, make_user, garbage):
        _, complaint = garbage
        admin = make_user("admin")
        assert client.patch(self.url(complaint), json={"status": "Pending"}, headers=admin["headers"]).status_code == 400
        res = client.patch(self.url(complaint), json={"status": "Pending", "internal_note": "Checked"}, headers=admin["headers"])
        assert res.status_code == 200
        assert res.json()["updates"][-1]["kind"] == "note"

    def test_unknown_complaint_404(self, client, make_user):
        res = client.patch("/api/admin/complaints/999999/status", json={"status": "Resolved"}, headers=make_user("admin")["headers"])
        assert res.status_code == 404

    def test_owner_notified_over_websocket(self, client, make_user, garbage):
        citizen, complaint = garbage
        admin = make_user("admin")
        with client.websocket_connect(f"/ws/notifications/{citizen['id']}?token={citizen['token']}") as ws:
            client.patch(self.url(complaint), json={"status": "Resolved", "public_response": "Cleared"}, headers=admin["headers"])
            message = ws.receive_json()
        assert message["type"] == "status_update"
        assert message["data"]["complaint_id"] == complaint["complaint_id"]
        assert message["response"] == "Cleared"

    def test_confirming_citizens_are_notified_too(self, client, make_user, garbage, file_complaint):
        _, complaint = garbage
        confirmer = make_user()
        merged = file_complaint(confirmer, "Same garbage", "garbage pile here too", lat=20.28, lng=85.83001).json()
        assert merged["outcome"] == "confirmed"
        with client.websocket_connect(f"/ws/notifications/{confirmer['id']}?token={confirmer['token']}") as ws:
            client.patch(self.url(complaint), json={"status": "Resolved"}, headers=make_user("admin")["headers"])
            message = ws.receive_json()
        assert message["message"].startswith("A complaint you confirmed")
        assert message["data"]["status"] == "Resolved"

    def test_websocket_rejects_someone_elses_token(self, client, make_user):
        from starlette.websockets import WebSocketDisconnect

        alice, bob = make_user(), make_user()
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"/ws/notifications/{alice['id']}?token={bob['token']}") as ws:
                ws.receive_json()

    def test_legacy_endpoint_is_admin_only_and_validated(self, client, make_user, garbage):
        _, complaint = garbage
        path = f"/api/complaints/{complaint['complaint_id']}/status"
        staff = make_user("department", "Sanitation Department")
        admin = make_user("admin")
        assert client.patch(path, json={"status": "Resolved"}, headers=staff["headers"]).status_code == 403
        assert client.patch(path, json={"status": "Nonsense"}, headers=admin["headers"]).status_code == 422
        assert client.patch(path, json={"status": "Resolved"}, headers=admin["headers"]).json()["status"] == "Resolved"


class TestReassign:
    def test_reassign_moves_complaint(self, client, make_user, garbage, departments):
        _, complaint = garbage
        res = client.patch(
            f"/api/admin/complaints/{complaint['complaint_id']}/department",
            json={"department_id": departments["Water Department"]},
            headers=make_user("admin")["headers"],
        )
        assert res.status_code == 200
        assert res.json()["department_name"] == "Water Department"

    def test_reassign_validation(self, client, make_user, garbage, departments):
        _, complaint = garbage
        admin = make_user("admin")
        path = f"/api/admin/complaints/{complaint['complaint_id']}/department"
        assert client.patch(path, json={"department_id": 999999}, headers=admin["headers"]).status_code == 404
        same = departments["Sanitation Department"]
        assert client.patch(path, json={"department_id": same}, headers=admin["headers"]).status_code == 400


class TestListing:
    def test_pagination_and_sort_validation(self, client, make_user, file_complaint):
        citizen = make_user()
        for i in range(5):
            file_complaint(citizen, f"C{i}", "garbage", lat=20.2 + i * 0.01, lng=85.8)
        admin = make_user("admin")
        page = client.get("/api/admin/complaints?page=2&page_size=2&sort_by=id&sort_dir=asc", headers=admin["headers"]).json()
        assert page["total"] == 5 and page["pages"] == 3 and len(page["items"]) == 2
        assert page["items"][0]["complaint_id"] < page["items"][1]["complaint_id"]
        assert client.get("/api/admin/complaints?sort_by=bogus", headers=admin["headers"]).status_code == 422
        assert client.get("/api/admin/complaints?page_size=1000", headers=admin["headers"]).status_code == 422
