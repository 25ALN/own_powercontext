from fastapi.testclient import TestClient

from app import main


class FakePowerContext:
    async def get_scope(self, scope_id):
        return {"scope_id": scope_id, "title": "Sample", "summary": "Filtering"}

    async def list_memory_entries(self, scope_id):
        return [
            {"kind": "decision", "text": "Keep this", "artifact": {"artifact_id": "1", "revision": 1}},
            {"kind": "fact", "text": "Keep that", "artifact": {"artifact_id": "2", "revision": 1}},
        ]


def test_topic_detail_filters_by_kind_and_preserves_unfiltered_order(monkeypatch):
    monkeypatch.setattr(main, "get_client", lambda request: FakePowerContext())
    with TestClient(main.app) as client:
        filtered = client.get("/api/topics/scope-1?kind=decision")
        assert [note["kind"] for note in filtered.json()["notes"]] == ["decision"]
        unfiltered = client.get("/api/topics/scope-1")
        assert [note["kind"] for note in unfiltered.json()["notes"]] == ["decision", "fact"]
        unknown = client.get("/api/topics/scope-1?kind=unknown")
        assert unknown.json()["notes"] == []
