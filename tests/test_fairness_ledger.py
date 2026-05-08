"""Tests for ContributionLedger — recording, ratio, HMAC chain, receipt."""
import time
import pytest
from llmesh.identity.node_id import NodeIdentity
from llmesh.fairness.ledger import ContributionLedger
from llmesh.fairness.receipt import ServiceReceipt

HMAC_KEY = b"test-ledger-hmac-key-32bytes-pad"
NODE_A = "peer:nodeA"
NODE_B = "peer:nodeB"


def _ledger() -> ContributionLedger:
    return ContributionLedger(HMAC_KEY)


class TestRecording:
    def test_record_served_increments(self):
        ledger = _ledger()
        ledger.record_served(NODE_A, "t1")
        assert ledger.entry_count() == 1

    def test_record_consumed_increments(self):
        ledger = _ledger()
        ledger.record_consumed(NODE_A, "t1")
        assert ledger.entry_count() == 1

    def test_both_recorded_independently(self):
        ledger = _ledger()
        ledger.record_served(NODE_A, "t1")
        ledger.record_consumed(NODE_B, "t2")
        assert ledger.entry_count() == 2


class TestRatio:
    def test_no_events_returns_neutral(self):
        ledger = _ledger()
        assert ledger.get_ratio(NODE_A) == 1.0

    def test_only_served_returns_neutral(self):
        ledger = _ledger()
        ledger.record_served(NODE_A, "t1")
        assert ledger.get_ratio(NODE_A) == 1.0

    def test_served_equals_consumed_is_one(self):
        ledger = _ledger()
        ledger.record_served(NODE_A, "t1")
        ledger.record_consumed(NODE_A, "t2")
        assert ledger.get_ratio(NODE_A) == pytest.approx(1.0)

    def test_twice_consumed_ratio_half(self):
        ledger = _ledger()
        ledger.record_served(NODE_A, "t1")
        ledger.record_consumed(NODE_A, "t2")
        ledger.record_consumed(NODE_A, "t3")
        assert ledger.get_ratio(NODE_A) == pytest.approx(0.5)

    def test_zero_served_ratio_zero(self):
        ledger = _ledger()
        ledger.record_consumed(NODE_A, "t1")
        assert ledger.get_ratio(NODE_A) == pytest.approx(0.0)

    def test_nodes_independent(self):
        ledger = _ledger()
        ledger.record_served(NODE_A, "t1")
        ledger.record_consumed(NODE_B, "t2")
        ledger.record_consumed(NODE_B, "t3")
        assert ledger.get_ratio(NODE_A) == 1.0
        assert ledger.get_ratio(NODE_B) == pytest.approx(0.0)

    def test_time_window_filters_old_events(self):
        ledger = _ledger()
        old_ts = time.time() - 7200.0  # 2 hours ago (outside 1h window)
        ledger.record_served(NODE_A, "t1", timestamp=old_ts)
        ledger.record_consumed(NODE_A, "t2", timestamp=old_ts)
        ledger.record_consumed(NODE_A, "t3", timestamp=old_ts)
        # Old events excluded — ratio should be neutral (no consumed in window)
        assert ledger.get_ratio(NODE_A, window=3600.0) == 1.0

    def test_custom_window(self):
        ledger = _ledger()
        now = time.time()
        ledger.record_consumed(NODE_A, "t1", timestamp=now - 30)  # 30s ago
        assert ledger.get_ratio(NODE_A, window=60.0) == pytest.approx(0.0)
        assert ledger.get_ratio(NODE_A, window=20.0) == 1.0  # excluded by 20s window


class TestHmacChain:
    def test_empty_chain_verifies(self):
        ledger = _ledger()
        assert ledger.verify_chain() is True

    def test_chain_verifies_after_records(self):
        ledger = _ledger()
        for i in range(5):
            ledger.record_served(NODE_A, f"t{i}")
        assert ledger.verify_chain() is True

    def test_tampered_entry_detected(self):
        ledger = _ledger()
        ledger.record_served(NODE_A, "t1")
        # Tamper directly via per-node deque
        dq = ledger._by_node[NODE_A]
        dq[0] = dq[0]._replace(node_id="peer:evil")
        assert ledger.verify_chain(node_id=NODE_A) is False

    def test_wrong_key_detected(self):
        ledger = _ledger()
        ledger.record_served(NODE_A, "t1")
        bad = ContributionLedger(b"wrong-key-32bytes-padded-here--")
        # Copy the deque and compaction root to the bad-key ledger
        bad._node_lock(NODE_A)
        bad._by_node[NODE_A] = ledger._by_node[NODE_A]
        assert bad.verify_chain(node_id=NODE_A) is False


class TestRecordReceipt:
    def test_records_both_sides(self):
        ledger = _ledger()
        identity = NodeIdentity.generate()
        receipt = ServiceReceipt.create("peer:server", "peer:client", "llm", "t1", identity)
        ledger.record_receipt(receipt)
        assert ledger.entry_count() == 2

    def test_chain_valid_after_receipt(self):
        ledger = _ledger()
        identity = NodeIdentity.generate()
        receipt = ServiceReceipt.create("peer:server", "peer:client", "llm", "t1", identity)
        ledger.record_receipt(receipt)
        assert ledger.verify_chain() is True

    def test_server_served_client_consumed(self):
        ledger = _ledger()
        identity = NodeIdentity.generate()
        receipt = ServiceReceipt.create("peer:server", "peer:client", "llm", "t1", identity)
        ledger.record_receipt(receipt)
        # server ratio: 1 served / 0 consumed → neutral (1.0)
        assert ledger.get_ratio("peer:server") == 1.0
        # client ratio: 0 served / 1 consumed → 0.0
        assert ledger.get_ratio("peer:client") == pytest.approx(0.0)


class TestCompaction:
    def test_compact_removes_old_entries(self):
        ledger = ContributionLedger(HMAC_KEY)
        old_ts = time.time() - 7200.0
        ledger.record_served(NODE_A, "t1", timestamp=old_ts)
        ledger.record_served(NODE_A, "t2", timestamp=old_ts)
        ledger.record_served(NODE_A, "t3")  # recent
        removed = ledger.compact(max_age_seconds=3600.0)
        assert removed == 2
        assert ledger.entry_count() == 1

    def test_compact_returns_zero_if_nothing_to_remove(self):
        ledger = ContributionLedger(HMAC_KEY)
        ledger.record_served(NODE_A, "t1")  # recent
        assert ledger.compact(max_age_seconds=3600.0) == 0
        assert ledger.entry_count() == 1

    def test_compact_all_entries(self):
        ledger = ContributionLedger(HMAC_KEY)
        old_ts = time.time() - 7200.0
        ledger.record_served(NODE_A, "t1", timestamp=old_ts)
        ledger.record_served(NODE_A, "t2", timestamp=old_ts)
        removed = ledger.compact(max_age_seconds=3600.0)
        assert removed == 2
        assert ledger.entry_count() == 0

    def test_chain_valid_after_compaction(self):
        ledger = ContributionLedger(HMAC_KEY)
        old_ts = time.time() - 7200.0
        for i in range(5):
            ledger.record_served(NODE_A, f"old-{i}", timestamp=old_ts)
        for i in range(3):
            ledger.record_served(NODE_A, f"new-{i}")
        ledger.compact(max_age_seconds=3600.0)
        assert ledger.verify_chain() is True

    def test_ratio_correct_after_compaction(self):
        ledger = ContributionLedger(HMAC_KEY)
        old_ts = time.time() - 7200.0
        # old events outside the 1h window
        for _ in range(10):
            ledger.record_consumed(NODE_A, "old", timestamp=old_ts)
        ledger.compact(max_age_seconds=3600.0)
        # After compaction, no consumed events in window → neutral
        assert ledger.get_ratio(NODE_A) == 1.0

    def test_auto_compact_keeps_entries_bounded(self):
        max_e = 20
        ledger = ContributionLedger(HMAC_KEY, default_window=3600.0, max_entries_per_node=max_e)
        # Use 7300s (> default_window*2=7200s) so entries are strictly below cutoff
        old_ts = time.time() - 7300.0
        # Fill with old entries that will be compacted
        for i in range(30):
            ledger.record_served(NODE_A, f"t{i}", timestamp=old_ts)
        # Entry count should not exceed max_e + 1 (one new entry triggers compact)
        assert ledger.entry_count() <= max_e + 1

    def test_auto_compact_disabled_when_zero(self):
        ledger = ContributionLedger(HMAC_KEY, max_entries_per_node=0)
        old_ts = time.time() - 7200.0
        for i in range(100):
            ledger.record_served(NODE_A, f"t{i}", timestamp=old_ts)
        assert ledger.entry_count() == 100

    def test_chain_valid_after_new_entries_post_compaction(self):
        ledger = ContributionLedger(HMAC_KEY)
        old_ts = time.time() - 7200.0
        ledger.record_served(NODE_A, "old", timestamp=old_ts)
        ledger.compact(max_age_seconds=3600.0)
        # Add new entries after compaction
        ledger.record_served(NODE_A, "new1")
        ledger.record_consumed(NODE_B, "new2")
        assert ledger.verify_chain() is True
