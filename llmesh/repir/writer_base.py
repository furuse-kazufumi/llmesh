"""Shared base for RepIR writers — capability negotiation with graceful degrade.

A *writer* turns a :class:`~llmesh.repir.model.Document` into one concrete
representation (Markdown, SVG, TUI, …).  All writers share one rule, modelled on
glTF / General-MIDI capability negotiation:

- A writer declares the extension names it understands in ``supported_extensions``.
- Before rendering, it checks the document's ``extensions_required``.  If the
  document *requires* an extension the writer cannot honour, the writer **refuses**
  (raises :class:`~llmesh.repir.model.RepIRCapabilityError`) — fail-closed, never a
  silent wrong render.
- *Non-required* extensions a writer does not understand are simply ignored
  (graceful degrade).

The Markdown writer is the floor of this lattice: it understands no extensions, so
it refuses any document that *requires* one, but renders every core node.  Richer
writers (SVG/TUI) opt into extensions as they gain support.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from .model import Document, Node, RepIRCapabilityError


class Writer(ABC):
    """Base class for RepIR renderers.

    Subclasses set :attr:`supported_extensions` (default: none) and implement
    :meth:`render`.  Call :meth:`check_capabilities` first.
    """

    #: Extension names this writer can honour.  Empty = core-only (Markdown floor).
    supported_extensions: frozenset[str] = frozenset()

    #: Short human-readable format name, e.g. "markdown".
    format_name: str = "abstract"

    def check_capabilities(self, doc: Document) -> None:
        """Fail-closed gate: refuse documents requiring unsupported extensions."""
        unmet = set(doc.extensions_required) - set(self.supported_extensions)
        if unmet:
            raise RepIRCapabilityError(
                f"{self.format_name} writer cannot satisfy required extensions "
                f"{sorted(unmet)} (supports {sorted(self.supported_extensions)})"
            )

    @abstractmethod
    def render(self, doc: Document) -> str:
        """Render *doc* to this writer's representation.

        Implementations must call :meth:`check_capabilities` before producing
        output and :meth:`Document.validate` is the caller's responsibility (or the
        writer may call it defensively).
        """
        raise NotImplementedError

    # -- convenience --------------------------------------------------------

    def render_node(self, node: Node) -> str:
        """Render a single detached node (wraps it in a minimal document)."""
        return self.render(Document.of(node))
