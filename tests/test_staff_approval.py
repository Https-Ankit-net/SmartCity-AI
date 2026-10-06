"""Department staff accounts need admin approval before they can sign in."""

from __future__ import annotations

import pytest

from conftest import PASSWORD


def login(client, email):
    return client.post("/api/auth/login", json={"email": email, "password": PASSWORD})


@pytest.fixture()
def applicant(make_user):
    """A self-registered, still-pending Sanitation staff account."""
    return make_user("department", "Sanitation Department", name="Sunita Patra", approved=False)


class TestRegistration:
    def test_self_registered_staff_start_pending(self, applicant):
        assert applicant["account_status"] == "pending"
        assert applicant["role"] == "department"

    def test_citizens_are_active_immediately(self, client, make_user):
        citizen = make_user()
        assert client.get("/api/auth/me", headers=citizen["headers"]).json()["account_status"] == "active"

    def test_department_is_required(self, client):
        body = {"full_name": "No Dept", "email": "nodept@example.test", "password": PASSWORD, "role": "department"}
        assert client.post("/api/users", json=body).status_code == 422
        assert client.post("/api/users", json={**body, "department_id": 999999}).status_code == 422

    def test_admin_created_staff_are_active(self, client, make_user, departments):
        admin = make_user("admin")
        body = {
            "full_name": "Hired Officer", "email": "hired@example.test", "password": PASSWORD,
            "role": "department", "department_id": departments["Fire Services"],
        }
        res = client.post("/api/users", json=body, headers=admin["headers"])
        assert res.status_code == 201
        assert res.json()["account_status"] == "active"
        assert login(client, "hired@example.test").status_code == 200

    def test_admins_get_a_live_notification(self, client, make_user, departments):
        admin = make_user("admin")
        with client.websocket_connect(f"/ws/notifications/{admin['id']}?token={admin['token']}") as ws:
            client.post("/api/users", json={
                "full_name": "New Officer", "email": "newofficer@example.test", "password": PASSWORD,
                "role": "department", "department_id": departments["Water Department"],
            })
            message = ws.receive_json()
        assert message["type"] == "staff_request"
        assert "New Officer" in message["message"] and "Water Department" in message["message"]


class TestLoginGate:
    def test_pending_account_cannot_log_in(self, client, applicant):
        res = login(client, applicant["email"])
        assert res.status_code == 403
        assert "awaiting approval" in res.json()["detail"]

    def test_wrong_password_does_not_reveal_status(self, client, applicant):
        res = client.post("/api/auth/login", json={"email": applicant["email"], "password": "not-the-password"})
        assert res.status_code == 401

    def test_rejected_account_sees_reason(self, client, make_user, applicant):
        admin = make_user("admin")
        client.post(f"/api/admin/staff/{applicant['id']}/reject", json={"reason": "Not on the department roster"}, headers=admin["headers"])
        res = login(client, applicant["email"])
        assert res.status_code == 403
        assert "Not on the department roster" in res.json()["detail"]


class TestReview:
    def test_admin_lists_pending_requests_with_counts(self, client, make_user, applicant):
        admin = make_user("admin")
        make_user("department", "Fire Services")  # already active
        body = client.get("/api/admin/staff?status=pending", headers=admin["headers"]).json()
        assert [a["user_id"] for a in body["items"]] == [applicant["id"]]
        assert body["items"][0]["department_name"] == "Sanitation Department"
        assert body["counts"] == {"pending": 1, "active": 1, "rejected": 0}

    @pytest.mark.parametrize("who", ["anonymous", "citizen", "department"])
    def test_only_admins_can_review(self, client, make_user, applicant, who):
        headers = {} if who == "anonymous" else make_user(who, "Fire Services" if who == "department" else None)["headers"]
        assert client.get("/api/admin/staff", headers=headers).status_code in (401, 403)
        assert client.post(f"/api/admin/staff/{applicant['id']}/approve", json={}, headers=headers).status_code in (401, 403)

    def test_approve_lets_them_in(self, client, make_user, applicant):
        admin = make_user("admin", name="Meera Admin")
        res = client.post(f"/api/admin/staff/{applicant['id']}/approve", json={"note": "Verified with HR"}, headers=admin["headers"])
        assert res.status_code == 200
        body = res.json()
        assert body["account_status"] == "active"
        assert body["reviewed_by_name"] == "Meera Admin"
        assert body["review_note"] == "Verified with HR"

        token = login(client, applicant["email"]).json()["access_token"]
        assert client.get("/api/admin/complaints", headers={"Authorization": f"Bearer {token}"}).status_code == 200

    def test_approve_can_correct_the_department(self, client, make_user, applicant, departments):
        admin = make_user("admin")
        res = client.post(
            f"/api/admin/staff/{applicant['id']}/approve",
            json={"department_id": departments["Water Department"]},
            headers=admin["headers"],
        )
        assert res.json()["department_name"] == "Water Department"

    def test_revoking_an_active_account_kills_existing_tokens(self, client, make_user):
        admin = make_user("admin")
        officer = make_user("department", "Fire Services")
        assert client.get("/api/admin/complaints", headers=officer["headers"]).status_code == 200

        client.post(f"/api/admin/staff/{officer['id']}/reject", json={"reason": "Left the department"}, headers=admin["headers"])
        assert client.get("/api/admin/complaints", headers=officer["headers"]).status_code == 403
        assert login(client, officer["email"]).status_code == 403

        client.post(f"/api/admin/staff/{officer['id']}/approve", json={}, headers=admin["headers"])
        assert client.get("/api/admin/complaints", headers=officer["headers"]).status_code == 200

    def test_invalid_transitions(self, client, make_user, applicant):
        admin = make_user("admin")
        citizen = make_user()
        path = f"/api/admin/staff/{applicant['id']}"
        assert client.post(f"{path}/approve", json={}, headers=admin["headers"]).status_code == 200
        assert client.post(f"{path}/approve", json={}, headers=admin["headers"]).status_code == 400
        assert client.post(f"{path}/reject", json={}, headers=admin["headers"]).status_code == 200
        assert client.post(f"{path}/reject", json={}, headers=admin["headers"]).status_code == 400
        # Only department accounts are reviewed here.
        assert client.post(f"/api/admin/staff/{citizen['id']}/reject", json={}, headers=admin["headers"]).status_code == 404
        assert client.post("/api/admin/staff/999999/approve", json={}, headers=admin["headers"]).status_code == 404
