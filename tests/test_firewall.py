"""Tests for PromptFirewall Layer 1/2 — fail-closed behavior."""
import pytest
from unittest.mock import patch
from llmesh.privacy import PromptFirewall
from llmesh.classifier import DataLevel


FW = PromptFirewall()


class TestLayer1:
    def test_clean_prompt_passes(self):
        d = FW.classify("Implement a bounded retry utility in Python.")
        assert not d.blocked

    def test_api_key_blocked(self):
        d = FW.classify("api_key = 'sk-ant-abcdefghijklmnopqrstuvwxyz1234567890ABCDEF'")
        assert d.blocked
        assert "layer1" in d.reason

    def test_pem_private_key_blocked(self):
        d = FW.classify("-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA")
        assert d.blocked

    def test_jwt_blocked(self):
        d = FW.classify("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.abc123def456")
        assert d.blocked

    def test_aws_access_key_blocked(self):
        d = FW.classify("key = AKIAIOSFODNN7EXAMPLE")
        assert d.blocked

    def test_gh_token_blocked(self):
        d = FW.classify("GITHUB_TOKEN=ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef1234")
        assert d.blocked

    def test_level_is_l4_when_blocked(self):
        d = FW.classify("password = 'super_secret_password_123'")
        assert d.blocked
        assert d.level == DataLevel.L4


class TestLayer2:
    def test_absolute_unix_path_blocked(self):
        d = FW.classify("Read the file at /home/user/company/secret/config.yaml")
        assert d.blocked
        assert "layer2" in d.reason

    def test_windows_path_blocked(self):
        d = FW.classify(r"Load from C:\Users\admin\company\proprietary\code.py")
        assert d.blocked

    def test_internal_import_blocked(self):
        d = FW.classify("from internal.auth import SecretManager")
        assert d.blocked

    def test_oversized_payload_blocked(self):
        fw = PromptFirewall(max_payload_chars=100)
        d = fw.classify("A" * 101)
        assert d.blocked
        assert "too_large" in d.reason

    def test_normal_size_passes(self):
        fw = PromptFirewall(max_payload_chars=100)
        d = fw.classify("A" * 99)
        assert not d.blocked


class TestFailClosed:
    def test_exception_in_pipeline_returns_block(self):
        fw = PromptFirewall()
        with patch.object(fw, "_run_pipeline", side_effect=RuntimeError("boom")):
            d = fw.classify("anything")
        assert d.blocked
        assert d.level == DataLevel.L4
        assert "fail_closed" in d.reason

    def test_exception_in_layer1_returns_block(self):
        fw = PromptFirewall()
        with patch.object(fw, "_layer1", side_effect=ValueError("internal error")):
            d = fw.classify("anything")
        assert d.blocked
        assert d.level == DataLevel.L4


class TestWrap:
    def test_wrap_clean_returns_allowed_payload(self):
        fw = PromptFirewall()
        p = fw.wrap("Implement retry logic.")
        assert p.policy_decision == "ALLOW"
        assert p.level <= DataLevel.L1

    def test_wrap_secret_returns_blocked_l4(self):
        fw = PromptFirewall()
        p = fw.wrap("api_key = 'sk-ant-abcdefghijklmnopqrstuvwxyz1234567890ABCDEF'")
        assert p.policy_decision == "BLOCK"
        assert p.level == DataLevel.L4


class TestFirewallAudit:
    """AuditTrace integration — firewall_allow / firewall_block events."""

    def _make_audit(self, tmp_path):
        from llmesh.audit import AuditTrace
        key = b"test-audit-hmac-key-32bytes-here"
        path = tmp_path / "fw_audit.jsonl"
        return AuditTrace(path, key), path, key

    def test_clean_prompt_logs_firewall_allow(self, tmp_path):
        audit, path, key = self._make_audit(tmp_path)
        fw = PromptFirewall(audit_trace=audit)
        fw.classify("Write a sort algorithm.", node_id="n1", task_id="t1")
        import json
        entry = json.loads(path.read_text().strip())
        assert entry["event_type"] == "firewall_allow"
        assert entry["policy_decision"] == "ALLOW"
        assert entry["node_id"] == "n1"
        assert entry["task_id"] == "t1"

    def test_blocked_prompt_logs_firewall_block(self, tmp_path):
        audit, path, key = self._make_audit(tmp_path)
        fw = PromptFirewall(audit_trace=audit)
        fw.classify("api_key = 'AKIAIOSFODNN7EXAMPLE'", node_id="n2", task_id="t2")
        import json
        entry = json.loads(path.read_text().strip())
        assert entry["event_type"] == "firewall_block"
        assert entry["policy_decision"] == "BLOCK"

    def test_audit_chain_verifies(self, tmp_path):
        from llmesh.audit import AuditTrace
        key = b"test-audit-hmac-key-32bytes-here"
        path = tmp_path / "fw_chain.jsonl"
        audit = AuditTrace(path, key)
        fw = PromptFirewall(audit_trace=audit)
        fw.classify("clean prompt", node_id="n3", task_id="t3")
        fw.classify("api_key = 'AKIAIOSFODNN7EXAMPLE'", node_id="n3", task_id="t3")
        assert AuditTrace.verify_chain(path, key) is True
