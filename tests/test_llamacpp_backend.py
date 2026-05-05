"""Tests for llmesh.llm.llamacpp — LlamaCppBackend."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from llmesh.llm.llamacpp import LlamaCppBackend
from llmesh.llm.backend import BackendError


def _mock_resp(data: object, status: int = 200) -> MagicMock:
    m = MagicMock()
    m.status = status
    m.read.return_value = json.dumps(data).encode()
    m.__enter__ = lambda s: s
    m.__exit__ = MagicMock(return_value=False)
    return m


def _openai_resp(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}


class TestLlamaCppBackendHealth:
    def test_healthy_when_status_ok(self):
        b = LlamaCppBackend()
        with patch("urllib.request.urlopen", return_value=_mock_resp({"status": "ok"})):
            assert b.health() is True

    def test_unhealthy_when_status_not_ok(self):
        b = LlamaCppBackend()
        with patch("urllib.request.urlopen", return_value=_mock_resp({"status": "loading"})):
            assert b.health() is False

    def test_unhealthy_on_connection_error(self):
        import urllib.error
        b = LlamaCppBackend()
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            assert b.health() is False


class TestLlamaCppBackendInvoke:
    def setup_method(self):
        self.backend = LlamaCppBackend(base_url="http://localhost:8080")
        self.valid_content = json.dumps({
            "task_id": "test-id",
            "code": "def f(): pass",
            "language": "python",
            "explanation": "stub",
            "dependencies_added": [],
            "generated_files": [],
            "cve_scan_requested": False,
            "caller_nonce_echo": "aabbccddeeff00112233445566778899",
        })

    def _req_body(self) -> dict:
        return {
            "task_id": "test-id",
            "code_request": "fibonacci",
            "language": "python",
            "caller_nonce": "aabbccddeeff00112233445566778899",
        }

    def test_invoke_returns_parsed_dict(self):
        resp = _mock_resp(_openai_resp(self.valid_content))
        with patch("urllib.request.urlopen", return_value=resp):
            result = self.backend.invoke("generate_code", self._req_body())
        assert isinstance(result, dict)
        assert result["code"] == "def f(): pass"

    def test_invoke_unknown_tool_raises(self):
        with pytest.raises(BackendError, match="no_prompt_builder_for"):
            self.backend.invoke("nonexistent_tool", {})

    def test_invoke_url_error_raises(self):
        import urllib.error
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            with pytest.raises(BackendError, match="llamacpp_unreachable"):
                self.backend.invoke("generate_code", self._req_body())

    def test_invoke_timeout_raises(self):
        with patch("urllib.request.urlopen", side_effect=TimeoutError()):
            with pytest.raises(BackendError, match="llamacpp_timeout"):
                self.backend.invoke("generate_code", self._req_body())

    def test_invoke_non_json_response_raises(self):
        m = MagicMock()
        m.read.return_value = b"not json"
        m.__enter__ = lambda s: s
        m.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=m):
            with pytest.raises(BackendError, match="llamacpp_response_not_json"):
                self.backend.invoke("generate_code", self._req_body())

    def test_invoke_missing_choices_raises(self):
        resp = _mock_resp({"error": "no choices"})
        with patch("urllib.request.urlopen", return_value=resp):
            with pytest.raises(BackendError, match="unexpected_llamacpp_response_shape"):
                self.backend.invoke("generate_code", self._req_body())

    def test_invoke_non_json_content_raises(self):
        resp = _mock_resp(_openai_resp("not a json object"))
        with patch("urllib.request.urlopen", return_value=resp):
            with pytest.raises(BackendError, match="llm_content_not_json"):
                self.backend.invoke("generate_code", self._req_body())

    def test_default_url_is_localhost_8080(self):
        b = LlamaCppBackend()
        assert "8080" in b._chat_url
