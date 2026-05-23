"""llrepr — typed Representation IR ("LLVM-for-expression") for FullSense / LLMesh.

One typed node tree, many renderers.  LLM output is emitted as llrepr once, then
Markdown (the always-safe degrade floor), SVG (web/article), and TUI (``llove``)
writers render it.  The tree travels over MCP as standard ``structuredContent``
with Markdown co-located in a ``text`` block, so non-llrepr-aware clients never
break.

Public API::

    from llmesh.llrepr import Document, Container, Heading, Text, render
    doc = Document.of(Heading(level=1, children=[Text(text="Hello")]))
    print(render(doc, "markdown"))
"""
from __future__ import annotations

from .diff import apply_patch, diff_documents, prediction_error
from .markdown_writer import MarkdownWriter
from .mcp_result import build_error_result, build_mcp_result
from .model import (
    CONTAINER_TAGS,
    NODE_TYPES,
    LLREPR_SCHEMA_VERSION,
    CodeBlock,
    Container,
    Document,
    Figure,
    Heading,
    ListNode,
    Node,
    Panel,
    LlreprCapabilityError,
    LlreprError,
    LlreprValidationError,
    Style,
    Table,
    Text,
    node_from_dict,
)
from .schema import LLREPR_DOCUMENT_SCHEMA, llrepr_output_schema
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
        raise LlreprValidationError(
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
    "LLREPR_SCHEMA_VERSION",
    # errors
    "LlreprError",
    "LlreprValidationError",
    "LlreprCapabilityError",
    # writers
    "Writer",
    "MarkdownWriter",
    "SvgWriter",
    "TuiWriter",
    "render",
    # schema + mcp
    "LLREPR_DOCUMENT_SCHEMA",
    "llrepr_output_schema",
    "build_mcp_result",
    "build_error_result",
    # diff (prediction-error primitive)
    "diff_documents",
    "apply_patch",
    "prediction_error",
]
