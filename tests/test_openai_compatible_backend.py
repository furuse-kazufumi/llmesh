"""Tests for OpenAICompatibleBackend (v3.1+)."""
from __future__ import annotations

from io import BytesIO
import json

import pytest

from llmesh.llm.backend import BackendError
from llmesh.llm.openai_compatible import (
    OpenAICompatibleBackend,
    azure_openai_backend,
    deepseek_backend,
    groq_backend,
    mistral_backend,
    openai_backend,
    openrouter_backend,
    together_backend,
)


class _FakeResp:
    def __init__(self, body: bytes, status: int = 200):
        self._buf = BytesIO(body)
        self.status = status
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def read(self, n=-1): return self._buf.read(n)


def _ok_chat_response(content: str = '{"result":"ok"}') -> bytes:
    return json.dumps({
        "choices": [{"message": {"role": "assistant", "content": content}}],
    }).encode()


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

class TestConstruct:
    def test_missing_api_key_raises(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(BackendError):
            OpenAICompatibleBackend()

    def test_explicit_api_key(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        b = OpenAICompatibleBackend(api_key="sk-test")
        assert b._api_key == "sk-test"

    def test_env_api_key(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
        b = OpenAICompatibleBackend()
        assert b._api_key == "sk-from-env"

    def test_invalid_max_tokens(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "k")
        with pytest.raises(ValueError):
            OpenAICompatibleBackend(max_tokens=0)

    def test_invalid_timeout(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "k")
        with pytest.raises(ValueError):
            OpenAICompatibleBackend(timeout=0)

    def test_invalid_max_response_bytes(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "k")
        with pytest.raises(ValueError):
            OpenAICompatibleBackend(max_response_bytes=0)

    def test_chat_url_construction(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "k")
        b = OpenAICompatibleBackend(base_url="https://example.com/v1")
        assert b._chat_url == "https://example.com/v1/chat/completions"

    def test_chat_url_already_includes_path(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "k")
        # Azure-style: base_url already ends with /chat/completions...
        b = OpenAICompatibleBackend(
            base_url="https://x.openai.azure.com/openai/deployments/d/chat/completions",
        )
        # ...should not be doubled.
        assert b._chat_url.count("/chat/completions") == 1


# ---------------------------------------------------------------------------
# Auth headers
# ---------------------------------------------------------------------------

class TestHeaders:
    def test_default_authorization_bearer(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "k")
        b = OpenAICompatibleBackend()
        h = b._build_headers()
        assert h["Authorization"] == "Bearer k"
        assert h["Content-Type"] == "application/json"

    def test_azure_style_api_key_header(self, monkeypatch):
        monkeypatch.setenv("AZURE_OPENAI_API_KEY", "azkey")
        b = azure_openai_backend(resource="my-res", deployment="gpt-4o")
        h = b._build_headers()
        assert h["api-key"] == "azkey"
        assert "Authorization" not in h

    def test_extra_headers_merge(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "k")
        b = OpenAICompatibleBackend(extra_headers={"OpenAI-Organization": "org-1"})
        h = b._build_headers()
        assert h["OpenAI-Organization"] == "org-1"


# ---------------------------------------------------------------------------
# Invoke happy / error paths
# ---------------------------------------------------------------------------

class TestInvoke:
    def test_happy_path(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "k")
        captured = {}
        def _fake_urlopen(req, timeout):
            captured["url"] = req.full_url
            captured["data"] = json.loads(req.data.decode())
            captured["headers"] = dict(req.header_items())
            return _FakeResp(_ok_chat_response('{"answer":42}'))
        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)

        b = OpenAICompatibleBackend(model="gpt-4o-mini")
        out = b.invoke("review_code", {"code": "print('hi')", "language": "python"})
        assert out == {"answer": 42}
        assert captured["url"].endswith("/chat/completions")
        assert captured["data"]["model"] == "gpt-4o-mini"
        assert captured["data"]["max_tokens"] > 0
        assert captured["data"]["response_format"] == {"type": "json_object"}
        assert any(k.lower() == "authorization" for k in captured["headers"])

    def test_response_format_json_can_be_disabled(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "k")
        seen = {}
        def _fake_urlopen(req, timeout):
            seen["payload"] = json.loads(req.data.decode())
            return _FakeResp(_ok_chat_response())
        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)

        b = OpenAICompatibleBackend(response_format_json=False)
        b.invoke("review_code", {"code": "x", "language": "python"})
        assert "response_format" not in seen["payload"]

    def test_unknown_tool(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "k")
        b = OpenAICompatibleBackend()
        with pytest.raises(BackendError):
            b.invoke("not_a_tool", {})

    def test_http_error_wrapped(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "k")
        import urllib.error
        def _boom(req, timeout):
            raise urllib.error.HTTPError(
                url=req.full_url, code=429, msg="rate limited",
                hdrs=None, fp=BytesIO(b"too many requests"),
            )
        monkeypatch.setattr("urllib.request.urlopen", _boom)
        b = OpenAICompatibleBackend()
        with pytest.raises(BackendError) as exc:
            b.invoke("review_code", {"code": "x", "language": "python"})
        assert "429" in str(exc.value)

    def test_url_error_wrapped(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "k")
        import urllib.error
        def _boom(req, timeout):
            raise urllib.error.URLError("connection refused")
        monkeypatch.setattr("urllib.request.urlopen", _boom)
        b = OpenAICompatibleBackend()
        with pytest.raises(BackendError):
            b.invoke("review_code", {"code": "x", "language": "python"})

    def test_response_too_large(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "k")
        big = b"x" * 1024
        def _fake_urlopen(req, timeout):
            return _FakeResp(big)
        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)

        b = OpenAICompatibleBackend(max_response_bytes=64)
        with pytest.raises(BackendError) as exc:
            b.invoke("review_code", {"code": "x", "language": "python"})
        assert "too_large" in str(exc.value)

    def test_malformed_response(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "k")
        def _fake_urlopen(req, timeout):
            return _FakeResp(b'{"not_choices": 1}')
        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
        b = OpenAICompatibleBackend()
        with pytest.raises(BackendError):
            b.invoke("review_code", {"code": "x", "language": "python"})

    def test_non_json_content_in_response(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "k")
        def _fake_urlopen(req, timeout):
            return _FakeResp(_ok_chat_response("not-json"))
        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
        b = OpenAICompatibleBackend()
        with pytest.raises(BackendError):
            b.invoke("review_code", {"code": "x", "language": "python"})


# ---------------------------------------------------------------------------
# Provider factories
# ---------------------------------------------------------------------------

class TestFactories:
    def test_openai_factory(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "k")
        b = openai_backend(model="gpt-4o-mini")
        assert b._chat_url == "https://api.openai.com/v1/chat/completions"
        assert b._model == "gpt-4o-mini"

    def test_openrouter_factory(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "k")
        b = openrouter_backend()
        assert b._chat_url.startswith("https://openrouter.ai/api/v1")

    def test_groq_factory(self, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "k")
        b = groq_backend()
        assert b._chat_url.startswith("https://api.groq.com/openai/v1")

    def test_together_factory(self, monkeypatch):
        monkeypatch.setenv("TOGETHER_API_KEY", "k")
        b = together_backend()
        assert b._chat_url.startswith("https://api.together.xyz/v1")

    def test_deepseek_factory(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
        b = deepseek_backend()
        assert b._chat_url.startswith("https://api.deepseek.com/v1")

    def test_mistral_factory(self, monkeypatch):
        monkeypatch.setenv("MISTRAL_API_KEY", "k")
        b = mistral_backend()
        assert b._chat_url.startswith("https://api.mistral.ai/v1")

    def test_azure_factory(self, monkeypatch):
        monkeypatch.setenv("AZURE_OPENAI_API_KEY", "k")
        b = azure_openai_backend(resource="my-res", deployment="gpt-4o")
        assert "my-res.openai.azure.com" in b._chat_url
        assert "deployments/gpt-4o" in b._chat_url
