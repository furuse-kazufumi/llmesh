"""Text → dense vector embedders for the RAG module.

Two implementations are shipped:

- ``MockEmbedder``  — deterministic hash-based embedding for tests and
                      offline use. No external dependencies.
- ``OllamaEmbedder`` — Ollama's ``/api/embeddings`` HTTP endpoint. Uses
                      ``urllib`` (stdlib) so it carries no extra deps.

Both produce L2-normalized vectors so cosine similarity reduces to a
plain dot product in the retriever.
"""
from __future__ import annotations

import hashlib
import json
import math
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from typing import Sequence


from llmesh.security.http_limits import (
    DEFAULT_MAX_RESPONSE_BYTES,
    ResponseTooLargeError,
    read_capped,
)


class EmbeddingError(RuntimeError):
    """Raised when an embedder cannot produce a vector."""


# Cap the response size we accept from a single embedding call. A
# legitimate embedding for ``dim ≤ 4096`` floats is ≤ 64 KiB; 1 MiB
# leaves plenty of headroom while preventing a hostile / runaway
# backend from filling memory.
_MAX_EMBED_RESPONSE_BYTES = DEFAULT_MAX_RESPONSE_BYTES


def _l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0.0:
        return vec
    return [v / norm for v in vec]


class Embedder(ABC):
    """Common embedder interface."""

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Output vector dimension."""

    @abstractmethod
    def embed(self, text: str) -> list[float]:
        """Embed a single text. Returns an L2-normalized vector."""

    def embed_many(self, texts: Sequence[str]) -> list[list[float]]:
        """Default batch implementation — subclasses can override for efficiency."""
        return [self.embed(t) for t in texts]


# ---------------------------------------------------------------------------
# Deterministic hash-based embedder (test / offline default)
# ---------------------------------------------------------------------------

class MockEmbedder(Embedder):
    """Deterministic hash embedding suitable for tests and demos.

    The embedding is built from token-level SHA-256 digests folded into a
    fixed-dimension vector. Identical text always produces the same vector;
    similar text shares many components, so cosine similarity is meaningful
    enough for unit tests but should not be used for production retrieval.
    """

    def __init__(self, dimension: int = 64) -> None:
        if dimension <= 0:
            raise ValueError("dimension must be positive")
        self._dim = int(dimension)

    @property
    def dimension(self) -> int:
        return self._dim

    def embed(self, text: str) -> list[float]:
        if not isinstance(text, str):
            raise EmbeddingError("text must be a str")
        vec = [0.0] * self._dim
        tokens = text.lower().split() or [""]
        for tok in tokens:
            digest = hashlib.sha256(tok.encode("utf-8")).digest()
            for i, b in enumerate(digest):
                vec[i % self._dim] += (b - 127.5) / 128.0
        return _l2_normalize(vec)


# ---------------------------------------------------------------------------
# Ollama embedder (HTTP, stdlib-only)
# ---------------------------------------------------------------------------

class OllamaEmbedder(Embedder):
    """Embedder backed by Ollama's ``/api/embeddings`` endpoint.

    Parameters
    ----------
    model:
        The Ollama embedding model (e.g. ``"nomic-embed-text"``,
        ``"mxbai-embed-large"``).
    host:
        Base URL of the Ollama daemon. Defaults to
        ``http://127.0.0.1:11434``.
    timeout:
        Per-request timeout in seconds. Defaults to ``30``.
    dimension:
        Optional dimension hint. When omitted, the first ``embed`` call
        infers it from the response and caches it.
    """

    def __init__(
        self,
        model: str,
        host: str = "http://127.0.0.1:11434",
        *,
        timeout: float = 30.0,
        dimension: int | None = None,
    ) -> None:
        self._model = str(model)
        self._host = host.rstrip("/")
        self._timeout = float(timeout)
        self._dim = dimension

    @property
    def dimension(self) -> int:
        if self._dim is None:
            # Force an embed of a single token to discover the dimension.
            self.embed(".")
            assert self._dim is not None
        return self._dim

    def embed(self, text: str) -> list[float]:
        if not isinstance(text, str):
            raise EmbeddingError("text must be a str")
        url = f"{self._host}/api/embeddings"
        body = json.dumps({"model": self._model, "prompt": text}).encode("utf-8")
        req = urllib.request.Request(  # noqa: S310 — fixed local URL, no shell
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # noqa: S310
                raw = read_capped(resp, max_bytes=_MAX_EMBED_RESPONSE_BYTES)
                payload = json.loads(raw.decode("utf-8"))
        except ResponseTooLargeError as exc:
            raise EmbeddingError(f"ollama embed failed: {exc}") from exc
        except (urllib.error.URLError, json.JSONDecodeError, OSError) as exc:
            raise EmbeddingError(f"ollama embed failed: {exc}") from exc

        vec = payload.get("embedding")
        if not isinstance(vec, list) or not all(isinstance(v, (int, float)) for v in vec):
            raise EmbeddingError("ollama returned malformed embedding")

        if self._dim is None:
            self._dim = len(vec)
        elif len(vec) != self._dim:
            raise EmbeddingError(
                f"embedding dimension mismatch: got {len(vec)}, expected {self._dim}"
            )

        return _l2_normalize([float(v) for v in vec])
