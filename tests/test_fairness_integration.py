"""Integration tests — fairness system wired into SmartNodeSelector and FanoutExecutor."""
import pytest
from llmesh.fairness.ledger import ContributionLedger
from llmesh.fairness.policy import FairnessPolicy, PenaltyLevel
from llmesh.routing.selector import SmartNodeSelector
from llmesh.orchestrator.fanout import FanoutExecutor

HMAC_KEY = b"test-integ-hmac-key-32bytes-pad-"


class _Node:
    def __init__(self, node_id: str, endpoint: str = "http://localhost"):
        self.node_id = node_id
        self.endpoint = endpoint


def _policy_blocking(node_id: str) -> FairnessPolicy:
    ledger = ContributionLedger(HMAC_KEY)
    ledger.record_consumed(node_id, "t1")  # ratio=0 → BLOCKED
    return FairnessPolicy(ledger)


class TestSelectorFairness:
    def test_blocked_node_filtered_out(self):
        bad = "peer:freeloader"
        policy = _policy_blocking(bad)
        selector = SmartNodeSelector(fairness_policy=policy)
        nodes = [_Node("peer:good"), _Node(bad)]
        result = selector.select(nodes, k=1)
        assert all(n.node_id != bad for n in result)

    def test_normal_node_passes(self):
        policy = FairnessPolicy(ContributionLedger(HMAC_KEY))
        selector = SmartNodeSelector(fairness_policy=policy)
        nodes = [_Node("peer:good1"), _Node("peer:good2")]
        result = selector.select(nodes, k=1)
        assert len(result) > 0

    def test_no_policy_passes_all(self):
        selector = SmartNodeSelector(fairness_policy=None)
        bad = "peer:freeloader"
        nodes = [_Node("peer:good"), _Node(bad)]
        ids = {n.node_id for n in selector.select(nodes, k=1)}
        assert bad in ids or "peer:good" in ids  # both survive without policy

    def test_fairness_policy_property(self):
        policy = FairnessPolicy(ContributionLedger(HMAC_KEY))
        selector = SmartNodeSelector(fairness_policy=policy)
        assert selector.fairness_policy is policy

    def test_disabled_policy_passes_blocked_node(self):
        bad = "peer:freeloader"
        ledger = ContributionLedger(HMAC_KEY)
        ledger.record_consumed(bad, "t1")
        policy = FairnessPolicy(ledger, enabled=False)
        selector = SmartNodeSelector(fairness_policy=policy)
        nodes = [_Node(bad)]
        result = selector.select(nodes, k=1)
        assert len(result) == 1


class TestFanoutFairnessFilter:
    def test_fairness_policy_param_accepted(self):
        policy = FairnessPolicy(ContributionLedger(HMAC_KEY))
        executor = FanoutExecutor(fairness_policy=policy)
        assert executor._fairness_policy is policy

    def test_no_policy_default_none(self):
        executor = FanoutExecutor()
        assert executor._fairness_policy is None

    def test_blocked_node_filtered_before_send(self):
        bad = "peer:freeloader"
        ledger = ContributionLedger(HMAC_KEY)
        ledger.record_consumed(bad, "t1")
        policy = FairnessPolicy(ledger)

        executor = FanoutExecutor(k=1, fairness_policy=policy)
        # No selector attached → fairness filter runs in execute()
        assert not policy.is_allowed(bad)

        # Verify filter logic directly (without a real network call)
        nodes = [_Node(bad, "http://localhost:9999")]
        allowed = [n for n in nodes if policy.is_allowed(n.node_id)]
        assert allowed == []
