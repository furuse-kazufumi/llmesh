# SPDX-License-Identifier: Apache-2.0
"""LlcoreBackend — run FullSense **llcore**'s on-prem CPU chat model as an llmesh LLM backend.

This wires llcore (the ll- family's local Transformer-core runtime) into llmesh's pluggable
``LLMBackend`` interface, so llmesh can serve a fully on-prem, CPU-only small model
(SmolLM2 / Qwen2.5, Apache-2.0) through the same path as the Ollama / llama.cpp backends.

llcore is an **optional dependency**: if it (or torch/transformers) is not installed, ``health()``
returns ``False`` and ``invoke()`` raises :class:`BackendError` — fail-closed, never a silent mock
(mirrors llcore's own ``ChatDependencyError`` discipline).

Honest scope: llcore's default model is a *small* CPU model. Free-text chat works (see ``chat()``);
llmesh's structured *tools* require schema-valid JSON, which a tiny model may not emit — in that case
``invoke()`` raises ``BackendError('llcore_non_json_output:...')`` rather than returning junk.
"""
from __future__ import annotations

import json
from typing import Any

from llmesh.llm.backend import BackendError, LLMBackend
from llmesh.llm.prompt import build_prompt


class LlcoreBackend(LLMBackend):
    """llmesh backend that delegates generation to llcore's on-prem CPU chat runtime."""

    def __init__(
        self,
        model_id: str | None = None,
        max_new_tokens: int = 256,
        temperature: float = 0.0,
    ) -> None:
        self._model_id = model_id
        self._max_new_tokens = max_new_tokens
        self._temperature = temperature
        self._backend: Any = None  # lazy llcore TransformersBackend

    def _ensure_backend(self) -> Any:
        if self._backend is None:
            from llcore.chat.backend import TransformersBackend  # optional dep, lazy
            self._backend = TransformersBackend(self._model_id)
        return self._backend

    def chat(self, system_prompt: str, user_message: str) -> str:
        """Free-text round-trip through llcore (the honest chat path; returns raw model text)."""
        from llcore.chat.session import GenerationSettings, Message

        backend = self._ensure_backend()
        messages = [
            Message(role="system", content=system_prompt),
            Message(role="user", content=user_message),
        ]
        do_sample = self._temperature > 0.0
        settings = GenerationSettings(
            max_new_tokens=self._max_new_tokens,
            temperature=self._temperature if do_sample else 0.3,
            do_sample=do_sample,
        )
        return backend.generate(messages, settings)

    def invoke(self, tool_name: str, request_body: dict[str, Any]) -> dict[str, Any]:
        try:
            system_prompt, user_message = build_prompt(tool_name, request_body)
        except KeyError as exc:
            raise BackendError(f"no_prompt_builder_for:{tool_name}") from exc
        try:
            text = self.chat(system_prompt, user_message)
        except BackendError:
            raise
        except Exception as exc:  # noqa: BLE001 - llcore load/generate failure -> fail-closed
            raise BackendError(f"llcore_generate_failed:{exc}") from exc
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise BackendError(f"llcore_non_json_output:{exc}") from exc
        if not isinstance(parsed, dict):
            raise BackendError(f"llcore_output_not_object:{type(parsed).__name__}")
        return parsed

    def health(self) -> bool:
        """True if llcore (and its torch/transformers deps) can be loaded — real, not a ping."""
        try:
            self._ensure_backend()
            return True
        except Exception:  # noqa: BLE001 - any import/construct failure = unhealthy
            return False
