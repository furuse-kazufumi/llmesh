"""P0-5: L3+ privacy pipeline enforcement at the MCP server boundary.

Key invariants verified:
- Raw L3 prompt text never reaches the LLM backend mock.
- Raw L4 prompt text never reaches the LLM backend mock.
- L4 requests return 422 without touching summarizer or backend.
- L3 requests are summarized; the backend receives only the summary.
- L0/L1 requests pass through unchanged.
- Summarizer exception causes fail-closed (422).
"""
from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from llmesh.mcp.server import app

client = TestClient(app, raise_server_exceptions=False)


def _nonce() -> str:
    return uuid.uuid4().hex[:32]


def _task_id() -> str:
    return str(uuid.uuid4())


def _post(prompt: str, tool: str = "generate_code") -> Any:
    tid = _task_id()
    nc = _nonce()
    body = {
        "task_id": tid,
        "caller_nonce": nc,
        "prompt": prompt,
        "language": "python",
    }
    good_resp = {
        "task_id": tid,
        "caller_nonce_echo": nc,
        "code": "def f(): pass",
        "language": "python",
        "explanation": "ok",
        "dependencies_added": [],
        "generated_files": [],
        "cve_scan_requested": False,
    }
    return body, good_resp


# ---------------------------------------------------------------------------
# L4 prompts — always blocked before summarizer or backend
# ---------------------------------------------------------------------------

class TestL4Block:
    def test_l4_secret_never_reaches_backend(self):
        """L4: secret key → 422; backend invoke must NOT be called."""
        body, _ = _post("api_key = 'sk-ant-abcdefghijklmnopqrstuvwxyz1234567890ABCDEF'")
        backend_called = []

        with patch("llmesh.mcp.server._llm_backend") as mock_be:
            mock_be.invoke.side_effect = lambda *a, **kw: backend_called.append(True) or {}
            resp = client.post("/tools/generate_code", json=body,
                               headers={"Content-Type": "application/json"})

        assert resp.status_code == 422
        assert not backend_called, "Backend must NOT be called for L4 prompts"

    def test_l4_oversized_blocked_before_backend(self):
        """Oversized payload → L4 BLOCK; backend not called."""
        from llmesh.privacy.firewall import _MAX_PAYLOAD_CHARS
        body, _ = _post("A" * (_MAX_PAYLOAD_CHARS + 1))
        backend_called = []

        with patch("llmesh.mcp.server._llm_backend") as mock_be:
            mock_be.invoke.side_effect = lambda *a, **kw: backend_called.append(True) or {}
            resp = client.post("/tools/generate_code", json=body,
                               headers={"Content-Type": "application/json"})

        assert resp.status_code == 422
        assert not backend_called

    def test_l4_summarizer_never_called(self):
        """L4 prompts must not even enter the summarizer."""
        body, _ = _post("GITHUB_TOKEN=ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef1234")
        summarizer_called = []

        with patch("llmesh.mcp.server._summarizer") as mock_sum:
            mock_sum.summarize_text.side_effect = lambda *a, **kw: summarizer_called.append(True)
            client.post("/tools/generate_code", json=body,
                        headers={"Content-Type": "application/json"})

        assert not summarizer_called, "Summarizer must NOT be called for L4 prompts"


# ---------------------------------------------------------------------------
# L3 prompts — summarized; raw text must not reach backend
# ---------------------------------------------------------------------------

class TestL3Summarization:
    def _l3_prompt(self) -> str:
        return "Review code at /home/user/company/secret/main.py for security issues"

    def test_l3_raw_prompt_not_passed_to_backend(self):
        """The backend must receive the summary, not the raw L3 prompt."""
        body, good_resp = _post(self._l3_prompt())
        raw_prompt = body["prompt"]
        received_prompts: list[str] = []

        def capture_invoke(tool_name, req_body):
            received_prompts.append(req_body.get("prompt", ""))
            tid = req_body.get("task_id", body["task_id"])
            nc = req_body.get("caller_nonce", body["caller_nonce"])
            return {**good_resp, "task_id": tid, "caller_nonce_echo": nc}

        with patch("llmesh.mcp.server._llm_backend") as mock_be:
            mock_be.invoke.side_effect = capture_invoke
            client.post("/tools/generate_code", json=body,
                        headers={"Content-Type": "application/json"})

        # Backend must have been called
        assert received_prompts, "Backend must be called for L3 prompts (with summary)"
        # Raw prompt text must NOT appear in what was sent to backend
        for sent in received_prompts:
            assert raw_prompt not in sent, (
                f"Raw L3 prompt text must not reach backend. Got: {sent!r}"
            )

    def test_l3_summarizer_is_called(self):
        """Summarizer must be invoked for L3 prompts."""
        body, good_resp = _post(self._l3_prompt())
        tid, nc = body["task_id"], body["caller_nonce"]
        summarizer_called = []

        def fake_summarize(text, source_level):
            summarizer_called.append(text)
            from llmesh.privacy.summarizer import SummaryResult
            from llmesh.classifier.data_level import DataLevel
            return SummaryResult(
                original_level=source_level,
                summary_level=DataLevel.L1,
                summary="[REDACTED summary]",
                masks_applied=1,
                paths_anonymized=1,
                signatures_extracted=False,
                truncated=False,
            )

        with patch("llmesh.mcp.server._summarizer") as mock_sum:
            mock_sum.summarize_text.side_effect = fake_summarize
            with patch("llmesh.mcp.server._llm_backend") as mock_be:
                mock_be.invoke.return_value = {**good_resp, "task_id": tid, "caller_nonce_echo": nc}
                client.post("/tools/generate_code", json=body,
                            headers={"Content-Type": "application/json"})

        assert summarizer_called, "Summarizer must be called for L3 prompts"

    def test_l3_summarizer_failure_fails_closed(self):
        """If summarization raises, server must return 422 (fail-closed)."""
        body, _ = _post(self._l3_prompt())
        backend_called = []

        with patch("llmesh.mcp.server._summarizer") as mock_sum:
            mock_sum.summarize_text.side_effect = RuntimeError("summarizer down")
            with patch("llmesh.mcp.server._llm_backend") as mock_be:
                mock_be.invoke.side_effect = lambda *a, **kw: backend_called.append(True) or {}
                resp = client.post("/tools/generate_code", json=body,
                                   headers={"Content-Type": "application/json"})

        assert resp.status_code == 422
        assert not backend_called, "Backend must NOT be called when summarization fails"


# ---------------------------------------------------------------------------
# L0/L1 prompts — pass through unchanged
# ---------------------------------------------------------------------------

class TestL0L1PassThrough:
    def test_clean_prompt_reaches_backend_unchanged(self):
        clean = "Implement a binary search function in Python."
        body, good_resp = _post(clean)
        tid, nc = body["task_id"], body["caller_nonce"]
        received: list[str] = []

        def capture(tool, req_body):
            received.append(req_body.get("prompt", ""))
            return {**good_resp, "task_id": tid, "caller_nonce_echo": nc}

        with patch("llmesh.mcp.server._llm_backend") as mock_be:
            mock_be.invoke.side_effect = capture
            resp = client.post("/tools/generate_code", json=body,
                               headers={"Content-Type": "application/json"})

        assert resp.status_code == 200
        assert received and received[0] == clean
