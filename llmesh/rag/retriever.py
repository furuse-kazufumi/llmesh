"""High-level retriever — wires Embedder + VectorStore + privacy gate.

Index path
----------
``Retriever.index(doc_id, text, metadata=...)`` runs the optional
``PromptFirewall`` over the text. If the firewall says ``BLOCK`` the
document is **not** indexed. ``SUMMARIZE`` is a soft warning logged to
the audit trace (if configured) but the original text is still indexed
because the retrieval gate (below) will summarize it on read.

Retrieval path
--------------
``Retriever.retrieve(query, top_k)`` embeds the query, runs cosine
search over the store, and returns up to ``top_k`` results. Each result
goes through the firewall again — anything that scores BLOCK is
dropped, SUMMARIZE results are tagged so the caller can route them
through ``PrivacySummarizer`` before forwarding to the LLM.

The retriever is safe to use without a firewall (``firewall=None``); in
that case all documents pass through unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .embedder import Embedder
from .store import Document, VectorStore


@dataclass(frozen=True)
class RetrievalResult:
    """A single retrieval hit annotated with the firewall verdict."""

    document: Document
    score: float
    action: str = "ALLOW"     # "ALLOW" | "SUMMARIZE" | "BLOCK"
    reason: str = ""

    @property
    def allowed(self) -> bool:
        return self.action == "ALLOW"

    @property
    def requires_summarization(self) -> bool:
        return self.action == "SUMMARIZE"


class Retriever:
    """Embed → search → privacy-gate retrieval pipeline."""

    def __init__(
        self,
        store: VectorStore,
        embedder: Embedder,
        firewall=None,
    ) -> None:
        if store.dimension != embedder.dimension:
            raise ValueError(
                f"embedder/store dimension mismatch: "
                f"embedder={embedder.dimension}, store={store.dimension}"
            )
        self._store = store
        self._embedder = embedder
        self._firewall = firewall

    @property
    def store(self) -> VectorStore:
        return self._store

    @property
    def embedder(self) -> Embedder:
        return self._embedder

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------

    def index(
        self,
        doc_id: str,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Index a document. Returns False when the firewall blocks it."""
        if self._firewall is not None:
            decision = self._firewall.classify(text)
            if decision.blocked:
                return False
        vector = self._embedder.embed(text)
        self._store.add(Document(
            doc_id=doc_id,
            text=text,
            vector=tuple(vector),
            metadata=metadata or {},
        ))
        return True

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        *,
        drop_blocked: bool = True,
    ) -> list[RetrievalResult]:
        """Embed the query, search, and apply the firewall to each hit.

        With ``drop_blocked=True`` (default) any BLOCK result is omitted.
        With ``drop_blocked=False`` BLOCK results are still returned but
        flagged so the caller can audit them.
        """
        # Allow the firewall to inspect the query first — if the query
        # itself is hostile (prompt injection), refuse to retrieve.
        if self._firewall is not None:
            q_decision = self._firewall.classify(query)
            if q_decision.blocked:
                return []

        q_vec = self._embedder.embed(query)
        hits = self._store.search(q_vec, top_k=top_k)

        out: list[RetrievalResult] = []
        for hit in hits:
            if self._firewall is None:
                out.append(RetrievalResult(
                    document=hit.document,
                    score=hit.score,
                    action="ALLOW",
                    reason="no_firewall",
                ))
                continue
            decision = self._firewall.classify(hit.document.text)
            if decision.blocked and drop_blocked:
                continue
            out.append(RetrievalResult(
                document=hit.document,
                score=hit.score,
                action=decision.action,
                reason=decision.reason,
            ))
        return out
