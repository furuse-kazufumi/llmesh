"""Tests for TimelineStore and server timeline endpoints."""
from __future__ import annotations

import time
import uuid
import pytest
from llmesh.timeline.store import TimelineStore


# ---------------------------------------------------------------------------
# TimelineStore unit tests
# ---------------------------------------------------------------------------

@pytest.fixture
def store(tmp_path):
    return TimelineStore(tmp_path / "timeline.db")


class TestRecord:
    def test_record_and_retrieve(self, store):
        tid = str(uuid.uuid4())
        store.record(tid, "node-A", "received", tool="generate_code")
        events = store.get_task_timeline(tid)
        assert len(events) == 1
        assert events[0].task_id == tid
        assert events[0].event_type == "received"
        assert events[0].metadata["tool"] == "generate_code"

    def test_multiple_events_in_order(self, store):
        tid = str(uuid.uuid4())
        for et in ("received", "firewall_allow", "llm_invoked", "completed"):
            store.record(tid, "node-A", et)
        events = store.get_task_timeline(tid)
        assert [e.event_type for e in events] == [
            "received", "firewall_allow", "llm_invoked", "completed"
        ]

    def test_record_never_raises(self, store):
        # Even invalid metadata values should not raise
        store.record("bad-task", "n", "received", obj=object())


class TestTerminal:
    def test_completed_is_terminal(self, store):
        tid = str(uuid.uuid4())
        store.record(tid, "n", "received")
        store.record(tid, "n", "completed", elapsed_ms=42)
        events = store.get_task_timeline(tid)
        assert events[-1].is_terminal

    def test_failed_is_terminal(self, store):
        tid = str(uuid.uuid4())
        store.record(tid, "n", "received")
        store.record(tid, "n", "failed", reason="backend_error")
        assert store.get_task_timeline(tid)[-1].is_terminal

    def test_in_progress_not_terminal(self, store):
        tid = str(uuid.uuid4())
        store.record(tid, "n", "received")
        store.record(tid, "n", "llm_invoked")
        events = store.get_task_timeline(tid)
        assert not events[-1].is_terminal


class TestResumable:
    def test_incomplete_task_is_resumable(self, store):
        tid = str(uuid.uuid4())
        store.record(tid, "node-X", "received")
        store.record(tid, "node-X", "llm_invoked")
        resumable = store.get_resumable_tasks()
        tids = [r["task_id"] for r in resumable]
        assert tid in tids

    def test_completed_task_not_resumable(self, store):
        tid = str(uuid.uuid4())
        store.record(tid, "node-X", "received")
        store.record(tid, "node-X", "completed")
        resumable = store.get_resumable_tasks()
        assert tid not in [r["task_id"] for r in resumable]

    def test_failed_task_not_resumable(self, store):
        tid = str(uuid.uuid4())
        store.record(tid, "node-X", "received")
        store.record(tid, "node-X", "failed", reason="backend_error")
        resumable = store.get_resumable_tasks()
        assert tid not in [r["task_id"] for r in resumable]

    def test_resumable_includes_idle_sec(self, store):
        tid = str(uuid.uuid4())
        store.record(tid, "node-X", "received")
        resumable = store.get_resumable_tasks()
        entry = next(r for r in resumable if r["task_id"] == tid)
        assert int(entry["idle_sec"]) >= 0


class TestRecentEvents:
    def test_recent_returns_newest_first(self, store):
        for i in range(5):
            store.record(str(uuid.uuid4()), "n", "received", seq=i)
        events = store.get_recent_events(limit=3)
        assert len(events) == 3
        # event_ids should be descending
        assert events[0].event_id > events[1].event_id

    def test_filter_by_node(self, store):
        for _ in range(3):
            store.record(str(uuid.uuid4()), "node-A", "received")
        for _ in range(2):
            store.record(str(uuid.uuid4()), "node-B", "received")
        a_events = store.get_recent_events(limit=10, node_id="node-A")
        assert all(e.node_id == "node-A" for e in a_events)
        assert len(a_events) == 3


class TestDeltaMs:
    def test_delta_ms_same_event_is_zero(self, store):
        tid = str(uuid.uuid4())
        store.record(tid, "n", "received")
        events = store.get_task_timeline(tid)
        assert events[0].delta_ms(events[0]) == 0

    def test_delta_ms_later_is_positive(self, store):
        tid = str(uuid.uuid4())
        store.record(tid, "n", "received")
        time.sleep(0.01)
        store.record(tid, "n", "completed")
        events = store.get_task_timeline(tid)
        assert events[1].delta_ms(events[0]) >= 0


class TestEventCount:
    def test_event_count(self, store):
        for _ in range(7):
            store.record(str(uuid.uuid4()), "n", "received")
        assert store.event_count() == 7


# ---------------------------------------------------------------------------
# Server timeline endpoint tests
# ---------------------------------------------------------------------------

from unittest.mock import patch
from fastapi.testclient import TestClient
from llmesh.mcp.server import app

client = TestClient(app, raise_server_exceptions=False)


class TestTimelineEndpointsDisabled:
    """When timeline is not configured, endpoints return 503."""

    def test_task_endpoint_503(self):
        with patch("llmesh.mcp.server._timeline", None):
            resp = client.get(f"/timeline/task/{uuid.uuid4()}")
        assert resp.status_code == 503

    def test_recent_endpoint_503(self):
        with patch("llmesh.mcp.server._timeline", None):
            resp = client.get("/timeline/recent")
        assert resp.status_code == 503

    def test_resumable_endpoint_503(self):
        with patch("llmesh.mcp.server._timeline", None):
            resp = client.get("/timeline/resumable")
        assert resp.status_code == 503


class TestTimelineEndpointsEnabled:
    def _mock_store(self, tmp_path):
        return TimelineStore(tmp_path / "tl.db")

    def test_task_404_when_not_found(self, tmp_path):
        store = self._mock_store(tmp_path)
        with patch("llmesh.mcp.server._timeline", store):
            resp = client.get(f"/timeline/task/{uuid.uuid4()}")
        assert resp.status_code == 404

    def test_task_returns_events(self, tmp_path):
        store = self._mock_store(tmp_path)
        tid = str(uuid.uuid4())
        store.record(tid, "n1", "received", tool="generate_code")
        store.record(tid, "n1", "completed", elapsed_ms=99)
        with patch("llmesh.mcp.server._timeline", store):
            resp = client.get(f"/timeline/task/{tid}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["task_id"] == tid
        assert data["terminal"] is True
        assert data["resumable"] is False
        assert len(data["events"]) == 2

    def test_recent_returns_list(self, tmp_path):
        store = self._mock_store(tmp_path)
        for _ in range(3):
            store.record(str(uuid.uuid4()), "n1", "received")
        with patch("llmesh.mcp.server._timeline", store):
            resp = client.get("/timeline/recent?limit=2")
        assert resp.status_code == 200
        assert resp.json()["count"] == 2

    def test_resumable_returns_incomplete_tasks(self, tmp_path):
        store = self._mock_store(tmp_path)
        tid = str(uuid.uuid4())
        store.record(tid, "n1", "received")
        store.record(tid, "n1", "llm_invoked")
        with patch("llmesh.mcp.server._timeline", store):
            resp = client.get("/timeline/resumable")
        assert resp.status_code == 200
        tasks = resp.json()["tasks"]
        assert any(t["task_id"] == tid for t in tasks)


# ---------------------------------------------------------------------------
# F25 (f): /timeline/ingest endpoint (external producers like llive)
# ---------------------------------------------------------------------------


def _ingest_body(
    event_type: str = "bwt_summary",
    *,
    task_id: str | None = None,
    node_id: str | None = None,
    metadata: dict | None = None,
) -> dict:
    """Build a minimal valid ingest body, with overrides."""
    body: dict = {
        "task_id": task_id or str(uuid.uuid4()),
        "event_type": event_type,
        "metadata": metadata if metadata is not None else {"bwt": -0.008, "n_tasks": 5},
    }
    if node_id is not None:
        body["node_id"] = node_id
    return body


class TestTimelineIngestDisabled:
    """When LLMESH_TIMELINE_DB_PATH is unset, ingest returns 503."""

    def test_ingest_returns_503_when_timeline_not_configured(self):
        with patch("llmesh.mcp.server._timeline", None):
            resp = client.post("/timeline/ingest", json=_ingest_body())
        assert resp.status_code == 503
        assert resp.json()["detail"] == "timeline_not_configured"


class TestTimelineIngestEnabled:
    def _mock_store(self, tmp_path):
        return TimelineStore(tmp_path / "tl.db")

    # ----- happy path -----

    def test_ingest_stores_event(self, tmp_path):
        store = self._mock_store(tmp_path)
        body = _ingest_body(
            event_type="bwt_summary",
            metadata={"bwt": -0.008, "n_tasks": 5, "avg_accuracy": 0.78},
        )
        with patch("llmesh.mcp.server._timeline", store):
            resp = client.post("/timeline/ingest", json=body)
        assert resp.status_code == 200
        assert resp.json() == {"stored": True}
        events = store.get_task_timeline(body["task_id"])
        assert len(events) == 1
        assert events[0].event_type == "bwt_summary"
        assert events[0].metadata["bwt"] == -0.008
        assert events[0].metadata["n_tasks"] == 5

    def test_ingest_accepts_route_trace_event(self, tmp_path):
        store = self._mock_store(tmp_path)
        body = _ingest_body(
            event_type="route_trace",
            metadata={"subblocks": [], "metrics": {"latency_ms": 1.0}},
        )
        with patch("llmesh.mcp.server._timeline", store):
            resp = client.post("/timeline/ingest", json=body)
        assert resp.status_code == 200

    def test_ingest_accepts_concept_update_event(self, tmp_path):
        store = self._mock_store(tmp_path)
        body = _ingest_body(
            event_type="concept_update",
            metadata={"concept_id": "memory-consolidation"},
        )
        with patch("llmesh.mcp.server._timeline", store):
            resp = client.post("/timeline/ingest", json=body)
        assert resp.status_code == 200

    def test_ingest_then_read_round_trip(self, tmp_path):
        """Ingest → /timeline/recent → 同じ event が読める."""
        store = self._mock_store(tmp_path)
        body = _ingest_body(
            event_type="bwt_summary",
            node_id="llive-instance-1",
            metadata={"bwt": -0.01},
        )
        with patch("llmesh.mcp.server._timeline", store):
            ingest_resp = client.post("/timeline/ingest", json=body)
            recent_resp = client.get(
                "/timeline/recent?node_id=llive-instance-1"
            )
        assert ingest_resp.status_code == 200
        assert recent_resp.status_code == 200
        events = recent_resp.json()["events"]
        assert len(events) == 1
        assert events[0]["event_type"] == "bwt_summary"
        assert events[0]["node_id"] == "llive-instance-1"

    def test_ingest_node_id_from_header(self, tmp_path):
        """node_id を body に書かなくても X-Node-Id ヘッダから取れる."""
        store = self._mock_store(tmp_path)
        body = _ingest_body()
        body.pop("node_id", None)  # 念のため削除
        with patch("llmesh.mcp.server._timeline", store):
            resp = client.post(
                "/timeline/ingest",
                json=body,
                headers={"X-Node-Id": "header-supplied"},
            )
        assert resp.status_code == 200
        events = store.get_task_timeline(body["task_id"])
        assert events[0].node_id == "header-supplied"

    def test_ingest_body_node_id_takes_precedence_over_header(self, tmp_path):
        store = self._mock_store(tmp_path)
        body = _ingest_body(node_id="body-wins")
        with patch("llmesh.mcp.server._timeline", store):
            resp = client.post(
                "/timeline/ingest",
                json=body,
                headers={"X-Node-Id": "header-loses"},
            )
        assert resp.status_code == 200
        events = store.get_task_timeline(body["task_id"])
        assert events[0].node_id == "body-wins"

    # ----- task_id validation -----

    def test_ingest_rejects_missing_task_id(self, tmp_path):
        store = self._mock_store(tmp_path)
        body = _ingest_body()
        body["task_id"] = ""
        with patch("llmesh.mcp.server._timeline", store):
            resp = client.post("/timeline/ingest", json=body)
        assert resp.status_code == 422
        assert "missing_task_id" in resp.json()["detail"]

    def test_ingest_rejects_non_uuid_task_id(self, tmp_path):
        store = self._mock_store(tmp_path)
        body = _ingest_body(task_id="not-a-uuid")
        with patch("llmesh.mcp.server._timeline", store):
            resp = client.post("/timeline/ingest", json=body)
        assert resp.status_code == 422
        assert "invalid_task_id_uuid4" in resp.json()["detail"]

    def test_ingest_rejects_uuid_v1_task_id(self, tmp_path):
        """UUID v1 (timestamp ベース) は拒否。v4 のみ受け入れる."""
        store = self._mock_store(tmp_path)
        body = _ingest_body(task_id=str(uuid.uuid1()))
        with patch("llmesh.mcp.server._timeline", store):
            resp = client.post("/timeline/ingest", json=body)
        assert resp.status_code == 422

    # ----- event_type validation -----

    def test_ingest_rejects_unknown_event_type(self, tmp_path):
        store = self._mock_store(tmp_path)
        body = _ingest_body(event_type="completed")  # 内部 event, ingest 不可
        with patch("llmesh.mcp.server._timeline", store):
            resp = client.post("/timeline/ingest", json=body)
        assert resp.status_code == 422
        assert "unknown_event_type" in resp.json()["detail"]

    def test_ingest_rejects_empty_event_type(self, tmp_path):
        store = self._mock_store(tmp_path)
        body = _ingest_body(event_type="")
        with patch("llmesh.mcp.server._timeline", store):
            resp = client.post("/timeline/ingest", json=body)
        assert resp.status_code == 422

    # ----- metadata validation -----

    def test_ingest_rejects_non_object_metadata(self, tmp_path):
        store = self._mock_store(tmp_path)
        body = _ingest_body(metadata=[1, 2, 3])  # type: ignore[arg-type]
        with patch("llmesh.mcp.server._timeline", store):
            resp = client.post("/timeline/ingest", json=body)
        assert resp.status_code == 422
        assert "metadata_must_be_object" in resp.json()["detail"]

    def test_ingest_rejects_reserved_metadata_key(self, tmp_path):
        """metadata に予約キー (task_id 等) を入れると 422."""
        store = self._mock_store(tmp_path)
        body = _ingest_body(metadata={"task_id": "shadow", "bwt": 0.0})
        with patch("llmesh.mcp.server._timeline", store):
            resp = client.post("/timeline/ingest", json=body)
        assert resp.status_code == 422
        assert "reserved_metadata_key" in resp.json()["detail"]

    def test_ingest_empty_metadata_ok(self, tmp_path):
        """metadata = {} は valid (一部 event は metadata 不要)."""
        store = self._mock_store(tmp_path)
        body = _ingest_body(metadata={})
        with patch("llmesh.mcp.server._timeline", store):
            resp = client.post("/timeline/ingest", json=body)
        assert resp.status_code == 200

    # ----- node_id validation -----

    def test_ingest_rejects_long_x_node_id_header(self, tmp_path):
        store = self._mock_store(tmp_path)
        body = _ingest_body()
        with patch("llmesh.mcp.server._timeline", store):
            resp = client.post(
                "/timeline/ingest",
                json=body,
                headers={"X-Node-Id": "x" * 200},
            )
        assert resp.status_code == 400
        assert resp.json()["detail"] == "node_id_too_long"

    def test_ingest_rejects_long_body_node_id(self, tmp_path):
        store = self._mock_store(tmp_path)
        body = _ingest_body(node_id="x" * 200)
        with patch("llmesh.mcp.server._timeline", store):
            resp = client.post("/timeline/ingest", json=body)
        assert resp.status_code == 400
        assert resp.json()["detail"] == "node_id_too_long"

    # ----- body / content-type validation -----

    def test_ingest_rejects_non_json_body(self, tmp_path):
        store = self._mock_store(tmp_path)
        with patch("llmesh.mcp.server._timeline", store):
            resp = client.post(
                "/timeline/ingest",
                content=b"not json",
                headers={"Content-Type": "application/json"},
            )
        assert resp.status_code == 400
        assert resp.json()["detail"] == "json_parse_error"

    def test_ingest_rejects_array_top_level(self, tmp_path):
        store = self._mock_store(tmp_path)
        with patch("llmesh.mcp.server._timeline", store):
            resp = client.post("/timeline/ingest", json=["not", "an", "object"])
        assert resp.status_code == 400
        assert resp.json()["detail"] == "request_must_be_object"

    def test_ingest_rejects_non_json_content_type(self, tmp_path):
        """既存の _json_only middleware が POST には application/json を要求."""
        store = self._mock_store(tmp_path)
        with patch("llmesh.mcp.server._timeline", store):
            resp = client.post(
                "/timeline/ingest",
                content=b'{"task_id":"x"}',
                headers={"Content-Type": "text/plain"},
            )
        assert resp.status_code == 415
