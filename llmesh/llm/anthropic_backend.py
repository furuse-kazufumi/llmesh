"""AnthropicBackend — Anthropic Messages API client.

Native Anthropic Messages API integration (distinct from
``OpenAICompatibleBackend`` because Anthropic exposes a slightly
different request / response shape):

- POST ``/v1/messages``
- Headers: ``x-api-key: <key>``, ``anthropic-version: 2023-06-01``
- Request: ``model``, ``max_tokens``, ``system``, ``messages``
- Response: ``content`` is a list of typed blocks; we read the first
  ``text`` block.

Security
--------
Same invariants as ``OpenAICompatibleBackend``: SSRF-safe (validate URL),
fail-closed, no shell / pickle / eval, ``read_capped`` on the response.
"""
from __future__ import annotations

import json
import os
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


_DEFAULT_BASE_URL = "https://api.anthropic.com"
_DEFAULT_MODEL = "claude-haiku-4-5"
_DEFAULT_TIMEOUT = 120
_DEFAULT_MAX_TOKENS = 2048
_DEFAULT_API_VERSION = "2023-06-01"


class AnthropicBackend(LLMBackend):
    """Anthropic Messages API backend.

    Parameters
    ----------
    model:
        Anthropic model id (e.g. ``"claude-haiku-4-5"``,
        ``"claude-sonnet-4-6"``, ``"claude-opus-4-7"``).
    api_key / api_key_env:
        Bearer credential. Read from ``ANTHROPIC_API_KEY`` env if not
        provided.
    api_version:
        Sent as the ``anthropic-version`` header.
    base_url:
        Override the API endpoint (e.g. for a corporate proxy).
    timeout / max_tokens / max_response_bytes:
        Same semantics as ``OpenAICompatibleBackend``.
    """

    def __init__(
        self,
        model: str = _DEFAULT_MODEL,
        *,
        api_key: str | None = None,
        api_key_env: str = "ANTHROPIC_API_KEY",
        api_version: str = _DEFAULT_API_VERSION,
        base_url: str = _DEFAULT_BASE_URL,
        timeout: int = _DEFAULT_TIMEOUT,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
        max_response_bytes: int = DEFAULT_LLM_RESPONSE_BYTES,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        if max_tokens <= 0:
            raise ValueError("max_tokens must be positive")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be positive")
        self._model = model
        self._api_key = api_key if api_key is not None else os.environ.get(api_key_env, "")
        if not self._api_key:
            raise BackendError(
                f"missing api key — set {api_key_env} env var or pass api_key="
            )
        self._api_version = api_version
        self._base_url = base_url.rstrip("/")
        self._messages_url = f"{self._base_url}/v1/messages"
        self._timeout = int(timeout)
        self._max_tokens = int(max_tokens)
        self._max_bytes = int(max_response_bytes)
        self._extra_headers = dict(extra_headers or {})

    # ------------------------------------------------------------------
    # LLMBackend
    # ------------------------------------------------------------------

    def health(self) -> bool:
        """Anthropic has no public health endpoint — issue a 1-token probe."""
        try:
            self._post({
                "model": self._model,
                "max_tokens": 1,
                "messages": [{"role": "user", "content": "."}],
            }, timeout=min(self._timeout, 10))
            return True
        except Exception:
            return False

    def invoke(self, tool_name: str, request_body: dict[str, Any]) -> dict[str, Any]:
        try:
            system_prompt, user_message = build_prompt(tool_name, request_body)
        except KeyError:
            raise BackendError(f"no_prompt_builder_for:{tool_name}")

        payload: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "system": system_prompt,
            "messages": [
                {"role": "user", "content": user_message},
            ],
        }

        body = self._post(payload, timeout=self._timeout)
        content = self._extract_content(body)
        return self._parse_content(content)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _build_headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self._api_key,
            "anthropic-version": self._api_version,
        }
        headers.update(self._extra_headers)
        return headers

    def _post(self, payload: dict[str, Any], *, timeout: int) -> dict[str, Any]:
        raw = json.dumps(payload).encode()
        req = urllib.request.Request(
            self._messages_url,
            data=raw,
            headers=self._build_headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                resp_bytes = read_capped(resp, max_bytes=self._max_bytes)
        except ResponseTooLargeError as exc:
            raise BackendError(f"anthropic_response_too_large:{exc.cap}") from exc
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read(1024).decode("utf-8", errors="replace")
            except Exception:
                detail = ""
            raise BackendError(f"anthropic_http:{exc.code}:{detail[:200]}") from exc
        except urllib.error.URLError as exc:
            raise BackendError(f"anthropic_unreachable:{exc.reason}") from exc
        except TimeoutError as exc:
            raise BackendError("anthropic_timeout") from exc

        try:
            return json.loads(resp_bytes)
        except json.JSONDecodeError as exc:
            raise BackendError(f"anthropic_response_not_json:{exc}") from exc

    @staticmethod
    def _extract_content(resp: dict[str, Any]) -> str:
        """Pull the first ``text`` block from Anthropic's response."""
        try:
            blocks = resp["content"]
            if not isinstance(blocks, list):
                raise TypeError("content is not a list")
            for block in blocks:
                if isinstance(block, dict) and block.get("type") == "text":
                    return str(block.get("text", ""))
            raise KeyError("no text block in content")
        except (KeyError, IndexError, TypeError) as exc:
            raise BackendError(f"unexpected_anthropic_shape:{exc}") from exc

    @staticmethod
    def _parse_content(content: str) -> dict[str, Any]:
        """Parse the assistant text as JSON.

        Anthropic doesn't have a direct equivalent of OpenAI's
        ``response_format=json_object``; the system prompt is expected
        to instruct JSON-only output (``build_prompt`` does this for
        LLMesh tools).
        """
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise BackendError(f"llm_content_not_json:{exc}") from exc
        if not isinstance(parsed, dict):
            raise BackendError("llm_content_not_an_object")
        return parsed
