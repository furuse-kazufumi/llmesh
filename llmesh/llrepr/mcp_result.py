"""Build an MCP ``tools/call`` result from a llrepr document.

Implements the design the compat note locked in (``llrepr_mcp_compat_2026_05_23.md``):

- **No custom content type.**  The typed tree rides in standard
  ``structuredContent`` (under ``llrepr``); llrepr-aware consumers validate it
  against :func:`llmesh.llrepr.schema.llrepr_output_schema` and render typed.
- **Markdown degrade co-located** in a ``content`` ``text`` block, the
  backwards-compatibility pattern the MCP spec recommends, so a non-llrepr client
  (e.g. a generic llama.cpp MCP router) still receives faithful content instead
  of breaking on an unknown block.
- **512 KB tool-result cap** (matching ``llmesh/mcp/validator.py``): if the
  serialised ``structuredContent`` would exceed it, the typed tree is dropped to
  a side-channel and only the Markdown text remains — honest degrade, never a
  silently truncated payload.
- **Large/binary content via ``resource_link``**, not inline.

This module is a thin, dependency-light bridge: it does not touch the stdio
server's privacy pipeline.  Wiring it into ``stdio_server.py`` (and bumping the
protocol version to ``2025-06-18``) is a separate, explicit step.
"""
from __future__ import annotations

import json
from typing import Any

from .markdown_writer import MarkdownWriter
from .model import Document

# Mirror the OutputValidator hard cap so we degrade *before* the gate rejects us.
_MAX_STRUCTURED_BYTES = 512_000


def _structured_size(structured: dict[str, Any]) -> int:
    return len(json.dumps(structured, ensure_ascii=False).encode("utf-8"))


def build_mcp_result(
    doc: Document,
    *,
    resource_links: list[dict[str, str]] | None = None,
    max_structured_bytes: int = _MAX_STRUCTURED_BYTES,
) -> dict[str, Any]:
    """Build an MCP tool-call result carrying *doc* as llrepr.

    Args:
        doc: The llrepr document (validated here, fail-closed).
        resource_links: Optional MCP ``resource_link`` content blocks for large
            or binary side-channel payloads (each ``{"type": "resource_link",
            "uri": ..., ...}``).
        max_structured_bytes: Drop ``structuredContent`` above this size and rely
            on the Markdown text + resource links instead.

    Returns:
        A dict suitable as the ``result`` of an MCP ``tools/call`` response.
    """
    doc.validate()
    markdown = MarkdownWriter().render(doc)

    content: list[dict[str, Any]] = [{"type": "text", "text": markdown}]
    if resource_links:
        for link in resource_links:
            block = {"type": "resource_link", **link}
            content.append(block)

    result: dict[str, Any] = {"content": content, "isError": False}

    structured = {"llrepr": doc.to_dict()}
    if _structured_size(structured) <= max_structured_bytes:
        result["structuredContent"] = structured
    else:
        # Honest degrade: typed tree too large for a tool result. The Markdown
        # text still carries the content; a side-channel (MQTT/SSE) should deliver
        # the full llrepr. Flag it so callers/audit can see the downgrade.
        result["_meta"] = {
            "llrepr.structured_omitted": True,
            "llrepr.reason": "structuredContent exceeds tool-result cap; use side-channel",
        }

    return result


def build_error_result(message: str) -> dict[str, Any]:
    """Build a fail-closed MCP error result (text-only, ``isError`` true)."""
    return {"content": [{"type": "text", "text": message}], "isError": True}
