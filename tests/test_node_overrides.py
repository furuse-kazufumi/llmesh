"""Tests for NodeOverrides — blocking, pinning, and persistence."""
import json
import time
from pathlib import Path

import pytest

from llmesh.routing.node_overrides import NodeOverrides


# ---------------------------------------------------------------------------
# Blocking
# ---------------------------------------------------------------------------

class TestBlocking:
    def test_block_makes_node_blocked(self):
        ov = NodeOverrides()
        ov.block("peer:aaa")
        assert ov.is_blocked("peer:aaa")

    def test_unblock_removes_block(self):
        ov = NodeOverrides()
        ov.block("peer:aaa")
        ov.unblock("peer:aaa")
        assert not ov.is_blocked("peer:aaa")

    def test_unblock_nonexistent_is_noop(self):
        ov = NodeOverrides()
        ov.unblock("peer:not_there")  # must not raise

    def test_block_stores_reason(self):
        ov = NodeOverrides()
        ov.block("peer:aaa", reason="too slow")
        meta = ov.blocked_nodes()["peer:aaa"]
        assert meta["reason"] == "too slow"

    def test_block_stores_timestamp(self):
        before = time.time()
        ov = NodeOverrides()
        ov.block("peer:aaa")
        after = time.time()
        ts = ov.blocked_nodes()["peer:aaa"]["blocked_at"]
        assert before <= ts <= after

    def test_blocked_nodes_snapshot_is_copy(self):
        ov = NodeOverrides()
        ov.block("peer:aaa")
        snap = ov.blocked_nodes()
        snap["peer:aaa"]["reason"] = "mutated"
        # internal state unchanged
        assert ov.blocked_nodes()["peer:aaa"]["reason"] == ""

    def test_block_multiple(self):
        ov = NodeOverrides()
        ov.block("peer:a")
        ov.block("peer:b")
        assert ov.is_blocked("peer:a")
        assert ov.is_blocked("peer:b")

    def test_unknown_node_not_blocked(self):
        ov = NodeOverrides()
        assert not ov.is_blocked("peer:unknown")


# ---------------------------------------------------------------------------
# Pinning
# ---------------------------------------------------------------------------

class TestPinning:
    def test_pin_makes_node_pinned(self):
        ov = NodeOverrides()
        ov.pin("peer:aaa")
        assert ov.is_pinned("peer:aaa")

    def test_unpin_removes_pin(self):
        ov = NodeOverrides()
        ov.pin("peer:aaa")
        ov.unpin("peer:aaa")
        assert not ov.is_pinned("peer:aaa")

    def test_unpin_nonexistent_is_noop(self):
        ov = NodeOverrides()
        ov.unpin("peer:not_there")

    def test_pin_stores_label(self):
        ov = NodeOverrides()
        ov.pin("peer:aaa", label="my trusted node")
        meta = ov.pinned_nodes()["peer:aaa"]
        assert meta["label"] == "my trusted node"

    def test_pin_stores_timestamp(self):
        before = time.time()
        ov = NodeOverrides()
        ov.pin("peer:aaa")
        after = time.time()
        ts = ov.pinned_nodes()["peer:aaa"]["pinned_at"]
        assert before <= ts <= after

    def test_pinned_nodes_snapshot_is_copy(self):
        ov = NodeOverrides()
        ov.pin("peer:aaa", label="orig")
        snap = ov.pinned_nodes()
        snap["peer:aaa"]["label"] = "mutated"
        assert ov.pinned_nodes()["peer:aaa"]["label"] == "orig"

    def test_unknown_node_not_pinned(self):
        ov = NodeOverrides()
        assert not ov.is_pinned("peer:unknown")


# ---------------------------------------------------------------------------
# Mutual exclusion: block ↔ pin
# ---------------------------------------------------------------------------

class TestMutualExclusion:
    def test_block_clears_pin(self):
        ov = NodeOverrides()
        ov.pin("peer:aaa")
        ov.block("peer:aaa")
        assert ov.is_blocked("peer:aaa")
        assert not ov.is_pinned("peer:aaa")

    def test_pin_clears_block(self):
        ov = NodeOverrides()
        ov.block("peer:aaa")
        ov.pin("peer:aaa")
        assert ov.is_pinned("peer:aaa")
        assert not ov.is_blocked("peer:aaa")


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

class TestPersistence:
    def test_save_and_reload(self, tmp_path):
        path = tmp_path / "overrides.json"
        ov = NodeOverrides(path=path)
        ov.block("peer:bad", reason="spam")
        ov.pin("peer:good", label="trusted")

        ov2 = NodeOverrides(path=path)
        assert ov2.is_blocked("peer:bad")
        assert ov2.blocked_nodes()["peer:bad"]["reason"] == "spam"
        assert ov2.is_pinned("peer:good")
        assert ov2.pinned_nodes()["peer:good"]["label"] == "trusted"

    def test_file_is_valid_json(self, tmp_path):
        path = tmp_path / "overrides.json"
        ov = NodeOverrides(path=path)
        ov.block("peer:x")
        data = json.loads(path.read_text())
        assert "blocked" in data
        assert "pinned" in data

    def test_missing_file_gives_empty_overrides(self, tmp_path):
        path = tmp_path / "nonexistent.json"
        ov = NodeOverrides(path=path)
        assert ov.blocked_nodes() == {}
        assert ov.pinned_nodes() == {}

    def test_corrupt_file_gives_empty_overrides(self, tmp_path):
        path = tmp_path / "overrides.json"
        path.write_text("not json {{")
        ov = NodeOverrides(path=path)
        assert ov.blocked_nodes() == {}

    def test_unblock_persists(self, tmp_path):
        path = tmp_path / "overrides.json"
        ov = NodeOverrides(path=path)
        ov.block("peer:x")
        ov.unblock("peer:x")
        ov2 = NodeOverrides(path=path)
        assert not ov2.is_blocked("peer:x")

    def test_no_path_does_not_raise(self):
        ov = NodeOverrides()
        ov.block("peer:a")
        ov.pin("peer:b")
        ov.unblock("peer:a")
        ov.unpin("peer:b")
