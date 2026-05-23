"""Tests for the RepIR core model and writers."""
from __future__ import annotations

import pytest

from llmesh.repir import (
    CodeBlock,
    Container,
    Document,
    Figure,
    Heading,
    ListNode,
    MarkdownWriter,
    Panel,
    RepIRCapabilityError,
    RepIRValidationError,
    Style,
    SvgWriter,
    Table,
    Text,
    TuiWriter,
    node_from_dict,
    render,
)


def _sample_doc() -> Document:
    return Document.of(
        Heading(level=1, children=[Text(text="RepIR Demo")]),
        Container(
            tag="block",
            children=[Text(text="Intro paragraph.", style=Style(bold=True))],
        ),
        ListNode(ordered=False, items=[[Text(text="first")], [Text(text="second")]]),
        Table(headers=["a", "b"], rows=[["1", "2"], ["3", "4"]]),
        CodeBlock(language="python", code="print('hi')"),
        Figure(src="https://example.com/x.png", caption="a figure", alt="x"),
        Panel(
            caption="Scene 1",
            dialogue=[{"speaker": "Ai", "text": "Hello!"}],
            characters=["Ai"],
        ),
    )


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

def test_document_of_builds_document_container_root():
    doc = Document.of(Text(text="x"))
    assert isinstance(doc.root, Container)
    assert doc.root.tag == "document"
    assert doc.rep_schema.startswith("repir/")


def test_round_trip_to_from_dict():
    doc = _sample_doc()
    as_dict = doc.to_dict()
    rebuilt = Document.from_dict(as_dict)
    assert rebuilt.to_dict() == as_dict


def test_validate_rejects_bad_heading_level():
    doc = Document.of(Heading(level=9, children=[Text(text="bad")]))
    with pytest.raises(RepIRValidationError):
        doc.validate()


def test_node_from_dict_rejects_unknown_type():
    with pytest.raises(RepIRValidationError):
        node_from_dict({"type": "wormhole"})


def test_table_row_width_mismatch_rejected():
    doc = Document.of(Table(headers=["a", "b"], rows=[["only-one"]]))
    with pytest.raises(RepIRValidationError):
        doc.validate()


def test_required_extension_must_be_subset_of_used():
    doc = Document(root=Text(text="x"), extensions_required=["ext.foo"], extensions_used=[])
    with pytest.raises(RepIRValidationError):
        doc.validate()


def test_style_round_trip_and_invalid_align():
    s = Style(bold=True, italic=True, align="center")
    assert Style.from_dict(s.to_dict()) == s
    with pytest.raises(RepIRValidationError):
        Style.from_dict({"align": "diagonal"})


# ---------------------------------------------------------------------------
# Writers — degrade floor (Markdown)
# ---------------------------------------------------------------------------

def test_markdown_renders_all_core_nodes():
    md = MarkdownWriter().render(_sample_doc())
    assert "# RepIR Demo" in md
    assert "**Intro paragraph.**" in md
    assert "- first" in md
    assert "| a | b |" in md
    assert "```python" in md
    assert "![x](https://example.com/x.png)" in md
    assert "Ai: Hello!" in md


def test_markdown_table_escapes_pipes():
    doc = Document.of(Table(headers=["c"], rows=[["a|b"]]))
    md = MarkdownWriter().render(doc)
    assert "a\\|b" in md


def test_writer_refuses_required_unsupported_extension():
    doc = Document(
        root=Text(text="x"),
        extensions_used=["ext.special"],
        extensions_required=["ext.special"],
    )
    with pytest.raises(RepIRCapabilityError):
        MarkdownWriter().render(doc)


# ---------------------------------------------------------------------------
# Writers — typed (SVG, TUI)
# ---------------------------------------------------------------------------

def test_svg_is_self_contained_and_sized():
    svg = SvgWriter().render(_sample_doc())
    assert svg.startswith("<svg")
    assert svg.rstrip().endswith("</svg>")
    assert "RepIR Demo" in svg
    assert 'href="https://example.com/x.png"' in svg


def test_svg_escapes_markup_in_text():
    doc = Document.of(Text(text="a < b & c > d"))
    svg = SvgWriter().render(doc)
    assert "&lt;" in svg and "&amp;" in svg
    assert "< b &" not in svg  # raw markup must not leak


def test_tui_draws_box_table_and_panel():
    out = TuiWriter().render(_sample_doc())
    assert "┌" in out and "┐" in out  # table box
    assert "╭" in out and "╯" in out  # panel box
    assert "RepIR Demo" in out
    assert "=" in out  # h1 underline


def test_render_dispatch_unknown_format_rejected():
    with pytest.raises(RepIRValidationError):
        render(_sample_doc(), "hologram")


def test_render_dispatch_all_formats():
    doc = _sample_doc()
    for fmt in ("markdown", "svg", "tui"):
        assert isinstance(render(doc, fmt), str)
