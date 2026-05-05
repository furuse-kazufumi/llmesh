"""LLMBackend ABC — common interface for all LLM execution backends.

Security invariants (enforced by callers, not backends):
- Backends must never use shell=True, pickle, eval, exec, or yaml.load(unsafe)
- All HTTP calls must use list-based args or urllib — no shell interpolation
- Backend responses are treated as untrusted until OutputValidator clears them
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BackendError(Exception):
    """Raised when an LLM backend fails to produce a usable response."""


class LLMBackend(ABC):
    """Common interface for LLM execution backends (Ollama, llama.cpp, etc.)."""

    @abstractmethod
    def invoke(self, tool_name: str, request_body: dict[str, Any]) -> dict[str, Any]:
        """Call the LLM and return a raw (unvalidated) response dict.

        The caller (server.py) is responsible for passing the result through
        OutputValidator before returning it to clients.

        Args:
            tool_name: One of the registered TOOL_SCHEMAS keys.
            request_body: The parsed JSON request body from the caller.

        Returns:
            A dict that should satisfy the tool's output schema.

        Raises:
            BackendError: On connectivity failure, timeout, or non-JSON response.
        """

    @abstractmethod
    def health(self) -> bool:
        """Return True if the backend is reachable."""
