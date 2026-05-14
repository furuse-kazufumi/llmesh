"""Tests for AuditTrace — log/verify, tamper detection, L3/L4 prompt privacy.

v0.2.0 additions:
- AuditTrace requires unsafe_no_lock=True in tests (no real process lock needed).
- verify_chain_detailed() returns VerifyResult with first_error_seq.
- Multi-thread concurrent append produces a verifiable chain.
"""
import json
import threading
from pathlib import Path

import pytest

from llmesh.audit import AuditTrace

HMAC_KEY = b"test-hmac-key-32bytes-padded-here"
NODE_ID = "peer:testnode"
TASK_ID = "12345678-1234-4234-89ab-123456789abc"


def _make_trace(tmp_path: Path) -> tuple[AuditTrace, Path]:
    log_path = tmp_path / "audit.jsonl"
    trace = AuditTrace(log_path, HMAC_KEY, unsafe_no_lock=True)
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
        for field in ("seq_no", "event_type", "node_id", "task_id",
                      "policy_decision", "output_sha256", "timestamp", "entry_hmac"):
            assert field in entry

    def test_seq_no_is_sequential(self, tmp_path):
        trace, path = _make_trace(tmp_path)
        for _ in range(3):
            trace.log("evt", NODE_ID, TASK_ID, "ALLOW", "a" * 64)
        lines = path.read_text().strip().splitlines()
        for i, line in enumerate(lines):
            entry = json.loads(line)
            assert entry["seq_no"] == i


class TestVerifyChainDetailed:
    """verify_chain_detailed() returns VerifyResult with structured error info."""

    def test_valid_chain_returns_true_result(self, tmp_path):
        trace, path = _make_trace(tmp_path)
        for _ in range(3):
            trace.log("evt", NODE_ID, TASK_ID, "ALLOW", "a" * 64)
        result = AuditTrace.verify_chain_detailed(path, HMAC_KEY)
        assert result.valid is True
        assert result.entry_count == 3
        assert result.first_error_seq is None
        assert result.error_detail == ""
        assert bool(result) is True

    def test_tampered_entry_reports_first_error_seq(self, tmp_path):
        trace, path = _make_trace(tmp_path)
        for _ in range(4):
            trace.log("evt", NODE_ID, TASK_ID, "ALLOW", "a" * 64)

        lines = path.read_text().splitlines()
        entry = json.loads(lines[1])
        entry["policy_decision"] = "FORGED"
        lines[1] = json.dumps(entry)
        path.write_text("\n".join(lines) + "\n")

        result = AuditTrace.verify_chain_detailed(path, HMAC_KEY)
        assert result.valid is False
        assert result.first_error_seq == 1
        assert "hmac_mismatch" in result.error_detail

    def test_empty_file_reports_empty_error(self, tmp_path):
        path = tmp_path / "empty.jsonl"
        path.write_text("")
        result = AuditTrace.verify_chain_detailed(path, HMAC_KEY)
        assert result.valid is False
        assert "empty_file" in result.error_detail

    def test_nonexistent_file_reports_not_found(self, tmp_path):
        path = tmp_path / "ghost.jsonl"
        result = AuditTrace.verify_chain_detailed(path, HMAC_KEY)
        assert result.valid is False
        assert "file_not_found" in result.error_detail

    def test_missing_hmac_field_detected(self, tmp_path):
        trace, path = _make_trace(tmp_path)
        trace.log("evt", NODE_ID, TASK_ID, "ALLOW", "a" * 64)

        lines = path.read_text().splitlines()
        entry = json.loads(lines[0])
        del entry["entry_hmac"]
        path.write_text(json.dumps(entry) + "\n")

        result = AuditTrace.verify_chain_detailed(path, HMAC_KEY)
        assert result.valid is False
        assert "missing_entry_hmac" in result.error_detail


class TestTamperDetection:
    def test_modified_field_detected(self, tmp_path):
        trace, path = _make_trace(tmp_path)
        trace.log("tool_call", NODE_ID, TASK_ID, "ALLOW", "a" * 64)
        trace.log("tool_call", NODE_ID, TASK_ID, "ALLOW", "b" * 64)

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

        lines = path.read_text().splitlines()
        path.write_text("\n".join(lines[1:]) + "\n")

        assert AuditTrace.verify_chain(path, HMAC_KEY) is False

    def test_reordered_entries_detected(self, tmp_path):
        trace, path = _make_trace(tmp_path)
        trace.log("first", NODE_ID, TASK_ID, "ALLOW", "a" * 64)
        trace.log("second", NODE_ID, TASK_ID, "ALLOW", "b" * 64)

        lines = path.read_text().splitlines()
        lines[0], lines[1] = lines[1], lines[0]
        path.write_text("\n".join(lines) + "\n")

        assert AuditTrace.verify_chain(path, HMAC_KEY) is False

    def test_appended_forged_entry_detected(self, tmp_path):
        trace, path = _make_trace(tmp_path)
        trace.log("real", NODE_ID, TASK_ID, "ALLOW", "a" * 64)

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


class TestMultiThreadAppend:
    """Multiple threads appending concurrently must produce a verifiable chain.

    This test validates the threading.Lock guard.  True multi-process locking
    is validated separately by the demo script (out-of-process workers).
    """

    def test_concurrent_thread_appends_chain_valid(self, tmp_path):
        log_path = tmp_path / "concurrent.jsonl"
        trace = AuditTrace(log_path, HMAC_KEY, unsafe_no_lock=True)

        errors: list[Exception] = []

        def worker(n: int) -> None:
            try:
                for i in range(5):
                    trace.log(
                        f"evt-{n}-{i}", NODE_ID, f"task-{n}-{i}", "ALLOW", "a" * 64
                    )
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Thread errors: {errors}"
        assert AuditTrace.verify_chain(log_path, HMAC_KEY) is True

    def test_chain_entry_count_matches_writes(self, tmp_path):
        log_path = tmp_path / "count.jsonl"
        trace = AuditTrace(log_path, HMAC_KEY, unsafe_no_lock=True)
        n_threads, n_per_thread = 4, 5

        def worker():
            for _ in range(n_per_thread):
                trace.log("evt", NODE_ID, TASK_ID, "ALLOW", "a" * 64)

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        result = AuditTrace.verify_chain_detailed(log_path, HMAC_KEY)
        assert result.valid is True
        assert result.entry_count == n_threads * n_per_thread


class TestL3L4PromptPrivacy:
    def test_l3_prompt_sha256_stored_not_body(self, tmp_path):
        trace, path = _make_trace(tmp_path)
        prompt_sha = "c" * 64
        trace.log(
            "tool_call", NODE_ID, TASK_ID, "BLOCK", "a" * 64,
            data_level=3, prompt_sha256=prompt_sha,
        )
        lines = path.read_text().strip().splitlines()
        entry = json.loads(lines[0])
        assert entry.get("prompt_sha256") == prompt_sha
        assert "prompt" not in entry or entry.get("prompt") is None

    def test_l4_prompt_sha256_stored_not_body(self, tmp_path):
        trace, path = _make_trace(tmp_path)
        secret_prompt = "sk-ant-secret-key-value"
        prompt_sha = "d" * 64
        trace.log(
            "tool_call", NODE_ID, TASK_ID, "BLOCK", "a" * 64,
            data_level=4, prompt_sha256=prompt_sha,
        )
        raw = path.read_text()
        assert secret_prompt not in raw
        entry = json.loads(raw.strip().splitlines()[0])
        assert entry.get("prompt_sha256") == prompt_sha

    def test_l0_no_prompt_sha256_field(self, tmp_path):
        trace, path = _make_trace(tmp_path)
        trace.log("tool_call", NODE_ID, TASK_ID, "ALLOW", "a" * 64, data_level=0)
        entry = json.loads(path.read_text().strip().splitlines()[0])
        assert "prompt_sha256" not in entry

    def test_l2_no_prompt_sha256_field(self, tmp_path):
        trace, path = _make_trace(tmp_path)
        trace.log(
            "tool_call", NODE_ID, TASK_ID, "ALLOW", "a" * 64,
            data_level=2, prompt_sha256="e" * 64,
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


class TestUnsafeLockFlag:
    """unsafe_no_lock=True bypasses platform lock check — dev environments only."""

    def test_unsafe_no_lock_allows_construction_without_platform_lock(self, tmp_path):
        from unittest.mock import patch
        path = tmp_path / "unsafe.jsonl"
        with patch("llmesh.audit.trace._LOCKING_AVAILABLE", False):
            trace = AuditTrace(path, HMAC_KEY, unsafe_no_lock=True)
            trace.log("evt", NODE_ID, TASK_ID, "ALLOW", "a" * 64)
        assert AuditTrace.verify_chain(path, HMAC_KEY) is True

    def test_no_lock_available_and_no_override_raises(self, tmp_path):
        from unittest.mock import patch
        path = tmp_path / "failclosed.jsonl"
        with patch("llmesh.audit.trace._LOCKING_AVAILABLE", False):
            with pytest.raises(RuntimeError, match="audit_locking_unavailable"):
                AuditTrace(path, HMAC_KEY)
