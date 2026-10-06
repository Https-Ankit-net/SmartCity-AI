"""Severity scoring, duplicate detection internals, text analysis and the Redis event bus."""

from __future__ import annotations

import asyncio
import json

import pytest

from app.services import severity_service, text_analysis
from app.services.infrastructure import InfrastructureIndex, haversine_m
from app.services.severity_service import keyword_hits, priority_for, score_severity


class TestSeverity:
    def test_score_is_clamped_to_1_10(self):
        worst = score_severity(
            category="fire",
            text="fire explosion trapped injured bleeding danger smoke",
            detection_label="fallen_tree",
            detection_confidence=0.99,
        )
        assert worst.score == 10
        assert score_severity(category="general", text="").score >= 1

    def test_keywords_and_negation(self):
        assert [p for p, _ in keyword_hits("Blocked highway after a crash")] == ["blocked highway"]
        assert keyword_hits("there is no fire here") == []
        assert keyword_hits("not dangerous at all") == []

    def test_keyword_contribution_is_capped(self):
        many = score_severity(category="general", text="fire explosion trapped injured bleeding")
        keyword_points = sum(f["points"] for f in many.factors if f["signal"] == "keyword")
        assert keyword_points == severity_service.KEYWORD_CAP

    def test_low_confidence_detection_ignored(self):
        result = score_severity(category="road", text="", detection_label="pothole", detection_confidence=0.2)
        assert not any(f["signal"] == "image" for f in result.factors)

    def test_proximity_to_hospital_and_major_road(self, monkeypatch):
        index = InfrastructureIndex(
            points=[{"kind": "hospital", "name": "City Hospital", "lat": 20.3000, "lng": 85.8000}],
            roads=[{"kind": "trunk", "name": "NH16", "coords": [[20.2990, 85.7990], [20.2990, 85.8010]]}],
        )
        monkeypatch.setattr(severity_service, "get_index", lambda: index)
        near = score_severity(category="road", text="pothole", latitude=20.2991, longitude=85.8000)
        far = score_severity(category="road", text="pothole", latitude=20.4000, longitude=85.9000)
        details = [f["detail"] for f in near.factors if f["signal"] == "proximity"]
        assert any("City Hospital" in d for d in details)
        assert any("trunk road (NH16)" in d for d in details)
        assert near.score > far.score

    def test_priority_bands(self):
        assert [priority_for(s) for s in (1, 3, 4, 6, 7, 10)] == ["Low", "Low", "Medium", "Medium", "High", "High"]

    def test_real_infrastructure_data_loads(self):
        from app.services.infrastructure import get_index

        index = get_index()
        assert len(index.points) > 100, "backend/app/ai/data/infrastructure.json missing or empty"


def test_haversine_known_distance():
    # 0.001 degrees of latitude is ~111 m everywhere.
    assert haversine_m(20.0, 85.0, 20.001, 85.0) == pytest.approx(111.2, abs=0.5)


class TestTextAnalysis:
    @pytest.mark.parametrize(
        "text,intent,category",
        [
            ("There is a huge pothole near Rasulgarh Square, two bikes fell yesterday", "report_issue", "road"),
            ("Fire in the transformer, people are trapped, help!", "emergency", "fire"),
            ("Any update on my complaint #42? The garbage is still not cleared.", "follow_up", "garbage"),
            ("Thank you for fixing the streetlights so quickly.", "feedback", "electrical"),
            ("What time does the garbage truck come?", "question", "garbage"),
        ],
    )
    def test_intent_and_category(self, text, intent, category):
        result = text_analysis.analyze_text(text)
        assert result["intent"]["label"] == intent
        assert result["suggested_category"] == category

    def test_extracts_landmark_and_title(self):
        result = text_analysis.analyze_text("Huge pothole near Rasulgarh Square since yesterday")
        assert any("Rasulgarh Square" in loc for loc in result["locations"])
        assert result["suggested_title"].startswith("Pothole near Rasulgarh Square")
        assert result["key_issues"][0]["issue"] == "pothole"

    def test_backend_reported(self):
        backend = text_analysis.analyze_text("pothole")["backend"]
        assert backend == "rules" or backend.startswith("spacy:")

    def test_endpoint_requires_login_and_validates(self, client, make_user):
        assert client.post("/api/ai/analyze-text", json={"text": "pothole"}).status_code in (401, 403)
        user = make_user()
        assert client.post("/api/ai/analyze-text", json={"text": ""}, headers=user["headers"]).status_code == 422
        res = client.post(
            "/api/ai/analyze-text",
            json={"text": "Live wire sparking near the school gate", "latitude": 20.3, "longitude": 85.8},
            headers=user["headers"],
        )
        assert res.status_code == 200
        body = res.json()
        assert body["suggested_category"] == "electrical"
        assert body["suggested_department"] == "Electrical Department"
        assert 1 <= body["severity"]["score"] <= 10


@pytest.mark.redis
class TestEventBus:
    def test_events_fan_out_through_redis(self, monkeypatch):
        """Two buses sharing one (fake) Redis: an event published on one reaches sockets on both."""
        import fakeredis

        from app.core import event_bus as bus_module

        delivered: list[dict] = []

        async def fake_deliver(event: dict) -> None:
            delivered.append(event)

        monkeypatch.setattr(bus_module, "deliver", fake_deliver)

        async def scenario() -> None:
            server = fakeredis.FakeServer()
            worker_a, worker_b = bus_module.EventBus(), bus_module.EventBus()
            factory = lambda: fakeredis.FakeAsyncRedis(server=server, decode_responses=True)  # noqa: E731
            await worker_a.start("redis://fake", client_factory=factory)
            await worker_b.start("redis://fake", client_factory=factory)
            assert worker_a.distributed and worker_b.distributed
            await asyncio.sleep(0.1)  # let both listeners subscribe
            await worker_a.publish({"target": "user", "user_id": 7, "message": {"type": "status_update"}})
            for _ in range(50):
                if len(delivered) >= 2:
                    break
                await asyncio.sleep(0.05)
            await worker_a.stop()
            await worker_b.stop()

        asyncio.run(scenario())
        assert len(delivered) == 2, "each worker should deliver the event to its own sockets"
        assert all(e["user_id"] == 7 for e in delivered)

    def test_falls_back_to_local_delivery_when_redis_down(self, monkeypatch):
        from app.core import event_bus as bus_module

        delivered: list[dict] = []

        async def fake_deliver(event: dict) -> None:
            delivered.append(event)

        monkeypatch.setattr(bus_module, "deliver", fake_deliver)

        class Down:
            async def ping(self):
                raise ConnectionError("redis is down")

        async def scenario() -> None:
            bus = bus_module.EventBus()
            await bus.start("redis://nowhere", client_factory=Down)
            assert not bus.distributed
            await bus.publish({"target": "complaints", "message": {"type": "new_complaint"}})

        asyncio.run(scenario())
        assert delivered == [{"target": "complaints", "message": {"type": "new_complaint"}}]

    def test_payloads_are_json_serialisable(self, make_user, file_complaint, db):
        from app.models import Complaint
        from app.services import notify

        complaint = db.get(Complaint, file_complaint(make_user(), "Garbage", "garbage").json()["complaint_id"])
        for payload in notify._payloads("new_complaint", complaint, "hello"):
            json.dumps(payload)
