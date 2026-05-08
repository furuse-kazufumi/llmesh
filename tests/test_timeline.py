"""Tests for TimelineStore and server timeline endpoints."""
from __future__ import annotations

import time
import uuid
import pytest
from llmesh.timeline.store import TimelineStore, TimelineEvent


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

from unittest.mock import patch, MagicMock
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
