"""Tests for llmesh.llm — OllamaBackend and prompt builders.

All tests run without a live Ollama instance by monkeypatching
urllib.request.urlopen. The backend is tested in isolation.
"""
from __future__ import annotations

import json
import uuid
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from llmesh.llm.backend import BackendError
from llmesh.llm.ollama import OllamaBackend
from llmesh.llm.prompt import build_prompt


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_nonce() -> str:
    return uuid.uuid4().hex[:32]


def _fake_ollama_response(content: dict[str, Any]) -> MagicMock:
    """Return a mock context-manager that yields an HTTP-like response."""
    body = json.dumps({
        "model": "llama3.2:latest",
        "message": {"role": "assistant", "content": json.dumps(content)},
    }).encode()
    mock_resp = MagicMock()
    mock_resp.read.return_value = body
    mock_resp.status = 200
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


def _fake_tags_response() -> MagicMock:
    body = json.dumps({"models": [{"name": "llama3.2:latest"}]}).encode()
    mock_resp = MagicMock()
    mock_resp.read.return_value = body
    mock_resp.status = 200
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


# ---------------------------------------------------------------------------
# OllamaBackend.health()
# ---------------------------------------------------------------------------

class TestOllamaHealth:
    def test_health_true_when_reachable(self):
        with patch("urllib.request.urlopen", return_value=_fake_tags_response()):
            backend = OllamaBackend()
            assert backend.health() is True

    def test_health_false_on_connection_error(self):
        import urllib.error
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            backend = OllamaBackend()
            assert backend.health() is False

    def test_health_false_on_timeout(self):
        with patch("urllib.request.urlopen", side_effect=TimeoutError()):
            backend = OllamaBackend()
            assert backend.health() is False


# ---------------------------------------------------------------------------
# OllamaBackend.invoke() — generate_code
# ---------------------------------------------------------------------------

class TestOllamaInvokeGenerateCode:
    def _body(self) -> dict[str, Any]:
        nonce = _make_nonce()
        return {
            "task_id": str(uuid.uuid4()),
            "caller_nonce": nonce,
            "prompt": "Write a hello world function in Python",
            "language": "python",
        }

    def _llm_result(self, body: dict[str, Any]) -> dict[str, Any]:
        return {
            "task_id": body["task_id"],
            "code": "def hello():\n    print('hello')",
            "language": "python",
            "explanation": "A simple hello world function.",
            "dependencies_added": [],
            "generated_files": [],
            "cve_scan_requested": False,
            "caller_nonce_echo": body["caller_nonce"],
        }

    def test_invoke_returns_dict(self):
        body = self._body()
        fake_resp = _fake_ollama_response(self._llm_result(body))
        with patch("urllib.request.urlopen", return_value=fake_resp):
            backend = OllamaBackend()
            result = backend.invoke("generate_code", body)
        assert isinstance(result, dict)
        assert result["language"] == "python"
        assert "code" in result

    def test_invoke_preserves_task_id(self):
        body = self._body()
        fake_resp = _fake_ollama_response(self._llm_result(body))
        with patch("urllib.request.urlopen", return_value=fake_resp):
            backend = OllamaBackend()
            result = backend.invoke("generate_code", body)
        assert result["task_id"] == body["task_id"]

    def test_invoke_preserves_nonce_echo(self):
        body = self._body()
        fake_resp = _fake_ollama_response(self._llm_result(body))
        with patch("urllib.request.urlopen", return_value=fake_resp):
            backend = OllamaBackend()
            result = backend.invoke("generate_code", body)
        assert result["caller_nonce_echo"] == body["caller_nonce"]


# ---------------------------------------------------------------------------
# OllamaBackend.invoke() — error paths
# ---------------------------------------------------------------------------

class TestOllamaInvokeErrors:
    def _body(self) -> dict[str, Any]:
        return {
            "task_id": str(uuid.uuid4()),
            "caller_nonce": _make_nonce(),
            "prompt": "test",
            "language": "python",
        }

    def test_backend_error_on_url_error(self):
        import urllib.error
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            backend = OllamaBackend()
            with pytest.raises(BackendError, match="ollama_unreachable"):
                backend.invoke("generate_code", self._body())

    def test_backend_error_on_timeout(self):
        with patch("urllib.request.urlopen", side_effect=TimeoutError()):
            backend = OllamaBackend()
            with pytest.raises(BackendError, match="ollama_timeout"):
                backend.invoke("generate_code", self._body())

    def test_backend_error_on_non_json_response(self):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"not json"
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            backend = OllamaBackend()
            with pytest.raises(BackendError, match="ollama_response_not_json"):
                backend.invoke("generate_code", self._body())

    def test_backend_error_on_missing_message_key(self):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"no_message": True}).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            backend = OllamaBackend()
            with pytest.raises(BackendError, match="unexpected_ollama_response_shape"):
                backend.invoke("generate_code", self._body())

    def test_backend_error_on_non_json_content(self):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "message": {"role": "assistant", "content": "not json at all"}
        }).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            backend = OllamaBackend()
            with pytest.raises(BackendError, match="llm_content_not_json"):
                backend.invoke("generate_code", self._body())

    def test_backend_error_on_unknown_tool(self):
        with patch("urllib.request.urlopen"):
            backend = OllamaBackend()
            with pytest.raises(BackendError, match="no_prompt_builder_for"):
                backend.invoke("nonexistent_tool", self._body())


# ---------------------------------------------------------------------------
# prompt.build_prompt()
# ---------------------------------------------------------------------------

class TestBuildPrompt:
    def _body(self, nonce: str | None = None) -> dict[str, Any]:
        return {
            "task_id": str(uuid.uuid4()),
            "caller_nonce": nonce or _make_nonce(),
            "prompt": "test",
            "language": "python",
        }

    @pytest.mark.parametrize("tool_name", [
        "generate_code", "review_code", "generate_tests", "critique_output"
    ])
    def test_build_prompt_returns_two_strings(self, tool_name: str):
        system, user = build_prompt(tool_name, self._body())
        assert isinstance(system, str) and len(system) > 0
        assert isinstance(user, str) and len(user) > 0

    def test_unknown_tool_raises_key_error(self):
        with pytest.raises(KeyError):
            build_prompt("unknown_tool", self._body())

    def test_user_message_contains_task_id(self):
        body = self._body()
        _, user = build_prompt("generate_code", body)
        assert body["task_id"] in user

    def test_user_message_contains_nonce(self):
        body = self._body()
        _, user = build_prompt("generate_code", body)
        assert body["caller_nonce"] in user

    def test_user_message_is_valid_json(self):
        body = self._body()
        _, user = build_prompt("generate_code", body)
        parsed = json.loads(user)
        assert isinstance(parsed, dict)
