"""RepIR — typed Representation IR ("LLVM-for-expression") for FullSense / LLMesh.

One typed node tree, many renderers.  LLM output is emitted as RepIR once, then
Markdown (the always-safe degrade floor), SVG (web/article), and TUI (``llove``)
writers render it.  The tree travels over MCP as standard ``structuredContent``
with Markdown co-located in a ``text`` block, so non-RepIR-aware clients never
break.

Public API::

    from llmesh.repir import Document, Container, Heading, Text, render
    doc = Document.of(Heading(level=1, children=[Text(text="Hello")]))
    print(render(doc, "markdown"))
"""
from __future__ import annotations

from .markdown_writer import MarkdownWriter
from .mcp_result import build_error_result, build_mcp_result
from .model import (
    CONTAINER_TAGS,
    NODE_TYPES,
    REP_SCHEMA_VERSION,
    CodeBlock,
    Container,
    Document,
    Figure,
    Heading,
    ListNode,
    Node,
    Panel,
    RepIRCapabilityError,
    RepIRError,
    RepIRValidationError,
    Style,
    Table,
    Text,
    node_from_dict,
)
from .schema import REPIR_DOCUMENT_SCHEMA, repir_output_schema
from .svg_writer import SvgWriter
from .tui_writer import TuiWriter
from .writer_base import Writer

_WRITERS: dict[str, type[Writer]] = {
    "markdown": MarkdownWriter,
    "svg": SvgWriter,
    "tui": TuiWriter,
}


def render(doc: Document, fmt: str = "markdown") -> str:
    """Render *doc* with the named writer (``markdown`` | ``svg`` | ``tui``)."""
    try:
        writer_cls = _WRITERS[fmt]
    except KeyError:
        raise RepIRValidationError(
            f"unknown render format {fmt!r}; available: {sorted(_WRITERS)}"
        ) from None
    return writer_cls().render(doc)


__all__ = [
    # model
    "Document",
    "Node",
    "Text",
    "Heading",
    "ListNode",
    "Table",
    "CodeBlock",
    "Figure",
    "Panel",
    "Container",
    "Style",
    "node_from_dict",
    "NODE_TYPES",
    "CONTAINER_TAGS",
    "REP_SCHEMA_VERSION",
    # errors
    "RepIRError",
    "RepIRValidationError",
    "RepIRCapabilityError",
    # writers
    "Writer",
    "MarkdownWriter",
    "SvgWriter",
    "TuiWriter",
    "render",
    # schema + mcp
    "REPIR_DOCUMENT_SCHEMA",
    "repir_output_schema",
    "build_mcp_result",
    "build_error_result",
]
