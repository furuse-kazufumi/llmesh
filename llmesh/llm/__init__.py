"""LLM backend abstraction layer for LLMesh nodes.

Backends fall into two families:

- **Local** (no external network, no API key):
  ``OllamaBackend`` (``http://localhost:11434``),
  ``LlamaCppBackend`` (llama.cpp ``llama-server``).
- **Cloud / hosted** (require API key, hit external HTTPS):
  ``OpenAICompatibleBackend`` (OpenAI / Azure / OpenRouter / Together /
  Groq / Mistral / DeepSeek), ``AnthropicBackend`` (Anthropic Messages
  API).

All backends implement the same :class:`LLMBackend` interface so the
rest of LLMesh (privacy pipeline, OutputValidator, audit) is backend-
agnostic. Custom backends can subclass :class:`LLMBackend` and follow
the same fail-closed conventions (see ``docs/DEVELOPMENT.md``).
"""
from .anthropic_backend import AnthropicBackend
from .backend import BackendError, LLMBackend
from .llamacpp import LlamaCppBackend
from .ollama import OllamaBackend
from .openai_compatible import (
    OpenAICompatibleBackend,
    azure_openai_backend,
    deepseek_backend,
    groq_backend,
    mistral_backend,
    openai_backend,
    openrouter_backend,
    together_backend,
)

__all__ = [
    "BackendError",
    "LLMBackend",
    # Local
    "OllamaBackend",
    "LlamaCppBackend",
    # Cloud
    "OpenAICompatibleBackend",
    "AnthropicBackend",
    # Provider factory functions
    "openai_backend",
    "azure_openai_backend",
    "openrouter_backend",
    "groq_backend",
    "together_backend",
    "deepseek_backend",
    "mistral_backend",
]
