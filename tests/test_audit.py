"""Tests for AuditTrace — log/verify, tamper detection, L3/L4 prompt privacy."""
import json
import os
import tempfile
from pathlib import Path

import pytest

from llmesh.audit import AuditTrace

HMAC_KEY = b"test-hmac-key-32bytes-padded-here"
NODE_ID = "peer:testnode"
TASK_ID = "12345678-1234-4234-89ab-123456789abc"


def _make_trace(tmp_path: Path) -> tuple[AuditTrace, Path]:
    log_path = tmp_path / "audit.jsonl"
    trace = AuditTrace(log_path, HMAC_KEY)
    return trace, log_path


class TestLogAndVerify:
    def test_single_entry_verifies(self, tmp_path):
        trace, path = _make_trace(tmp_path)
        trace.log("tool_call", NODE_ID, TASK_ID, "ALLOW", "a" * 64)
        assert AuditTrace.verify_chain(path, HMAC_KEY) is True

    def test_multiple_entries_verify(self, tmp_path):
        trace, path = _make_trace(tmp_path)
        for i in range(5):
            trace.log("tool_call", NODE_ID, f"task-{i}", "ALLOW", "a" * 64)
        assert AuditTrace.verify_chain(path, HMAC_KEY) is True

    def test_verify_empty_file_returns_false(self, tmp_path):
        path = tmp_path / "empty.jsonl"
        path.write_text("")
        assert AuditTrace.verify_chain(path, HMAC_KEY) is False

    def test_verify_nonexistent_file_returns_false(self, tmp_path):
        path = tmp_path / "nonexistent.jsonl"
        assert AuditTrace.verify_chain(path, HMAC_KEY) is False

    def test_wrong_hmac_key_fails_verification(self, tmp_path):
        trace, path = _make_trace(tmp_path)
        trace.log("tool_call", NODE_ID, TASK_ID, "ALLOW", "a" * 64)
        wrong_key = b"wrong-key-32bytes-padded-here---"
        assert AuditTrace.verify_chain(path, wrong_key) is False

    def test_entry_has_required_fields(self, tmp_path):
        trace, path = _make_trace(tmp_path)
        trace.log("tool_call", NODE_ID, TASK_ID, "BLOCK", "b" * 64)
        lines = path.read_text().strip().splitlines()
        entry = json.loads(lines[0])
        assert "seq_no" in entry
        assert "event_type" in entry
        assert "node_id" in entry
        assert "task_id" in entry
        assert "policy_decision" in entry
        assert "output_sha256" in entry
        assert "timestamp" in entry
        assert "entry_hmac" in entry

    def test_seq_no_is_sequential(self, tmp_path):
        trace, path = _make_trace(tmp_path)
        for _ in range(3):
            trace.log("evt", NODE_ID, TASK_ID, "ALLOW", "a" * 64)
        lines = path.read_text().strip().splitlines()
        for i, line in enumerate(lines):
            entry = json.loads(line)
            assert entry["seq_no"] == i


class TestTamperDetection:
    def test_modified_field_detected(self, tmp_path):
        trace, path = _make_trace(tmp_path)
        trace.log("tool_call", NODE_ID, TASK_ID, "ALLOW", "a" * 64)
        trace.log("tool_call", NODE_ID, TASK_ID, "ALLOW", "b" * 64)

        # Tamper: modify the policy_decision of the first entry
        lines = path.read_text().splitlines()
        entry = json.loads(lines[0])
        entry["policy_decision"] = "BLOCK"
        lines[0] = json.dumps(entry)
        path.write_text("\n".join(lines) + "\n")

        assert AuditTrace.verify_chain(path, HMAC_KEY) is False

    def test_deleted_entry_detected(self, tmp_path):
        trace, path = _make_trace(tmp_path)
        for _ in range(3):
            trace.log("evt", NODE_ID, TASK_ID, "ALLOW", "a" * 64)

        # Delete the first line
        lines = path.read_text().splitlines()
        path.write_text("\n".join(lines[1:]) + "\n")

        assert AuditTrace.verify_chain(path, HMAC_KEY) is False

    def test_reordered_entries_detected(self, tmp_path):
        trace, path = _make_trace(tmp_path)
        trace.log("first", NODE_ID, TASK_ID, "ALLOW", "a" * 64)
        trace.log("second", NODE_ID, TASK_ID, "ALLOW", "b" * 64)

        # Swap the two lines
        lines = path.read_text().splitlines()
        lines[0], lines[1] = lines[1], lines[0]
        path.write_text("\n".join(lines) + "\n")

        assert AuditTrace.verify_chain(path, HMAC_KEY) is False

    def test_appended_forged_entry_detected(self, tmp_path):
        trace, path = _make_trace(tmp_path)
        trace.log("real", NODE_ID, TASK_ID, "ALLOW", "a" * 64)

        # Append a forged entry with wrong HMAC
        forged = {
            "seq_no": 1,
            "event_type": "forged",
            "node_id": NODE_ID,
            "task_id": TASK_ID,
            "policy_decision": "ALLOW",
            "output_sha256": "c" * 64,
            "timestamp": "2024-01-01T00:00:00+00:00",
            "entry_hmac": "0" * 64,
        }
        with path.open("a") as f:
            f.write(json.dumps(forged) + "\n")

        assert AuditTrace.verify_chain(path, HMAC_KEY) is False


class TestL3L4PromptPrivacy:
    def test_l3_prompt_sha256_stored_not_body(self, tmp_path):
        trace, path = _make_trace(tmp_path)
        prompt_sha = "c" * 64
        trace.log(
            "tool_call", NODE_ID, TASK_ID, "BLOCK", "a" * 64,
            data_level=3,
            prompt_sha256=prompt_sha,
        )
        lines = path.read_text().strip().splitlines()
        entry = json.loads(lines[0])
        # sha256 recorded
        assert entry.get("prompt_sha256") == prompt_sha
        # raw prompt body must NOT be anywhere in the entry
        assert "prompt" not in entry or entry.get("prompt") is None

    def test_l4_prompt_sha256_stored_not_body(self, tmp_path):
        trace, path = _make_trace(tmp_path)
        secret_prompt = "sk-ant-secret-key-value"
        prompt_sha = "d" * 64
        trace.log(
            "tool_call", NODE_ID, TASK_ID, "BLOCK", "a" * 64,
            data_level=4,
            prompt_sha256=prompt_sha,
        )
        raw = path.read_text()
        # The secret prompt body must NEVER appear in the log
        assert secret_prompt not in raw
        entry = json.loads(raw.strip().splitlines()[0])
        assert entry.get("prompt_sha256") == prompt_sha

    def test_l0_no_prompt_sha256_field(self, tmp_path):
        trace, path = _make_trace(tmp_path)
        trace.log("tool_call", NODE_ID, TASK_ID, "ALLOW", "a" * 64, data_level=0)
        entry = json.loads(path.read_text().strip().splitlines()[0])
        assert "prompt_sha256" not in entry

    def test_l2_no_prompt_sha256_field(self, tmp_path):
        """L2 is internal but below the L3 threshold — no prompt_sha256."""
        trace, path = _make_trace(tmp_path)
        trace.log(
            "tool_call", NODE_ID, TASK_ID, "ALLOW", "a" * 64,
            data_level=2,
            prompt_sha256="e" * 64,
        )
        entry = json.loads(path.read_text().strip().splitlines()[0])
        assert "prompt_sha256" not in entry

    def test_verify_chain_still_passes_with_l3_entries(self, tmp_path):
        trace, path = _make_trace(tmp_path)
        trace.log("l0_call", NODE_ID, TASK_ID, "ALLOW", "a" * 64, data_level=0)
        trace.log(
            "l3_call", NODE_ID, TASK_ID, "BLOCK", "b" * 64,
            data_level=3, prompt_sha256="f" * 64,
        )
        assert AuditTrace.verify_chain(path, HMAC_KEY) is True
