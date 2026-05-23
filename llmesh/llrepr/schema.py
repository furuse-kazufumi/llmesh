"""JSON Schema for the llrepr document — the contract carried over MCP.

This schema is what a tool declares as its MCP ``outputSchema`` (wrapped under
``structuredContent.llrepr`` per the compat-doc recommendation), letting a
llrepr-aware consumer validate before typed rendering.  It mirrors
:mod:`llmesh.llrepr.model`; the model's own ``validate`` is the runtime guard,
this is the wire contract.

Built programmatically from the node catalog so the two stay in lockstep.
"""
from __future__ import annotations

from typing import Any

from .model import CONTAINER_TAGS, NODE_TYPES, LLREPR_SCHEMA_VERSION

_NODE_REF = {"$ref": "#/$defs/node"}

_STYLE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "bold": {"type": "boolean"},
        "italic": {"type": "boolean"},
        "color": {"type": "string"},
        "align": {"type": "string", "enum": ["left", "center", "right", "justify"]},
        "size": {"type": "string"},
    },
    "additionalProperties": False,
}


def _node_variant(node_type: str, props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    """Build one node-type variant schema with the shared optional fields."""
    properties: dict[str, Any] = {
        "type": {"const": node_type},
        "style": _STYLE_SCHEMA,
        "extensions": {"type": "object"},
    }
    properties.update(props)
    return {
        "type": "object",
        "properties": properties,
        "required": ["type", *required],
        "additionalProperties": False,
    }


_NODE_VARIANTS: dict[str, dict[str, Any]] = {
    "text": _node_variant("text", {"text": {"type": "string"}}, ["text"]),
    "heading": _node_variant(
        "heading",
        {
            "level": {"type": "integer", "minimum": 1, "maximum": 6},
            "children": {"type": "array", "items": _NODE_REF},
        },
        ["level", "children"],
    ),
    "list": _node_variant(
        "list",
        {
            "ordered": {"type": "boolean"},
            "items": {"type": "array", "items": {"type": "array", "items": _NODE_REF}},
        },
        ["ordered", "items"],
    ),
    "table": _node_variant(
        "table",
        {
            "headers": {"type": "array", "items": {"type": "string"}},
            "rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
        },
        ["headers", "rows"],
    ),
    "code_block": _node_variant(
        "code_block",
        {"code": {"type": "string"}, "language": {"type": "string"}},
        ["code", "language"],
    ),
    "figure": _node_variant(
        "figure",
        {
            "src": {"type": "string", "minLength": 1},
            "caption": {"type": "string"},
            "alt": {"type": "string"},
        },
        ["src"],
    ),
    "panel": _node_variant(
        "panel",
        {
            "caption": {"type": "string"},
            "dialogue": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"speaker": {"type": "string"}, "text": {"type": "string"}},
                    "required": ["text"],
                    "additionalProperties": False,
                },
            },
            "characters": {"type": "array", "items": {"type": "string"}},
        },
        ["dialogue", "characters"],
    ),
    "container": _node_variant(
        "container",
        {
            "tag": {"type": "string", "enum": sorted(CONTAINER_TAGS)},
            "children": {"type": "array", "items": _NODE_REF},
        },
        ["tag", "children"],
    ),
}

# Sanity: every core node type has a schema variant.
assert set(_NODE_VARIANTS) == set(NODE_TYPES), "schema/model node catalog drift"

LLREPR_DOCUMENT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    # An $id makes this a self-contained schema resource: its internal
    # "#/$defs/node" refs resolve against this base even when the whole schema is
    # embedded inside a larger one (e.g. under `llrepr` in llrepr_output_schema()).
    "$id": "https://fullsense.dev/schemas/llrepr/document.json",
    "title": "llrepr Document",
    "type": "object",
    "properties": {
        "repSchema": {"type": "string", "pattern": "^llrepr/"},
        "extensionsUsed": {"type": "array", "items": {"type": "string"}},
        "extensionsRequired": {"type": "array", "items": {"type": "string"}},
        "root": _NODE_REF,
    },
    "required": ["repSchema", "root"],
    "additionalProperties": False,
    "$defs": {"node": {"oneOf": list(_NODE_VARIANTS.values())}},
}


def llrepr_output_schema() -> dict[str, Any]:
    """The MCP ``outputSchema`` for a tool returning llrepr.

    Wraps the document schema under ``llrepr`` to match the ``structuredContent``
    shape produced by :func:`llmesh.llrepr.mcp_result.build_mcp_result`.
    """
    return {
        "type": "object",
        "properties": {"llrepr": LLREPR_DOCUMENT_SCHEMA},
        "required": ["llrepr"],
        "additionalProperties": True,
    }


__all__ = ["LLREPR_DOCUMENT_SCHEMA", "LLREPR_SCHEMA_VERSION", "llrepr_output_schema"]
