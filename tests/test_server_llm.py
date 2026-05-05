"""Integration tests for server.py with a mocked LLM backend.

Uses FastAPI TestClient (httpx) — no live Ollama or network required.
Tests the full request → nonce check → LLM invoke → validate → response path.
"""
from __future__ import annotations

import json
import uuid
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from llmesh.llm.backend import BackendError
from llmesh.mcp.server import app

client = TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _nonce() -> str:
    return uuid.uuid4().hex[:32]


def _task_id() -> str:
    return str(uuid.uuid4())


def _post(tool_name: str, body: dict[str, Any], node_id: str = "") -> Any:
    headers = {"Content-Type": "application/json"}
    if node_id:
        headers["X-Node-Id"] = node_id
    return client.post(f"/tools/{tool_name}", json=body, headers=headers)


def _valid_generate_code_response(task_id: str, nonce: str) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "code": "def hello(): pass",
        "language": "python",
        "explanation": "Simple stub.",
        "dependencies_added": [],
        "generated_files": [],
        "cve_scan_requested": False,
        "caller_nonce_echo": nonce,
    }


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------

class TestHealth:
    def test_health_ok(self):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "generate_code" in data["tools"]


# ---------------------------------------------------------------------------
# Input validation (no LLM needed)
# ---------------------------------------------------------------------------

class TestInputValidation:
    def test_unknown_tool_404(self):
        resp = _post("nonexistent", {"task_id": _task_id(), "caller_nonce": _nonce()})
        assert resp.status_code == 404

    def test_missing_task_id_422(self):
        resp = _post("generate_code", {"caller_nonce": _nonce()})
        assert resp.status_code == 422

    def test_invalid_task_id_422(self):
        resp = _post("generate_code", {"task_id": "not-a-uuid", "caller_nonce": _nonce()})
        assert resp.status_code == 422

    def test_missing_nonce_422(self):
        resp = _post("generate_code", {"task_id": _task_id()})
        assert resp.status_code == 422

    def test_wrong_content_type_415(self):
        resp = client.post(
            "/tools/generate_code",
            content=b"hello",
            headers={"Content-Type": "text/plain"},
        )
        assert resp.status_code == 415


# ---------------------------------------------------------------------------
# LLM backend success path
# ---------------------------------------------------------------------------

class TestLLMSuccess:
    def test_generate_code_returns_validated_response(self):
        tid = _task_id()
        nonce = _nonce()
        body = {"task_id": tid, "caller_nonce": nonce, "prompt": "hello world", "language": "python"}

        with patch("llmesh.mcp.server._llm_backend") as mock_backend:
            mock_backend.invoke.return_value = _valid_generate_code_response(tid, nonce)
            resp = _post("generate_code", body, node_id="node-test-1")

        assert resp.status_code == 200
        data = resp.json()
        assert data["task_id"] == tid
        assert data["language"] == "python"
        assert data["caller_nonce_echo"] == nonce

    def test_llm_result_missing_task_id_is_injected(self):
        """server.py injects task_id if LLM omits it."""
        tid = _task_id()
        nonce = _nonce()
        body = {"task_id": tid, "caller_nonce": nonce}

        llm_result = _valid_generate_code_response(tid, nonce)
        del llm_result["task_id"]  # LLM forgot to include it

        with patch("llmesh.mcp.server._llm_backend") as mock_backend:
            mock_backend.invoke.return_value = llm_result
            resp = _post("generate_code", body, node_id="node-inject-1")

        assert resp.status_code == 200
        assert resp.json()["task_id"] == tid

    def test_llm_result_missing_nonce_echo_is_injected(self):
        """server.py injects caller_nonce_echo if LLM omits it."""
        tid = _task_id()
        nonce = _nonce()
        body = {"task_id": tid, "caller_nonce": nonce}

        llm_result = _valid_generate_code_response(tid, nonce)
        del llm_result["caller_nonce_echo"]

        with patch("llmesh.mcp.server._llm_backend") as mock_backend:
            mock_backend.invoke.return_value = llm_result
            resp = _post("generate_code", body, node_id="node-inject-2")

        assert resp.status_code == 200
        assert resp.json()["caller_nonce_echo"] == nonce


# ---------------------------------------------------------------------------
# LLM backend error paths
# ---------------------------------------------------------------------------

class TestLLMErrors:
    def test_backend_error_returns_502(self):
        body = {"task_id": _task_id(), "caller_nonce": _nonce()}
        with patch("llmesh.mcp.server._llm_backend") as mock_backend:
            mock_backend.invoke.side_effect = BackendError("ollama_unreachable:refused")
            resp = _post("generate_code", body, node_id="node-err-1")
        assert resp.status_code == 502
        assert "llm_backend_error" in resp.json()["detail"]

    def test_invalid_llm_output_returns_502(self):
        tid = _task_id()
        nonce = _nonce()
        body = {"task_id": tid, "caller_nonce": nonce}

        # LLM returns wrong nonce echo — OutputValidator should reject
        bad_result = _valid_generate_code_response(tid, nonce)
        bad_result["caller_nonce_echo"] = "a" * 32  # wrong nonce

        with patch("llmesh.mcp.server._llm_backend") as mock_backend:
            mock_backend.invoke.return_value = bad_result
            resp = _post("generate_code", body, node_id="node-err-2")

        assert resp.status_code == 502
        assert "llm_output_invalid" in resp.json()["detail"]

    def test_replay_attack_409(self):
        """Same nonce used twice on the same node must be rejected."""
        tid1 = _task_id()
        tid2 = _task_id()
        nonce = _nonce()

        def make_result(tid: str) -> dict[str, Any]:
            return _valid_generate_code_response(tid, nonce)

        with patch("llmesh.mcp.server._llm_backend") as mock_backend:
            mock_backend.invoke.side_effect = [
                make_result(tid1),
                make_result(tid2),
            ]
            resp1 = _post("generate_code", {"task_id": tid1, "caller_nonce": nonce}, node_id="node-replay")
            resp2 = _post("generate_code", {"task_id": tid2, "caller_nonce": nonce}, node_id="node-replay")

        assert resp1.status_code == 200
        assert resp2.status_code == 409


# ---------------------------------------------------------------------------
# Prompt firewall integration
# ---------------------------------------------------------------------------

class TestFirewallIntegration:
    def test_firewall_blocked_prompt_returns_422(self):
        """Requests containing secrets are blocked before reaching the LLM."""
        tid = _task_id()
        nonce = _nonce()
        body = {
            "task_id": tid,
            "caller_nonce": nonce,
            "prompt": "api_key = 'AKIAIOSFODNN7EXAMPLE'",
        }
        resp = _post("generate_code", body, node_id="node-fw-1")
        assert resp.status_code == 422
        assert "firewall_blocked" in resp.json()["detail"]

    def test_clean_prompt_passes_firewall_and_reaches_llm(self):
        """Clean prompts are forwarded to the LLM backend."""
        tid = _task_id()
        nonce = _nonce()
        body = {"task_id": tid, "caller_nonce": nonce, "prompt": "sort a list in Python"}
        with patch("llmesh.mcp.server._llm_backend") as mock_backend:
            mock_backend.invoke.return_value = _valid_generate_code_response(tid, nonce)
            resp = _post("generate_code", body, node_id="node-fw-2")
        assert resp.status_code == 200

    def test_backend_error_logs_to_audit(self):
        """BackendError is recorded as ``backend_error`` in the audit log."""
        from llmesh.llm.backend import BackendError
        from unittest.mock import MagicMock
        tid = _task_id()
        nonce = _nonce()
        body = {"task_id": tid, "caller_nonce": nonce}
        mock_audit = MagicMock()
        with patch("llmesh.mcp.server._llm_backend") as mock_backend, \
             patch("llmesh.mcp.server._audit", mock_audit):
            mock_backend.invoke.side_effect = BackendError("unreachable")
            resp = _post("generate_code", body, node_id="node-audit-1")
        assert resp.status_code == 502
        mock_audit.log.assert_called_once()
        call_kwargs = mock_audit.log.call_args
        assert call_kwargs.kwargs.get("event_type") == "backend_error" or \
               (call_kwargs.args and call_kwargs.args[0] == "backend_error")
