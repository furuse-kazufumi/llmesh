"""Tests for ServiceReceipt — creation, signing, verification, serialisation."""
import pytest
from llmesh.identity.node_id import NodeIdentity
from llmesh.fairness.receipt import ServiceReceipt, _canonical_payload

SERVER_ID = "peer:server"
CLIENT_ID = "peer:client"
TOOL = "llm_infer"
TASK = "task-0001"
TS = 1_700_000_000.0


def _make_receipt(client_identity: NodeIdentity) -> ServiceReceipt:
    return ServiceReceipt.create(
        server_node_id=SERVER_ID,
        client_node_id=CLIENT_ID,
        tool_name=TOOL,
        task_id=TASK,
        client_identity=client_identity,
        timestamp=TS,
    )


class TestCreate:
    def test_fields_populated(self):
        identity = NodeIdentity.generate()
        r = _make_receipt(identity)
        assert r.server_node_id == SERVER_ID
        assert r.client_node_id == CLIENT_ID
        assert r.tool_name == TOOL
        assert r.task_id == TASK
        assert r.timestamp == TS
        assert r.client_pub_hex == identity.public_key_hex
        assert isinstance(r.signature, bytes) and len(r.signature) == 64

    def test_timestamp_defaults_to_now(self):
        import time
        identity = NodeIdentity.generate()
        before = time.time()
        r = ServiceReceipt.create(SERVER_ID, CLIENT_ID, TOOL, TASK, identity)
        after = time.time()
        assert before <= r.timestamp <= after


class TestVerify:
    def test_valid_signature_passes(self):
        identity = NodeIdentity.generate()
        r = _make_receipt(identity)
        assert r.verify() is True

    def test_tampered_server_id_fails(self):
        identity = NodeIdentity.generate()
        r = _make_receipt(identity)
        r.server_node_id = "peer:evil"
        assert r.verify() is False

    def test_tampered_task_id_fails(self):
        identity = NodeIdentity.generate()
        r = _make_receipt(identity)
        r.task_id = "task-evil"
        assert r.verify() is False

    def test_tampered_signature_fails(self):
        identity = NodeIdentity.generate()
        r = _make_receipt(identity)
        r.signature = bytes(64)
        assert r.verify() is False

    def test_wrong_public_key_fails(self):
        identity = NodeIdentity.generate()
        other = NodeIdentity.generate()
        r = _make_receipt(identity)
        r.client_pub_hex = other.public_key_hex
        assert r.verify() is False

    def test_different_identities_independent(self):
        a = NodeIdentity.generate()
        b = NodeIdentity.generate()
        ra = _make_receipt(a)
        rb = _make_receipt(b)
        assert ra.verify() is True
        assert rb.verify() is True


class TestSerialisation:
    def test_round_trip(self):
        identity = NodeIdentity.generate()
        r = _make_receipt(identity)
        d = r.to_dict()
        r2 = ServiceReceipt.from_dict(d)
        assert r2.server_node_id == r.server_node_id
        assert r2.client_node_id == r.client_node_id
        assert r2.tool_name == r.tool_name
        assert r2.task_id == r.task_id
        assert r2.timestamp == r.timestamp
        assert r2.client_pub_hex == r.client_pub_hex
        assert r2.signature == r.signature

    def test_round_trip_verifies(self):
        identity = NodeIdentity.generate()
        r = _make_receipt(identity)
        r2 = ServiceReceipt.from_dict(r.to_dict())
        assert r2.verify() is True

    def test_signature_serialised_as_hex(self):
        identity = NodeIdentity.generate()
        r = _make_receipt(identity)
        d = r.to_dict()
        assert isinstance(d["signature"], str)
        assert len(d["signature"]) == 128  # 64 bytes hex


class TestCanonicalPayload:
    def test_deterministic(self):
        p1 = _canonical_payload(SERVER_ID, CLIENT_ID, TOOL, TASK, TS)
        p2 = _canonical_payload(SERVER_ID, CLIENT_ID, TOOL, TASK, TS)
        assert p1 == p2

    def test_different_fields_differ(self):
        p1 = _canonical_payload(SERVER_ID, CLIENT_ID, TOOL, TASK, TS)
        p2 = _canonical_payload(SERVER_ID, CLIENT_ID, TOOL, "task-9999", TS)
        assert p1 != p2
