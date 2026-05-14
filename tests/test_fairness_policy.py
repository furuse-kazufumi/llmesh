"""Tests for FairnessPolicy — penalty levels, enable/disable opt-out, escalation."""
from llmesh.fairness.ledger import ContributionLedger
from llmesh.fairness.policy import FairnessPolicy, FairnessPolicyConfig, PenaltyLevel

HMAC_KEY = b"test-policy-hmac-key-32bytes-pad"
NODE = "peer:node1"


def _setup(served: int = 0, consumed: int = 0) -> tuple[ContributionLedger, FairnessPolicy]:
    ledger = ContributionLedger(HMAC_KEY)
    for i in range(served):
        ledger.record_served(NODE, f"t-s{i}")
    for i in range(consumed):
        ledger.record_consumed(NODE, f"t-c{i}")
    policy = FairnessPolicy(ledger)
    return ledger, policy


class TestPenaltyLevels:
    def test_no_history_is_normal(self):
        _, policy = _setup()
        assert policy.evaluate(NODE) == PenaltyLevel.NORMAL

    def test_equal_served_consumed_is_normal(self):
        _, policy = _setup(served=5, consumed=5)
        assert policy.evaluate(NODE) == PenaltyLevel.NORMAL

    def test_ratio_04_is_low_priority(self):
        _, policy = _setup(served=2, consumed=5)
        # ratio = 0.4
        assert policy.evaluate(NODE) == PenaltyLevel.LOW_PRIORITY

    def test_ratio_02_is_rate_limited(self):
        _, policy = _setup(served=1, consumed=5)
        # ratio = 0.2
        assert policy.evaluate(NODE) == PenaltyLevel.RATE_LIMITED

    def test_ratio_zero_is_blocked(self):
        _, policy = _setup(served=0, consumed=5)
        # ratio = 0.0
        assert policy.evaluate(NODE) == PenaltyLevel.BLOCKED

    def test_custom_thresholds(self):
        cfg = FairnessPolicyConfig(
            normal_threshold=0.8,
            low_priority_threshold=0.5,
            rate_limited_threshold=0.2,
        )
        ledger = ContributionLedger(HMAC_KEY)
        ledger.record_served(NODE, "t1")
        ledger.record_consumed(NODE, "t2")
        ledger.record_consumed(NODE, "t3")
        # ratio = 0.5, threshold for low_priority is 0.5 → LOW_PRIORITY (< normal=0.8)
        policy = FairnessPolicy(ledger, cfg)
        assert policy.evaluate(NODE) == PenaltyLevel.LOW_PRIORITY


class TestIsAllowed:
    def test_normal_is_allowed(self):
        _, policy = _setup()
        assert policy.is_allowed(NODE) is True

    def test_low_priority_is_allowed(self):
        _, policy = _setup(served=2, consumed=5)
        assert policy.is_allowed(NODE) is True

    def test_rate_limited_is_allowed(self):
        _, policy = _setup(served=1, consumed=5)
        assert policy.is_allowed(NODE) is True

    def test_blocked_not_allowed(self):
        _, policy = _setup(served=0, consumed=5)
        assert policy.is_allowed(NODE) is False


class TestQueuePriority:
    def test_normal_priority_3(self):
        _, policy = _setup()
        assert policy.get_queue_priority(NODE) == 3

    def test_low_priority_is_2(self):
        _, policy = _setup(served=2, consumed=5)
        assert policy.get_queue_priority(NODE) == 2

    def test_rate_limited_is_1(self):
        _, policy = _setup(served=1, consumed=5)
        assert policy.get_queue_priority(NODE) == 1

    def test_blocked_is_0(self):
        _, policy = _setup(served=0, consumed=5)
        assert policy.get_queue_priority(NODE) == 0


class TestEscalationToExcluded:
    def test_repeated_blocked_leads_to_excluded(self):
        cfg = FairnessPolicyConfig(exclude_after=3)
        ledger = ContributionLedger(HMAC_KEY)
        ledger.record_consumed(NODE, "t1")  # ratio = 0
        policy = FairnessPolicy(ledger, cfg)
        # First 2 → BLOCKED; 3rd → EXCLUDED
        policy.evaluate(NODE)
        policy.evaluate(NODE)
        assert policy.evaluate(NODE) == PenaltyLevel.EXCLUDED

    def test_excluded_persists(self):
        cfg = FairnessPolicyConfig(exclude_after=2)
        ledger = ContributionLedger(HMAC_KEY)
        ledger.record_consumed(NODE, "t1")
        policy = FairnessPolicy(ledger, cfg)
        policy.evaluate(NODE)
        policy.evaluate(NODE)  # triggers exclusion
        # Even if ratio improves, excluded stays
        ledger.record_served(NODE, "t2")
        ledger.record_served(NODE, "t3")
        assert policy.evaluate(NODE) == PenaltyLevel.EXCLUDED


class TestManualOverride:
    def test_exclude_forces_excluded(self):
        _, policy = _setup()  # ratio = 1.0 (neutral)
        policy.exclude(NODE)
        assert policy.evaluate(NODE) == PenaltyLevel.EXCLUDED

    def test_pardon_restores_normal(self):
        _, policy = _setup()
        policy.exclude(NODE)
        policy.pardon(NODE)
        assert policy.evaluate(NODE) == PenaltyLevel.NORMAL

    def test_pardon_resets_blocked_count(self):
        cfg = FairnessPolicyConfig(exclude_after=3)
        ledger = ContributionLedger(HMAC_KEY)
        ledger.record_consumed(NODE, "t1")
        policy = FairnessPolicy(ledger, cfg)
        policy.evaluate(NODE)
        policy.evaluate(NODE)
        policy.pardon(NODE)
        # After pardon, block count resets — need 3 more to escalate
        policy.evaluate(NODE)
        policy.evaluate(NODE)
        assert policy.evaluate(NODE) == PenaltyLevel.EXCLUDED


class TestEnableDisable:
    def test_disabled_always_normal(self):
        _, policy = _setup(served=0, consumed=10)  # ratio=0 → normally BLOCKED
        policy.disable()
        assert policy.evaluate(NODE) == PenaltyLevel.NORMAL

    def test_disabled_is_allowed(self):
        _, policy = _setup(served=0, consumed=10)
        policy.disable()
        assert policy.is_allowed(NODE) is True

    def test_disabled_priority_3(self):
        _, policy = _setup(served=0, consumed=10)
        policy.disable()
        assert policy.get_queue_priority(NODE) == 3

    def test_enable_restores_enforcement(self):
        _, policy = _setup(served=0, consumed=10)
        policy.disable()
        policy.enable()
        assert policy.evaluate(NODE) == PenaltyLevel.BLOCKED

    def test_constructed_disabled(self):
        ledger = ContributionLedger(HMAC_KEY)
        ledger.record_consumed(NODE, "t1")
        policy = FairnessPolicy(ledger, enabled=False)
        assert policy.evaluate(NODE) == PenaltyLevel.NORMAL
        assert policy.is_enabled is False

    def test_is_enabled_reflects_state(self):
        _, policy = _setup()
        assert policy.is_enabled is True
        policy.disable()
        assert policy.is_enabled is False
        policy.enable()
        assert policy.is_enabled is True

    def test_excluded_while_disabled_still_excluded_after_enable(self):
        _, policy = _setup()
        policy.exclude(NODE)
        policy.disable()
        assert policy.evaluate(NODE) == PenaltyLevel.NORMAL  # disabled
        policy.enable()
        assert policy.evaluate(NODE) == PenaltyLevel.EXCLUDED  # exclusion persists


class TestExcludedSizeCap:
    def test_excluded_cap_evicts_oldest(self):
        cfg = FairnessPolicyConfig(max_excluded_size=3)
        ledger = ContributionLedger(HMAC_KEY)
        policy = FairnessPolicy(ledger, cfg)
        # Manually exclude 4 nodes — cap is 3, so first one gets evicted
        policy.exclude("peer:a")
        policy.exclude("peer:b")
        policy.exclude("peer:c")
        policy.exclude("peer:d")  # peer:a should be evicted
        assert policy.excluded_count() == 3
        # peer:a was evicted (oldest)
        assert policy.evaluate("peer:a") != PenaltyLevel.EXCLUDED
        # peer:d is still excluded
        assert policy.evaluate("peer:d") == PenaltyLevel.EXCLUDED

    def test_excluded_cap_zero_is_unbounded(self):
        cfg = FairnessPolicyConfig(max_excluded_size=0)
        ledger = ContributionLedger(HMAC_KEY)
        policy = FairnessPolicy(ledger, cfg)
        for i in range(100):
            policy.exclude(f"peer:node{i}")
        assert policy.excluded_count() == 100

    def test_excluded_count_method(self):
        ledger = ContributionLedger(HMAC_KEY)
        policy = FairnessPolicy(ledger)
        assert policy.excluded_count() == 0
        policy.exclude("peer:x")
        assert policy.excluded_count() == 1
        policy.pardon("peer:x")
        assert policy.excluded_count() == 0
