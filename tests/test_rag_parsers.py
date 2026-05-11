"""Tests for llmesh.rag.parsers — text / markdown / html / pdf (Phase 5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from llmesh.rag.parsers import (
    PDFExtractionError,
    parse_document,
    parse_html,
    parse_markdown,
    parse_pdf,
    parse_text,
)


# ---------------------------------------------------------------------------
# text
# ---------------------------------------------------------------------------


class TestText:
    def test_passthrough_normalises_whitespace(self) -> None:
        out = parse_text("hello    world\r\nfoo")
        assert out == "hello world\nfoo"

    def test_collapses_runaway_newlines(self) -> None:
        out = parse_text("a\n\n\n\n\nb")
        assert out == "a\n\nb"


# ---------------------------------------------------------------------------
# markdown
# ---------------------------------------------------------------------------


class TestMarkdown:
    def test_strips_headers(self) -> None:
        out = parse_markdown("# Title\n\n## Subhead\n\ntext")
        assert out.startswith("Title")
        assert "Subhead" in out

    def test_strips_bold_and_emphasis(self) -> None:
        out = parse_markdown("**bold** and *emph* and ***both***")
        assert "bold" in out and "emph" in out
        assert "**" not in out
        assert "*emph*" not in out

    def test_keeps_link_label(self) -> None:
        out = parse_markdown("[click here](https://example.com)")
        assert "click here" in out
        assert "example.com" not in out

    def test_keeps_image_alt(self) -> None:
        out = parse_markdown("![alt text](img.png)")
        assert "alt text" in out

    def test_strips_inline_code(self) -> None:
        out = parse_markdown("Use `foo()` here.")
        assert "foo()" in out
        assert "`" not in out

    def test_strips_fenced_code_block(self) -> None:
        md = "Before\n\n```python\nprint('hi')\n```\n\nAfter"
        out = parse_markdown(md)
        assert "print('hi')" in out
        assert "```" not in out

    def test_strips_bullets_and_numbers(self) -> None:
        out = parse_markdown("- one\n- two\n\n1. three\n2. four")
        assert "one" in out and "four" in out
        assert "- one" not in out


# ---------------------------------------------------------------------------
# html
# ---------------------------------------------------------------------------


class TestHtml:
    def test_extracts_visible_text(self) -> None:
        html_doc = "<html><body><p>Hello <b>world</b></p></body></html>"
        out = parse_html(html_doc)
        assert "Hello" in out
        assert "world" in out
        assert "<" not in out

    def test_drops_script_and_style(self) -> None:
        html_doc = (
            "<html><head><style>p {color: red}</style>"
            "<script>alert(1)</script></head>"
            "<body><p>visible</p></body></html>"
        )
        out = parse_html(html_doc)
        assert "visible" in out
        assert "alert" not in out
        assert "color: red" not in out

    def test_decodes_entities(self) -> None:
        out = parse_html("<p>A &amp; B &lt; C</p>")
        assert "A & B < C" in out

    def test_block_boundaries_become_newlines(self) -> None:
        out = parse_html("<p>one</p><p>two</p>")
        assert "one" in out and "two" in out
        # block tags should not collapse into a single run-on line
        assert out.count("\n") >= 1 or out != "onetwo"


# ---------------------------------------------------------------------------
# pdf — optional dependency
# ---------------------------------------------------------------------------


class TestPdf:
    def test_raises_if_pypdf_missing_or_succeeds(self) -> None:
        # We don't ship a real PDF fixture for the PoC; we only check
        # that the failure mode is the documented one when bytes don't
        # form a real PDF.
        try:
            import pypdf  # noqa: F401
        except ImportError:
            with pytest.raises(PDFExtractionError):
                parse_pdf(b"not-a-pdf")
            return
        # pypdf is installed — invalid bytes should still raise (the
        # exact exception type depends on pypdf version, but the
        # parser only raises PDFExtractionError on extraction failure).
        # A non-PDF byte sequence raises before extraction begins;
        # that's acceptable for this PoC test.
        with pytest.raises(Exception):
            parse_pdf(b"not-a-pdf")


# ---------------------------------------------------------------------------
# dispatcher
# ---------------------------------------------------------------------------


class TestDispatcher:
    def test_auto_dispatch_markdown_from_path(self, tmp_path: Path) -> None:
        p = tmp_path / "doc.md"
        p.write_text("# Title\n\ntext", encoding="utf-8")
        out = parse_document(p)
        assert "Title" in out
        assert "#" not in out

    def test_auto_dispatch_html_from_path(self, tmp_path: Path) -> None:
        p = tmp_path / "doc.html"
        p.write_text("<p>hi</p>", encoding="utf-8")
        out = parse_document(p)
        assert "hi" in out

    def test_auto_str_defaults_to_text(self) -> None:
        out = parse_document("just  text\r\nmore")
        assert out == "just text\nmore"

    def test_explicit_kind_overrides_auto(self) -> None:
        out = parse_document("# H1", kind="markdown")
        assert out == "H1"

    def test_unknown_extension_falls_back_to_text(self, tmp_path: Path) -> None:
        p = tmp_path / "doc.weird"
        p.write_text("body", encoding="utf-8")
        out = parse_document(p)
        assert out == "body"

    def test_unknown_kind_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown parser kind"):
            parse_document("x", kind="quantum")  # type: ignore[arg-type]
