"""Tests for the llrepr JSON Schema and MCP result builder."""
from __future__ import annotations

import json

import jsonschema
import pytest

from llmesh.llrepr import (
    Container,
    Document,
    Heading,
    Text,
    build_error_result,
    build_mcp_result,
    llrepr_output_schema,
)
from llmesh.llrepr.schema import LLREPR_DOCUMENT_SCHEMA


def _doc() -> Document:
    return Document.of(
        Heading(level=2, children=[Text(text="Title")]),
        Container(tag="block", children=[Text(text="body")]),
    )


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def test_document_validates_against_schema():
    jsonschema.validate(_doc().to_dict(), LLREPR_DOCUMENT_SCHEMA)


def test_schema_rejects_unknown_node_type():
    bad = {"repSchema": "llrepr/0.1", "root": {"type": "wormhole"}}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, LLREPR_DOCUMENT_SCHEMA)


def test_schema_rejects_additional_properties():
    bad = _doc().to_dict()
    bad["surpriseField"] = 1
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, LLREPR_DOCUMENT_SCHEMA)


def test_output_schema_wraps_under_llrepr():
    out_schema = llrepr_output_schema()
    payload = {"llrepr": _doc().to_dict()}
    jsonschema.validate(payload, out_schema)


# ---------------------------------------------------------------------------
# MCP result builder
# ---------------------------------------------------------------------------

def test_build_result_has_structured_and_text():
    result = build_mcp_result(_doc())
    assert result["isError"] is False
    # Markdown degrade co-located in a text block (backwards-compat pattern).
    text_blocks = [c for c in result["content"] if c["type"] == "text"]
    assert text_blocks and "## Title" in text_blocks[0]["text"]
    # Typed tree in structuredContent under llrepr, schema-valid.
    assert "llrepr" in result["structuredContent"]
    jsonschema.validate(result["structuredContent"], llrepr_output_schema())


def test_build_result_includes_resource_links():
    links = [{"uri": "mqtt://node/topic", "name": "stream"}]
    result = build_mcp_result(_doc(), resource_links=links)
    res_blocks = [c for c in result["content"] if c["type"] == "resource_link"]
    assert res_blocks[0]["uri"] == "mqtt://node/topic"


def test_oversize_structured_content_degrades_to_text_only():
    # Force the cap low so the typed tree is dropped to a side-channel.
    result = build_mcp_result(_doc(), max_structured_bytes=10)
    assert "structuredContent" not in result
    assert result["_meta"]["llrepr.structured_omitted"] is True
    # Text degrade still present — never a silently truncated payload.
    assert any(c["type"] == "text" for c in result["content"])


def test_result_under_cap_passes_validator_size_gate():
    result = build_mcp_result(_doc())
    serialized = json.dumps(result["structuredContent"], ensure_ascii=False).encode()
    assert len(serialized) <= 512_000


def test_error_result_is_fail_closed():
    err = build_error_result("validation_error: nope")
    assert err["isError"] is True
    assert err["content"][0]["text"].startswith("validation_error")
