"""Tests for AnthropicBackend (v3.1+)."""
from __future__ import annotations

from io import BytesIO
import json

import pytest

from llmesh.llm.anthropic_backend import AnthropicBackend
from llmesh.llm.backend import BackendError


class _FakeResp:
    def __init__(self, body: bytes, status: int = 200):
        self._buf = BytesIO(body)
        self.status = status
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def read(self, n=-1): return self._buf.read(n)


def _ok_messages_response(text: str = '{"result":"ok"}') -> bytes:
    return json.dumps({
        "id": "msg_1",
        "model": "claude-haiku-4-5",
        "role": "assistant",
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
    }).encode()


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

class TestConstruct:
    def test_missing_api_key_raises(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(BackendError):
            AnthropicBackend()

    def test_explicit_api_key(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        b = AnthropicBackend(api_key="sk-ant-test")
        assert b._api_key == "sk-ant-test"

    def test_env_api_key(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env")
        b = AnthropicBackend()
        assert b._api_key == "sk-ant-env"

    def test_invalid_max_tokens(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        with pytest.raises(ValueError):
            AnthropicBackend(max_tokens=0)

    def test_invalid_timeout(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        with pytest.raises(ValueError):
            AnthropicBackend(timeout=0)

    def test_url_construction(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        b = AnthropicBackend()
        assert b._messages_url == "https://api.anthropic.com/v1/messages"


# ---------------------------------------------------------------------------
# Headers
# ---------------------------------------------------------------------------

class TestHeaders:
    def test_x_api_key_header(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        h = AnthropicBackend()._build_headers()
        assert h["x-api-key"] == "k"
        assert h["anthropic-version"] == "2023-06-01"

    def test_extra_headers(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        h = AnthropicBackend(extra_headers={"X-Custom": "1"})._build_headers()
        assert h["X-Custom"] == "1"


# ---------------------------------------------------------------------------
# Invoke
# ---------------------------------------------------------------------------

class TestInvoke:
    def test_happy_path(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        captured = {}
        def _fake_urlopen(req, timeout):
            captured["url"] = req.full_url
            captured["data"] = json.loads(req.data.decode())
            return _FakeResp(_ok_messages_response('{"answer": 42}'))
        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)

        b = AnthropicBackend(model="claude-haiku-4-5")
        out = b.invoke("review_code", {"code": "x", "language": "python"})
        assert out == {"answer": 42}
        assert captured["url"] == "https://api.anthropic.com/v1/messages"
        # Anthropic Messages API has system as a top-level param, not a msg.
        assert "system" in captured["data"]
        assert captured["data"]["model"] == "claude-haiku-4-5"

    def test_unknown_tool(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        b = AnthropicBackend()
        with pytest.raises(BackendError):
            b.invoke("not_a_tool", {})

    def test_http_error(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        import urllib.error
        def _boom(req, timeout):
            raise urllib.error.HTTPError(
                url=req.full_url, code=401, msg="unauthorized",
                hdrs=None, fp=BytesIO(b"bad key"),
            )
        monkeypatch.setattr("urllib.request.urlopen", _boom)
        b = AnthropicBackend()
        with pytest.raises(BackendError) as exc:
            b.invoke("review_code", {"code": "x", "language": "python"})
        assert "401" in str(exc.value)

    def test_url_error(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        import urllib.error
        def _boom(req, timeout):
            raise urllib.error.URLError("dns failure")
        monkeypatch.setattr("urllib.request.urlopen", _boom)
        b = AnthropicBackend()
        with pytest.raises(BackendError):
            b.invoke("review_code", {"code": "x", "language": "python"})

    def test_response_too_large(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        big = b"x" * 4096
        def _fake_urlopen(req, timeout):
            return _FakeResp(big)
        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
        b = AnthropicBackend(max_response_bytes=128)
        with pytest.raises(BackendError) as exc:
            b.invoke("review_code", {"code": "x", "language": "python"})
        assert "too_large" in str(exc.value)

    def test_no_text_block(self, monkeypatch):
        """Anthropic may return only tool_use blocks — we expect text."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        body = json.dumps({
            "content": [{"type": "tool_use", "name": "x", "input": {}}],
        }).encode()
        def _fake_urlopen(req, timeout):
            return _FakeResp(body)
        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
        b = AnthropicBackend()
        with pytest.raises(BackendError):
            b.invoke("review_code", {"code": "x", "language": "python"})

    def test_non_json_text(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        def _fake_urlopen(req, timeout):
            return _FakeResp(_ok_messages_response("not json"))
        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
        b = AnthropicBackend()
        with pytest.raises(BackendError):
            b.invoke("review_code", {"code": "x", "language": "python"})


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

class TestHealth:
    def test_health_true_on_ok(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        def _fake_urlopen(req, timeout):
            return _FakeResp(_ok_messages_response())
        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
        b = AnthropicBackend()
        assert b.health() is True

    def test_health_false_on_error(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        import urllib.error
        def _boom(req, timeout):
            raise urllib.error.URLError("nope")
        monkeypatch.setattr("urllib.request.urlopen", _boom)
        b = AnthropicBackend()
        assert b.health() is False
