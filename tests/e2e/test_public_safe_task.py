"""E2E Scenario 1: L0 public-safe task end-to-end.

Pipeline: PromptFirewall → ClassifiedPayload(L0/L1) → OutputValidator
          → LocalSynthesizer, with NonceStore replay check and AuditTrace
          entry recording.

No real network calls — all LLM responses are mocked.
"""
from __future__ import annotations

import hashlib
import json
import uuid

import pytest

from llmesh.audit import AuditTrace
from llmesh.classifier.data_level import DataLevel
from llmesh.mcp.nonce_store import NonceStore
from llmesh.mcp.validator import OutputValidator, ValidationError
from llmesh.orchestrator import LocalSynthesizer
from llmesh.privacy.firewall import PromptFirewall

# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------

HMAC_KEY = b"e2e-test-hmac-key-32-bytes-pad!!"
TASK_ID = "12345678-1234-4234-89ab-123456789abc"
NODE_ID = "peer:test-node-e2e"
NONCE = "a" * 32
SHA256 = "b" * 64


def _make_generate_code_response(task_id: str = TASK_ID, nonce: str = NONCE) -> dict:
    return {
        "task_id": task_id,
        "code": "def hello(): return 'hello'",
        "language": "python",
        "explanation": "simple hello function",
        "dependencies_added": [],
        "generated_files": [],
        "cve_scan_requested": False,
        "caller_nonce_echo": nonce,
    }


# -----------------------------------------------------------------------
# Scenario 1: L0 public-safe task end-to-end
# -----------------------------------------------------------------------

class TestPublicSafeTaskE2E:
    def test_l0_task_full_pipeline(self, tmp_path):
        """Happy path: L0 prompt flows through all layers successfully."""
        # 1. Firewall classifies the prompt
        firewall = PromptFirewall()
        prompt = "Write a Python function that returns 'hello world'."
        decision = firewall.classify(prompt)
        assert not decision.blocked
        assert decision.level in (DataLevel.L0, DataLevel.L1)

        payload = firewall.wrap(prompt)
        assert payload.level in (DataLevel.L0, DataLevel.L1)
        assert payload.policy_decision == "ALLOW"

        # 2. OutputValidator with NonceStore validates a (mocked) node response
        nonce_store = NonceStore(ttl_seconds=300)
        validator = OutputValidator(nonce_store=nonce_store)

        raw_response = json.dumps(_make_generate_code_response())
        result = validator.validate(
            raw_response,
            "generate_code",
            NONCE,
            node_id=NODE_ID,
            task_id=TASK_ID,
        )
        assert result["code"] == "def hello(): return 'hello'"

        # 3. LocalSynthesizer produces consensus
        synthesizer = LocalSynthesizer()
        consensus = synthesizer.synthesize([result], "generate_code")
        assert consensus["task_id"] == TASK_ID

        # 4. AuditTrace records the event
        log_path = tmp_path / "audit.jsonl"
        trace = AuditTrace(log_path, HMAC_KEY)
        output_sha = hashlib.sha256(
            json.dumps(consensus, sort_keys=True).encode()
        ).hexdigest()
        trace.log(
            event_type="tool_call_complete",
            node_id=NODE_ID,
            task_id=TASK_ID,
            policy_decision="ALLOW",
            output_sha256=output_sha,
            data_level=payload.level.value,
        )

        # 5. Verify audit chain
        assert AuditTrace.verify_chain(log_path, HMAC_KEY) is True
        assert log_path.exists()
        lines = log_path.read_text().strip().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["policy_decision"] == "ALLOW"
        assert entry["task_id"] == TASK_ID

    def test_replay_attack_rejected_in_pipeline(self, tmp_path):
        """Same (node_id, nonce) pair is rejected on the second call."""
        nonce_store = NonceStore(ttl_seconds=300)
        validator = OutputValidator(nonce_store=nonce_store)

        raw_response = json.dumps(_make_generate_code_response())

        # First call — accepted
        result = validator.validate(
            raw_response, "generate_code", NONCE, node_id=NODE_ID, task_id=TASK_ID
        )
        assert result is not None

        # Second call with same nonce — replay attack
        with pytest.raises(ValidationError, match="replay_attack_detected"):
            validator.validate(
                raw_response, "generate_code", NONCE, node_id=NODE_ID, task_id=TASK_ID
            )

    def test_audit_trace_records_on_each_validated_call(self, tmp_path):
        """Each validated call produces exactly one audit entry."""
        log_path = tmp_path / "audit.jsonl"
        trace = AuditTrace(log_path, HMAC_KEY)

        for i in range(3):
            # Generate fresh unique task_id and nonce for each call
            tid = str(uuid.uuid4())
            nonce = f"{i:032x}"
            trace.log(
                event_type="tool_call",
                node_id=NODE_ID,
                task_id=tid,
                policy_decision="ALLOW",
                output_sha256="a" * 64,
                data_level=0,
            )

        lines = log_path.read_text().strip().splitlines()
        assert len(lines) == 3
        assert AuditTrace.verify_chain(log_path, HMAC_KEY) is True

    def test_l1_classified_payload_allowed(self):
        """Low-risk (L1) prompts are allowed through the firewall."""
        firewall = PromptFirewall()
        # L1: abstract error message, no secrets
        prompt = "Explain what an IndexError means in Python."
        decision = firewall.classify(prompt)
        assert not decision.blocked

        payload = firewall.wrap(prompt)
        assert payload.level in (DataLevel.L0, DataLevel.L1)
        assert payload.policy_decision == "ALLOW"
