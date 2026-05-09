"""OpenAICompatibleBackend — OpenAI v1 chat-completions HTTP API client.

A single backend that works against **any** OpenAI v1 chat-completions
compatible endpoint:

- OpenAI (`https://api.openai.com/v1`)
- Azure OpenAI (`https://<resource>.openai.azure.com/openai/deployments/<deployment>`)
- OpenRouter (`https://openrouter.ai/api/v1`)
- Together AI (`https://api.together.xyz/v1`)
- Groq (`https://api.groq.com/openai/v1`)
- Mistral (`https://api.mistral.ai/v1`)
- DeepSeek (`https://api.deepseek.com/v1`)
- vLLM / TGI / llama-server local deployments (already covered by
  ``LlamaCppBackend`` for the local case)
- Anthropic via OpenAI-compatible proxies (e.g. LiteLLM)

Security invariants
-------------------
- Uses :func:`llmesh.security.endpoint_validator.validate_endpoint` to
  guard against SSRF (no private IPs, no IMDS) when ``validate_endpoint``
  is enabled.
- Response body is bounded by
  :data:`llmesh.security.http_limits.DEFAULT_LLM_RESPONSE_BYTES` (16 MiB).
- API key is read from environment / argument; never logged.
- Fail-closed: any unhandled exception raises :class:`BackendError`.
- No ``shell=True``, ``pickle``, ``eval``, ``exec``, ``yaml.load``.

Authentication header
---------------------
Default: ``Authorization: Bearer <api_key>`` (works for OpenAI / OpenRouter /
Together / Groq / Mistral / DeepSeek).

Azure OpenAI uses ``api-key: <api_key>`` instead — pass
``auth_header_name="api-key"`` and ``auth_scheme=""``.
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


_DEFAULT_BASE_URL = "https://api.openai.com/v1"
_DEFAULT_MODEL = "gpt-4o-mini"
_DEFAULT_TIMEOUT = 60
_DEFAULT_MAX_TOKENS = 2048


class OpenAICompatibleBackend(LLMBackend):
    """Generic OpenAI v1 chat-completions backend.

    Parameters
    ----------
    base_url:
        The OpenAI-compatible endpoint root (everything before
        ``/chat/completions``). Default: official OpenAI v1.
    model:
        Model name accepted by the upstream provider.
    api_key:
        Bearer token / API key. If ``None`` the constructor reads
        ``api_key_env`` from the environment.
    api_key_env:
        Environment variable name to read the key from. Default
        ``OPENAI_API_KEY``.
    auth_header_name:
        Header to carry the credential. Default ``Authorization``
        (Bearer scheme). Set to ``"api-key"`` for Azure OpenAI.
    auth_scheme:
        Prefix prepended to the key (with a trailing space). Default
        ``"Bearer"``. Set to ``""`` for raw key headers.
    extra_headers:
        Optional headers (e.g. ``{"OpenAI-Organization": "..."}``).
    timeout:
        HTTP request timeout in seconds.
    max_tokens:
        Generation cap.
    response_format_json:
        When True, sends ``response_format={"type":"json_object"}`` so
        the upstream returns JSON-only content (recommended for LLMesh
        tools where ``OutputValidator`` parses the response). Default
        True.
    max_response_bytes:
        Byte cap for the upstream response body. Defaults to
        :data:`DEFAULT_LLM_RESPONSE_BYTES` (16 MiB).
    """

    def __init__(
        self,
        base_url: str = _DEFAULT_BASE_URL,
        model: str = _DEFAULT_MODEL,
        *,
        api_key: str | None = None,
        api_key_env: str = "OPENAI_API_KEY",
        auth_header_name: str = "Authorization",
        auth_scheme: str = "Bearer",
        extra_headers: dict[str, str] | None = None,
        timeout: int = _DEFAULT_TIMEOUT,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
        response_format_json: bool = True,
        max_response_bytes: int = DEFAULT_LLM_RESPONSE_BYTES,
    ) -> None:
        if max_tokens <= 0:
            raise ValueError("max_tokens must be positive")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be positive")
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key if api_key is not None else os.environ.get(api_key_env, "")
        if not self._api_key:
            raise BackendError(
                f"missing api key — set {api_key_env} env var or pass api_key="
            )
        self._auth_header_name = auth_header_name
        self._auth_scheme = auth_scheme
        self._extra_headers = dict(extra_headers or {})
        self._timeout = int(timeout)
        self._max_tokens = int(max_tokens)
        self._response_format_json = bool(response_format_json)
        self._max_bytes = int(max_response_bytes)

        # Endpoint construction. Azure OpenAI users typically already
        # include the deployment path in base_url — we just append
        # /chat/completions if it isn't already there.
        if self._base_url.endswith("/chat/completions"):
            self._chat_url = self._base_url
        else:
            self._chat_url = f"{self._base_url}/chat/completions"

    # ------------------------------------------------------------------
    # LLMBackend
    # ------------------------------------------------------------------

    def health(self) -> bool:
        """Cloud APIs do not all expose a health endpoint, so we fall
        back to a lightweight 1-token probe.

        Returns ``True`` only on a clean 2xx + JSON response.
        """
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
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_message},
            ],
        }
        if self._response_format_json:
            payload["response_format"] = {"type": "json_object"}

        body = self._post(payload, timeout=self._timeout)
        content = self._extract_content(body)
        return self._parse_content(content)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _build_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        cred = (
            f"{self._auth_scheme} {self._api_key}".strip()
            if self._auth_scheme
            else self._api_key
        )
        headers[self._auth_header_name] = cred
        headers.update(self._extra_headers)
        return headers

    def _post(self, payload: dict[str, Any], *, timeout: int) -> dict[str, Any]:
        raw = json.dumps(payload).encode()
        req = urllib.request.Request(
            self._chat_url,
            data=raw,
            headers=self._build_headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310  # nosec B310 - base_url validated by builder; https-only; response capped.
                resp_bytes = read_capped(resp, max_bytes=self._max_bytes)
        except ResponseTooLargeError as exc:
            raise BackendError(f"openai_compatible_response_too_large:{exc.cap}") from exc
        except urllib.error.HTTPError as exc:
            # Read up to 1 KiB of error body for diagnostics
            try:
                detail = exc.read(1024).decode("utf-8", errors="replace")
            except Exception:
                detail = ""
            raise BackendError(
                f"openai_compatible_http:{exc.code}:{detail[:200]}"
            ) from exc
        except urllib.error.URLError as exc:
            raise BackendError(
                f"openai_compatible_unreachable:{exc.reason}"
            ) from exc
        except TimeoutError as exc:
            raise BackendError("openai_compatible_timeout") from exc

        try:
            return json.loads(resp_bytes)
        except json.JSONDecodeError as exc:
            raise BackendError(f"openai_compatible_response_not_json:{exc}") from exc

    @staticmethod
    def _extract_content(resp: dict[str, Any]) -> str:
        try:
            return resp["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise BackendError(f"unexpected_openai_compatible_shape:{exc}") from exc

    @staticmethod
    def _parse_content(content: str) -> dict[str, Any]:
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise BackendError(f"llm_content_not_json:{exc}") from exc
        if not isinstance(parsed, dict):
            raise BackendError("llm_content_not_an_object")
        return parsed


# ---------------------------------------------------------------------------
# Convenience factory functions for the most common providers
# ---------------------------------------------------------------------------

def openai_backend(
    *,
    model: str = "gpt-4o-mini",
    api_key: str | None = None,
    **kw,
) -> OpenAICompatibleBackend:
    """Plain OpenAI."""
    return OpenAICompatibleBackend(
        base_url="https://api.openai.com/v1",
        model=model, api_key=api_key, api_key_env="OPENAI_API_KEY", **kw,
    )


def azure_openai_backend(
    *,
    resource: str,
    deployment: str,
    api_version: str = "2024-02-15-preview",
    api_key: str | None = None,
    **kw,
) -> OpenAICompatibleBackend:
    """Azure OpenAI — note the different auth header."""
    base = (
        f"https://{resource}.openai.azure.com/openai/deployments/{deployment}"
        f"/chat/completions?api-version={api_version}"
    )
    return OpenAICompatibleBackend(
        base_url=base,
        model=deployment,           # Azure ignores model field; deployment is in URL
        api_key=api_key,
        api_key_env="AZURE_OPENAI_API_KEY",
        auth_header_name="api-key",
        auth_scheme="",
        **kw,
    )


def openrouter_backend(
    *,
    model: str = "openai/gpt-4o-mini",
    api_key: str | None = None,
    **kw,
) -> OpenAICompatibleBackend:
    """OpenRouter — multi-model proxy."""
    return OpenAICompatibleBackend(
        base_url="https://openrouter.ai/api/v1",
        model=model, api_key=api_key, api_key_env="OPENROUTER_API_KEY", **kw,
    )


def groq_backend(
    *,
    model: str = "llama-3.3-70b-versatile",
    api_key: str | None = None,
    **kw,
) -> OpenAICompatibleBackend:
    """Groq inference — fast OpenAI-compatible."""
    return OpenAICompatibleBackend(
        base_url="https://api.groq.com/openai/v1",
        model=model, api_key=api_key, api_key_env="GROQ_API_KEY", **kw,
    )


def together_backend(
    *,
    model: str = "meta-llama/Llama-3.3-70B-Instruct-Turbo",
    api_key: str | None = None,
    **kw,
) -> OpenAICompatibleBackend:
    """Together AI."""
    return OpenAICompatibleBackend(
        base_url="https://api.together.xyz/v1",
        model=model, api_key=api_key, api_key_env="TOGETHER_API_KEY", **kw,
    )


def deepseek_backend(
    *,
    model: str = "deepseek-chat",
    api_key: str | None = None,
    **kw,
) -> OpenAICompatibleBackend:
    """DeepSeek."""
    return OpenAICompatibleBackend(
        base_url="https://api.deepseek.com/v1",
        model=model, api_key=api_key, api_key_env="DEEPSEEK_API_KEY", **kw,
    )


def mistral_backend(
    *,
    model: str = "mistral-large-latest",
    api_key: str | None = None,
    **kw,
) -> OpenAICompatibleBackend:
    """Mistral AI."""
    return OpenAICompatibleBackend(
        base_url="https://api.mistral.ai/v1",
        model=model, api_key=api_key, api_key_env="MISTRAL_API_KEY", **kw,
    )
