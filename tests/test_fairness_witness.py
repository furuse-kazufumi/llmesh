"""Tests for WitnessProtocol — selection, verification, quorum, ledger recording."""
import pytest
from llmesh.identity.node_id import NodeIdentity
from llmesh.fairness.receipt import ServiceReceipt
from llmesh.fairness.ledger import ContributionLedger
from llmesh.fairness.witness import WitnessProtocol

HMAC_KEY = b"test-witness-hmac-key-32bytes-pa"
SERVER_ID = "peer:server"
CLIENT_ID = "peer:client"


def _make_receipt(server_id=SERVER_ID, client_id=CLIENT_ID) -> tuple[ServiceReceipt, NodeIdentity]:
    identity = NodeIdentity.generate()
    receipt = ServiceReceipt.create(server_id, client_id, "llm_infer", "task-001", identity)
    return receipt, identity


def _protocol_with_witnesses(n: int = 4, quorum: int = 2) -> WitnessProtocol:
    wp = WitnessProtocol(quorum=quorum, max_sample=3)
    for i in range(n):
        nid = NodeIdentity.generate()
        wp.register_node(f"peer:w{i}", nid.public_key_hex)
    return wp


class TestConstruction:
    def test_quorum_zero_raises(self):
        with pytest.raises(ValueError, match="quorum"):
            WitnessProtocol(quorum=0)

    def test_max_sample_lt_quorum_raises(self):
        with pytest.raises(ValueError, match="max_sample"):
            WitnessProtocol(quorum=3, max_sample=2)


class TestNodeRegistry:
    def test_register_and_count(self):
        wp = WitnessProtocol()
        assert wp.registered_count() == 0
        nid = NodeIdentity.generate()
        wp.register_node("peer:w1", nid.public_key_hex)
        assert wp.registered_count() == 1

    def test_duplicate_register_no_double_count(self):
        wp = WitnessProtocol()
        nid = NodeIdentity.generate()
        wp.register_node("peer:w1", nid.public_key_hex)
        wp.register_node("peer:w1", nid.public_key_hex)
        assert wp.registered_count() == 1


class TestSelectWitnesses:
    def test_excludes_named_nodes(self):
        wp = _protocol_with_witnesses(n=4)
        selected = wp.select_witnesses(exclude=["peer:w0", "peer:w1"])
        assert "peer:w0" not in selected
        assert "peer:w1" not in selected

    def test_returns_at_most_max_sample(self):
        wp = _protocol_with_witnesses(n=10)
        selected = wp.select_witnesses()
        assert len(selected) <= 3

    def test_empty_pool_returns_empty(self):
        wp = WitnessProtocol(quorum=1, max_sample=1)
        assert wp.select_witnesses() == []

    def test_pool_smaller_than_max_returns_all(self):
        wp = WitnessProtocol(quorum=1, max_sample=3)
        nid = NodeIdentity.generate()
        wp.register_node("peer:only", nid.public_key_hex)
        result = wp.select_witnesses()
        assert result == ["peer:only"]


class TestVerifyReceipt:
    def test_valid_receipt_passes_with_quorum(self):
        wp = _protocol_with_witnesses(n=4, quorum=2)
        receipt, _ = _make_receipt()
        verdict = wp.verify_receipt(receipt)
        assert verdict.valid is True
        assert verdict.confirmed >= 2

    def test_invalid_signature_fails(self):
        wp = _protocol_with_witnesses(n=4, quorum=2)
        receipt, _ = _make_receipt()
        receipt.signature = bytes(64)  # zero signature
        verdict = wp.verify_receipt(receipt)
        assert verdict.valid is False
        assert verdict.confirmed == 0

    def test_no_witnesses_fails_quorum(self):
        wp = WitnessProtocol(quorum=2, max_sample=3)
        # No nodes registered → empty witness list
        receipt, _ = _make_receipt()
        verdict = wp.verify_receipt(receipt, witnesses=[])
        assert verdict.valid is False

    def test_insufficient_quorum_fails(self):
        wp = WitnessProtocol(quorum=3, max_sample=3)
        nid = NodeIdentity.generate()
        wp.register_node("peer:only", nid.public_key_hex)
        receipt, _ = _make_receipt()
        verdict = wp.verify_receipt(receipt)
        # Only 1 witness available; quorum=3
        assert verdict.valid is False

    def test_explicit_witnesses_list(self):
        wp = WitnessProtocol(quorum=1, max_sample=3)
        receipt, _ = _make_receipt()
        verdict = wp.verify_receipt(receipt, witnesses=["peer:w1", "peer:w2"])
        assert verdict.witness_node_ids == ["peer:w1", "peer:w2"]

    def test_server_client_excluded_from_auto_witnesses(self):
        wp = WitnessProtocol(quorum=1, max_sample=3)
        nid = NodeIdentity.generate()
        wp.register_node(SERVER_ID, nid.public_key_hex)
        wp.register_node(CLIENT_ID, nid.public_key_hex)
        wp.register_node("peer:neutral", nid.public_key_hex)
        receipt, _ = _make_receipt()
        verdict = wp.verify_receipt(receipt)
        assert SERVER_ID not in verdict.witness_node_ids
        assert CLIENT_ID not in verdict.witness_node_ids

    def test_verdict_contains_task_id(self):
        wp = _protocol_with_witnesses(n=4)
        receipt, _ = _make_receipt()
        verdict = wp.verify_receipt(receipt)
        assert verdict.receipt_task_id == receipt.task_id


class TestVerifyAndRecord:
    def test_valid_receipt_recorded_to_ledger(self):
        wp = _protocol_with_witnesses(n=4, quorum=2)
        ledger = ContributionLedger(HMAC_KEY)
        receipt, _ = _make_receipt()
        verdict = wp.verify_and_record(receipt, ledger)
        assert verdict.valid is True
        assert ledger.entry_count() == 2  # served + consumed

    def test_invalid_receipt_not_recorded(self):
        wp = _protocol_with_witnesses(n=4, quorum=2)
        ledger = ContributionLedger(HMAC_KEY)
        receipt, _ = _make_receipt()
        receipt.signature = bytes(64)
        verdict = wp.verify_and_record(receipt, ledger)
        assert verdict.valid is False
        assert ledger.entry_count() == 0

    def test_ledger_chain_valid_after_record(self):
        wp = _protocol_with_witnesses(n=4, quorum=2)
        ledger = ContributionLedger(HMAC_KEY)
        receipt, _ = _make_receipt()
        wp.verify_and_record(receipt, ledger)
        assert ledger.verify_chain() is True
