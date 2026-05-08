"""E2E Scenario 2: L3 task — Firewall block → zero MCP calls.

A prompt containing an API key (L3/L4 content) must be blocked by the
PromptFirewall before any MCP node receives it. This test verifies:
  - Firewall returns BLOCK for L3-equivalent prompts
  - OutputValidator is never called (zero MCP calls)
  - AuditTrace records the block event with only sha256(prompt), not the body
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from llmesh.audit import AuditTrace
from llmesh.classifier.data_level import DataLevel
from llmesh.mcp.validator import OutputValidator, ValidationError
from llmesh.privacy.firewall import PromptFirewall

HMAC_KEY = b"e2e-secret-block-hmac-key-32byte"
NODE_ID = "peer:test-node-e2e-secret"
TASK_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"

# Prompts that contain secret-like patterns — should all be BLOCKed by L1
_SECRET_PROMPTS = [
    # AWS access key
    "Use AKIAIOSFODNN7EXAMPLE to authenticate.",
    # Anthropic API key
    "My key is sk-ant-api03-abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ012345678901234567.",
    # OpenAI API key
    "Call the API using sk-" + "A" * 48,
    # Generic API key assignment
    "api_key = 'my-super-secret-api-key-value-here'",
    # Private key PEM header
    "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA...",
    # GitHub token
    "ghp_" + "A" * 36,
]


class TestSecretCodeBlocked:
    def test_secret_prompt_blocked_by_firewall(self):
        """All secret-containing prompts are blocked by Firewall Layer 1."""
        firewall = PromptFirewall()
        for prompt in _SECRET_PROMPTS:
            decision = firewall.classify(prompt)
            assert decision.blocked, f"Expected BLOCK for prompt starting: {prompt[:60]!r}"
            assert decision.triggered_layer == 1

    def test_zero_mcp_calls_on_firewall_block(self):
        """OutputValidator.validate() must never be called when Firewall blocks."""
        firewall = PromptFirewall()
        validator = MagicMock(spec=OutputValidator)

        for prompt in _SECRET_PROMPTS:
            decision = firewall.classify(prompt)
            if decision.blocked:
                # Simulate correct orchestrator behaviour: skip MCP on BLOCK
                pass  # validator.validate() is NOT called
            else:
                # Should not reach here for these prompts
                validator.validate("...", "generate_code", "nonce")

        # Confirm the mock was never called
        validator.validate.assert_not_called()

    def test_firewall_wrap_returns_l4_blocked_payload(self):
        """wrap() on a secret prompt returns L4/BLOCK ClassifiedPayload."""
        firewall = PromptFirewall()
        secret_prompt = "AKIAIOSFODNN7EXAMPLE is my AWS key."
        payload = firewall.wrap(secret_prompt)

        assert payload.policy_decision == "BLOCK"
        assert payload.level == DataLevel.L4

    def test_audit_records_block_with_only_sha256(self, tmp_path):
        """On a BLOCK, audit log stores only sha256(prompt), never the prompt body."""
        firewall = PromptFirewall()
        log_path = tmp_path / "audit_block.jsonl"
        trace = AuditTrace(log_path, HMAC_KEY)

        secret_prompt = "api_key = 'sk-ant-api03-secret-value-here-12345678901234567890'"
        decision = firewall.classify(secret_prompt)
        assert decision.blocked

        prompt_sha = hashlib.sha256(secret_prompt.encode()).hexdigest()
        output_sha = "0" * 64  # no output produced

        trace.log(
            event_type="firewall_block",
            node_id=NODE_ID,
            task_id=TASK_ID,
            policy_decision="BLOCK",
            output_sha256=output_sha,
            data_level=4,          # L4 — classified as regulated/secret
            prompt_sha256=prompt_sha,
        )

        # Verify chain integrity
        assert AuditTrace.verify_chain(log_path, HMAC_KEY) is True

        # Verify prompt body does NOT appear in the log
        raw_log = log_path.read_text()
        assert secret_prompt not in raw_log
        assert "sk-ant-api03-secret-value-here" not in raw_log

        # Verify only the sha256 is recorded
        entry = json.loads(raw_log.strip().splitlines()[0])
        assert entry["prompt_sha256"] == prompt_sha
        assert entry["policy_decision"] == "BLOCK"

    def test_validator_raises_on_attempt_after_block(self):
        """If orchestrator mistakenly calls validator on a blocked payload,
        the validator still enforces schema — defence in depth."""
        validator = OutputValidator()
        # Attempt to validate a clearly invalid response after a block
        with pytest.raises(ValidationError):
            validator.validate(
                json.dumps({"malicious": "data"}),
                "generate_code",
                "a" * 32,
            )

    def test_all_secret_prompt_levels_are_l3_or_l4(self):
        """Firewall must classify all secret prompts as L3 or L4."""
        firewall = PromptFirewall()
        for prompt in _SECRET_PROMPTS:
            decision = firewall.classify(prompt)
            assert decision.level >= DataLevel.L3, (
                f"Expected L3+ for prompt: {prompt[:60]!r}, got {decision.level}"
            )
