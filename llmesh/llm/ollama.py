"""OllamaBackend — LLMBackend implementation using the Ollama HTTP API.

Uses urllib.request only (stdlib) — no additional dependencies.
All calls are non-shell, list-based, and fail-closed on any error.

Security invariants:
- shell=True is never used
- URL is never interpolated from user input
- Response is treated as untrusted until OutputValidator validates it
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from llmesh.security.http_limits import (
    DEFAULT_LLM_RESPONSE_BYTES,
    ResponseTooLargeError,
    read_capped,
)

from .backend import BackendError, LLMBackend
from .prompt import build_prompt

_DEFAULT_BASE_URL = "http://localhost:11434"
_DEFAULT_MODEL = "llama3.2:latest"
_DEFAULT_TIMEOUT = 120  # seconds — LLM inference can be slow


class OllamaBackend(LLMBackend):
    """Calls the local Ollama /api/chat endpoint to fulfil LLMesh tool requests."""

    def __init__(
        self,
        base_url: str = _DEFAULT_BASE_URL,
        model: str = _DEFAULT_MODEL,
        timeout: int = _DEFAULT_TIMEOUT,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout
        self._chat_url = f"{self._base_url}/api/chat"
        self._tags_url = f"{self._base_url}/api/tags"

    def health(self) -> bool:
        """Return True if Ollama is reachable at the configured base URL."""
        try:
            with urllib.request.urlopen(self._tags_url, timeout=5) as resp:  # nosec B310 - Ollama URL controlled by operator; response capped.
                return resp.status == 200
        except Exception:
            return False

    def invoke(self, tool_name: str, request_body: dict[str, Any]) -> dict[str, Any]:
        """Call Ollama /api/chat and parse the JSON response.

        Returns the parsed dict from the LLM response.
        Raises BackendError on any failure.
        """
        try:
            system_prompt, user_message = build_prompt(tool_name, request_body)
        except KeyError:
            raise BackendError(f"no_prompt_builder_for:{tool_name}")

        payload = {
            "model": self._model,
            "stream": False,
            "format": "json",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        }

        raw_bytes = json.dumps(payload).encode()
        req = urllib.request.Request(
            self._chat_url,
            data=raw_bytes,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # nosec B310 - Ollama URL controlled by operator; response capped.
                resp_bytes = read_capped(resp, max_bytes=DEFAULT_LLM_RESPONSE_BYTES)
        except ResponseTooLargeError as exc:
            raise BackendError(f"ollama_response_too_large:{exc.cap}") from exc
        except urllib.error.URLError as exc:
            raise BackendError(f"ollama_unreachable:{exc.reason}") from exc
        except TimeoutError as exc:
            raise BackendError("ollama_timeout") from exc

        try:
            ollama_resp = json.loads(resp_bytes)
        except json.JSONDecodeError as exc:
            raise BackendError(f"ollama_response_not_json:{exc}") from exc

        content = self._extract_content(ollama_resp)
        return self._parse_content(content)

    @staticmethod
    def _extract_content(ollama_resp: dict[str, Any]) -> str:
        """Pull the assistant message content out of an Ollama /api/chat response."""
        try:
            return ollama_resp["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise BackendError(f"unexpected_ollama_response_shape:{exc}") from exc

    @staticmethod
    def _parse_content(content: str) -> dict[str, Any]:
        """Parse the LLM content string as JSON.

        Ollama's format=json mode guarantees valid JSON, but we still
        treat parsing errors as BackendError (fail-closed).
        """
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise BackendError(f"llm_content_not_json:{exc}") from exc
        if not isinstance(parsed, dict):
            raise BackendError("llm_content_not_an_object")
        return parsed
