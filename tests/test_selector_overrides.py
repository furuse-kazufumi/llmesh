"""Tests for SmartNodeSelector with NodeOverrides integration."""
from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest

from llmesh.routing.node_overrides import NodeOverrides
from llmesh.routing.selector import SmartNodeSelector


@dataclass
class _Node:
    node_id: str
    endpoint: str = "http://localhost:8000"


def _make_nodes(*ids: str) -> list[_Node]:
    return [_Node(node_id=nid) for nid in ids]


def _selector(overrides: NodeOverrides | None = None) -> SmartNodeSelector:
    return SmartNodeSelector(overrides=overrides)


# ---------------------------------------------------------------------------
# Blocked nodes are always excluded
# ---------------------------------------------------------------------------

class TestBlockFilter:
    def test_blocked_node_not_selected(self):
        ov = NodeOverrides()
        ov.block("peer:bad")
        sel = _selector(ov)
        nodes = _make_nodes("peer:bad", "peer:good")
        result = sel.select(nodes, k=1)
        ids = [n.node_id for n in result]
        assert "peer:bad" not in ids
        assert "peer:good" in ids

    def test_all_blocked_returns_empty(self):
        ov = NodeOverrides()
        ov.block("peer:a")
        ov.block("peer:b")
        sel = _selector(ov)
        result = sel.select(_make_nodes("peer:a", "peer:b"), k=1)
        assert result == []

    def test_no_overrides_all_pass(self):
        sel = _selector(None)
        nodes = _make_nodes("peer:a", "peer:b")
        result = sel.select(nodes, k=1)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# Pinned nodes bypass fairness and sort first
# ---------------------------------------------------------------------------

class TestPinSorting:
    def test_pinned_node_sorted_first(self):
        ov = NodeOverrides()
        ov.pin("peer:trusted")
        sel = _selector(ov)
        nodes = _make_nodes("peer:a", "peer:b", "peer:trusted")
        result = sel.select(nodes, k=1)
        assert result[0].node_id == "peer:trusted"

    def test_pinned_bypasses_fairness_block(self):
        ov = NodeOverrides()
        ov.pin("peer:trusted")

        fairness = MagicMock()
        # fairness says the pinned node is NOT allowed
        fairness.is_allowed.side_effect = lambda nid, *a, **kw: nid != "peer:trusted"

        sel = SmartNodeSelector(fairness_policy=fairness, overrides=ov)
        nodes = _make_nodes("peer:trusted", "peer:normal")
        result = sel.select(nodes, k=1)
        ids = [n.node_id for n in result]
        assert "peer:trusted" in ids   # pinned → fairness bypassed

    def test_unpinned_node_subject_to_fairness(self):
        ov = NodeOverrides()
        fairness = MagicMock()
        # fairness blocks peer:bad
        fairness.is_allowed.side_effect = lambda nid, *a, **kw: nid != "peer:bad"

        sel = SmartNodeSelector(fairness_policy=fairness, overrides=ov)
        nodes = _make_nodes("peer:bad", "peer:good")
        result = sel.select(nodes, k=1)
        ids = [n.node_id for n in result]
        assert "peer:bad" not in ids
        assert "peer:good" in ids

    def test_multiple_pinned_sorted_before_unpinned(self):
        ov = NodeOverrides()
        ov.pin("peer:p1")
        ov.pin("peer:p2")
        # candidate_multiplier=10 ensures all 4 nodes survive trimming
        sel = SmartNodeSelector(candidate_multiplier=10, overrides=ov)
        nodes = _make_nodes("peer:u1", "peer:p1", "peer:u2", "peer:p2")
        result = sel.select(nodes, k=1)
        ids = [n.node_id for n in result]
        # pinned nodes must occupy the first two positions
        assert set(ids[:2]) == {"peer:p1", "peer:p2"}
        assert set(ids[2:]) == {"peer:u1", "peer:u2"}


# ---------------------------------------------------------------------------
# candidate_multiplier still applies
# ---------------------------------------------------------------------------

class TestCandidateMultiplier:
    def test_trim_respects_multiplier(self):
        sel = SmartNodeSelector(candidate_multiplier=2)
        nodes = _make_nodes("a", "b", "c", "d", "e")
        result = sel.select(nodes, k=2)
        assert len(result) == 4   # k=2 * multiplier=2

    def test_blocked_reduces_pool_before_trim(self):
        ov = NodeOverrides()
        ov.block("peer:x")
        sel = SmartNodeSelector(candidate_multiplier=2, overrides=ov)
        nodes = _make_nodes("peer:x", "peer:a", "peer:b", "peer:c")
        result = sel.select(nodes, k=2)
        assert len(result) == 3   # 3 surviving nodes, limited to k*multi=4 but only 3 available
        assert all(n.node_id != "peer:x" for n in result)


# ---------------------------------------------------------------------------
# overrides property accessor
# ---------------------------------------------------------------------------

class TestAccessors:
    def test_overrides_property(self):
        ov = NodeOverrides()
        sel = _selector(ov)
        assert sel.overrides is ov

    def test_no_overrides_property_is_none(self):
        sel = _selector(None)
        assert sel.overrides is None
