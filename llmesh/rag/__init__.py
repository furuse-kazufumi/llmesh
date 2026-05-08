"""RAG (Retrieval-Augmented Generation) module — F-1 (v3.0+).

Local vector index + retrieval pipeline that respects the existing
LLMesh privacy stack:

- ``Embedder``      — turn text into a dense vector.
- ``VectorStore``   — store and search those vectors.
- ``Retriever``     — top-k similarity search wrapped with PromptFirewall
                      (block on index, summarize on retrieve).

The default backend is a pure-numpy in-memory store
(``llmesh.rag.numpy_store.NumpyVectorStore``) that persists to ``.npz``.
It depends only on numpy (already an ``industrial`` extra) and is
suitable for tens of thousands of documents. Larger deployments can
swap in an alternative ``VectorStore`` implementation without touching
the rest of the stack.

Note
----
The numpy-backed store is loaded lazily so that simply importing
``llmesh.rag`` does not pull numpy. Install the ``rag`` extra
(``pip install llmesh[rag]``) before using ``NumpyVectorStore``, or
import it directly via ``from llmesh.rag.numpy_store import
NumpyVectorStore``.
"""
from .embedder import Embedder, MockEmbedder, OllamaEmbedder, EmbeddingError
from .sqlite_store import SqliteVectorStore
from .store import Document, RetrievedDocument, VectorStore
from .retriever import Retriever, RetrievalResult


def __getattr__(name):
    """Lazy import for numpy-backed concrete stores."""
    if name == "NumpyVectorStore":
        from .numpy_store import NumpyVectorStore
        return NumpyVectorStore
    if name == "LSHVectorStore":
        from .lsh_store import LSHVectorStore
        return LSHVectorStore
    raise AttributeError(f"module 'llmesh.rag' has no attribute {name!r}")


__all__ = [
    "Embedder",
    "MockEmbedder",
    "OllamaEmbedder",
    "EmbeddingError",
    "Document",
    "RetrievedDocument",
    "VectorStore",
    "NumpyVectorStore",   # numpy lazy-import — exact in-memory cosine
    "SqliteVectorStore",  # stdlib durable backend — single-file, ≤10⁶ rows
    "LSHVectorStore",     # numpy lazy-import — approximate NN for ≥10⁶ rows
    "Retriever",
    "RetrievalResult",
]
