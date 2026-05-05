"""Tests for environment-variable-driven LLM backend selection in server.py."""
from __future__ import annotations

import pytest

from llmesh.llm.llamacpp import LlamaCppBackend
from llmesh.llm.ollama import OllamaBackend
from llmesh.mcp.server import _select_backend


class TestSelectBackend:
    def test_default_is_ollama(self, monkeypatch):
        monkeypatch.delenv("LLMESH_BACKEND", raising=False)
        monkeypatch.delenv("LLMESH_BACKEND_URL", raising=False)
        monkeypatch.delenv("LLMESH_MODEL", raising=False)
        assert isinstance(_select_backend(), OllamaBackend)

    def test_ollama_explicit(self, monkeypatch):
        monkeypatch.setenv("LLMESH_BACKEND", "ollama")
        assert isinstance(_select_backend(), OllamaBackend)

    def test_llamacpp_selected(self, monkeypatch):
        monkeypatch.setenv("LLMESH_BACKEND", "llamacpp")
        monkeypatch.delenv("LLMESH_BACKEND_URL", raising=False)
        monkeypatch.delenv("LLMESH_MODEL", raising=False)
        assert isinstance(_select_backend(), LlamaCppBackend)

    def test_unknown_backend_falls_back_to_ollama(self, monkeypatch):
        monkeypatch.setenv("LLMESH_BACKEND", "unknown_backend")
        assert isinstance(_select_backend(), OllamaBackend)

    def test_custom_url_applied_to_ollama(self, monkeypatch):
        monkeypatch.delenv("LLMESH_BACKEND", raising=False)
        monkeypatch.setenv("LLMESH_BACKEND_URL", "http://my-ollama:11434")
        monkeypatch.delenv("LLMESH_MODEL", raising=False)
        backend = _select_backend()
        assert isinstance(backend, OllamaBackend)
        assert backend._base_url == "http://my-ollama:11434"

    def test_custom_url_applied_to_llamacpp(self, monkeypatch):
        monkeypatch.setenv("LLMESH_BACKEND", "llamacpp")
        monkeypatch.setenv("LLMESH_BACKEND_URL", "http://my-llama:8080")
        monkeypatch.delenv("LLMESH_MODEL", raising=False)
        backend = _select_backend()
        assert isinstance(backend, LlamaCppBackend)
        assert backend._base_url == "http://my-llama:8080"

    def test_custom_model_applied_to_ollama(self, monkeypatch):
        monkeypatch.delenv("LLMESH_BACKEND", raising=False)
        monkeypatch.delenv("LLMESH_BACKEND_URL", raising=False)
        monkeypatch.setenv("LLMESH_MODEL", "mistral:7b")
        backend = _select_backend()
        assert isinstance(backend, OllamaBackend)
        assert backend._model == "mistral:7b"

    def test_custom_model_applied_to_llamacpp(self, monkeypatch):
        monkeypatch.setenv("LLMESH_BACKEND", "llamacpp")
        monkeypatch.delenv("LLMESH_BACKEND_URL", raising=False)
        monkeypatch.setenv("LLMESH_MODEL", "mistral-7b")
        backend = _select_backend()
        assert isinstance(backend, LlamaCppBackend)
        assert backend._model == "mistral-7b"

    def test_url_trailing_slash_stripped_ollama(self, monkeypatch):
        monkeypatch.delenv("LLMESH_BACKEND", raising=False)
        monkeypatch.setenv("LLMESH_BACKEND_URL", "http://my-ollama:11434/")
        monkeypatch.delenv("LLMESH_MODEL", raising=False)
        backend = _select_backend()
        assert backend._base_url == "http://my-ollama:11434"

    def test_url_trailing_slash_stripped_llamacpp(self, monkeypatch):
        monkeypatch.setenv("LLMESH_BACKEND", "llamacpp")
        monkeypatch.setenv("LLMESH_BACKEND_URL", "http://my-llama:8080/")
        monkeypatch.delenv("LLMESH_MODEL", raising=False)
        backend = _select_backend()
        assert backend._base_url == "http://my-llama:8080"
