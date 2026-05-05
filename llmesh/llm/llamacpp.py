"""LlamaCppBackend — LLMBackend using llama.cpp server's OpenAI-compatible API.

llama-server exposes:
  GET  /health                  → {"status": "ok"}
  POST /v1/chat/completions     → OpenAI-compatible response

Default base URL: http://localhost:8080 (llama-server default port).

Security invariants:
- shell=True is never used
- URL is never interpolated from user input
- Response is treated as untrusted until OutputValidator validates it
- No eval, exec, pickle, yaml.load(unsafe)
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from .backend import BackendError, LLMBackend
from .prompt import build_prompt

_DEFAULT_BASE_URL = "http://localhost:8080"
_DEFAULT_MODEL = "local"        # llama-server ignores this if only one model is loaded
_DEFAULT_TIMEOUT = 120          # seconds — inference can be slow
_DEFAULT_MAX_TOKENS = 2048


class LlamaCppBackend(LLMBackend):
    """Calls llama-server (llama.cpp) via the OpenAI-compatible chat endpoint.

    Args:
        base_url:   llama-server base URL (default: http://localhost:8080).
        model:      Model name sent in the request body (often ignored by server).
        timeout:    HTTP request timeout in seconds.
        max_tokens: Maximum tokens to generate per request.
    """

    def __init__(
        self,
        base_url: str = _DEFAULT_BASE_URL,
        model: str = _DEFAULT_MODEL,
        timeout: int = _DEFAULT_TIMEOUT,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout
        self._max_tokens = max_tokens
        self._chat_url = f"{self._base_url}/v1/chat/completions"
        self._health_url = f"{self._base_url}/health"

    def health(self) -> bool:
        """Return True if llama-server is reachable at the configured base URL."""
        try:
            with urllib.request.urlopen(self._health_url, timeout=5) as resp:
                if resp.status != 200:
                    return False
                data = json.loads(resp.read())
                # llama-server returns {"status": "ok"} when ready
                return data.get("status") == "ok"
        except Exception:
            return False

    def invoke(self, tool_name: str, request_body: dict[str, Any]) -> dict[str, Any]:
        """Call llama-server /v1/chat/completions and parse the JSON response.

        Returns the parsed dict from the LLM response.
        Raises BackendError on any failure.
        """
        try:
            system_prompt, user_message = build_prompt(tool_name, request_body)
        except KeyError:
            raise BackendError(f"no_prompt_builder_for:{tool_name}")

        payload = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "response_format": {"type": "json_object"},  # JSON mode
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_message},
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
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                resp_bytes = resp.read()
        except urllib.error.URLError as exc:
            raise BackendError(f"llamacpp_unreachable:{exc.reason}") from exc
        except TimeoutError as exc:
            raise BackendError("llamacpp_timeout") from exc

        try:
            openai_resp = json.loads(resp_bytes)
        except json.JSONDecodeError as exc:
            raise BackendError(f"llamacpp_response_not_json:{exc}") from exc

        content = self._extract_content(openai_resp)
        return self._parse_content(content)

    @staticmethod
    def _extract_content(openai_resp: dict[str, Any]) -> str:
        """Pull the assistant message content from an OpenAI-format response."""
        try:
            return openai_resp["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise BackendError(f"unexpected_llamacpp_response_shape:{exc}") from exc

    @staticmethod
    def _parse_content(content: str) -> dict[str, Any]:
        """Parse the LLM content string as JSON (fail-closed on bad JSON)."""
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise BackendError(f"llm_content_not_json:{exc}") from exc
        if not isinstance(parsed, dict):
            raise BackendError("llm_content_not_an_object")
        return parsed
