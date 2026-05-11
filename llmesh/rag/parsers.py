"""Document parsers — text / Markdown / HTML / PDF → plain text (Phase 5).

Lightweight extractors that turn a raw bytes / string source into the
plain text shape consumed by :class:`llmesh.rag.embedder.Embedder`.
Stdlib-only for ``text`` / ``markdown`` / ``html``; PDF is optional
and raises a clear error when ``pypdf`` is not installed.

The parsers intentionally produce *naive* text — they do not preserve
formatting, layout, or table structure beyond a row → tab separation
fallback. Phase 5 ships the I/O boundary; richer extraction (figures,
captions, embedded equations) lives in later phases that may pull in
heavier dependencies.
"""

from __future__ import annotations

import html
import io
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Literal

# Supported source kinds. ``auto`` lets :func:`parse_document` dispatch
# by file extension when given a path.
ParserKind = Literal["text", "markdown", "html", "pdf", "auto"]

_MD_FENCED = re.compile(r"```[^\n]*\n[\s\S]*?\n```", re.MULTILINE)
_MD_INLINE_CODE = re.compile(r"`([^`]+)`")
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_MD_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]+\)")
_MD_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_MD_EMPH = re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)")
_MD_HEADER = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_MD_LIST_BULLET = re.compile(r"^\s*[-*+]\s+", re.MULTILINE)
_MD_LIST_NUM = re.compile(r"^\s*\d+\.\s+", re.MULTILINE)
_MULTI_WS = re.compile(r"[ \t]+")
_MULTI_NL = re.compile(r"\n{3,}")


# ---------------------------------------------------------------------------
# text — passthrough
# ---------------------------------------------------------------------------


def parse_text(source: str) -> str:
    """Normalise whitespace; otherwise pass the source through."""
    text = source.replace("\r\n", "\n").replace("\r", "\n")
    text = _MULTI_WS.sub(" ", text)
    return _MULTI_NL.sub("\n\n", text).strip()


# ---------------------------------------------------------------------------
# markdown — stdlib-only naive extractor
# ---------------------------------------------------------------------------


def parse_markdown(source: str) -> str:
    """Strip Markdown markup, preserving the visible text content.

    Code blocks become plain code (fences removed), images render as
    their alt text, links as their label. Heavy syntax (tables, HTML
    embeds) is left mostly intact — the caller is welcome to feed the
    result back through :func:`parse_html` if HTML appears.
    """
    text = source.replace("\r\n", "\n").replace("\r", "\n")
    # Pull fenced code-block content without backticks
    def _strip_fence(match: re.Match[str]) -> str:
        lines = match.group(0).split("\n")
        return "\n".join(lines[1:-1])

    text = _MD_FENCED.sub(_strip_fence, text)
    text = _MD_IMAGE.sub(r"\1", text)
    text = _MD_LINK.sub(r"\1", text)
    text = _MD_BOLD.sub(r"\1", text)
    text = _MD_EMPH.sub(r"\1", text)
    text = _MD_INLINE_CODE.sub(r"\1", text)
    text = _MD_HEADER.sub("", text)
    text = _MD_LIST_BULLET.sub("", text)
    text = _MD_LIST_NUM.sub("", text)
    text = _MULTI_WS.sub(" ", text)
    return _MULTI_NL.sub("\n\n", text).strip()


# ---------------------------------------------------------------------------
# html — stdlib html.parser
# ---------------------------------------------------------------------------


class _HTMLTextExtractor(HTMLParser):
    """Collect visible text from an HTML document.

    Drops ``<script>`` / ``<style>`` content; preserves block-level
    boundaries with a newline so a paragraph break in the source
    survives extraction.
    """

    _SKIP_TAGS: frozenset[str] = frozenset({"script", "style"})
    _BLOCK_TAGS: frozenset[str] = frozenset(
        {"p", "div", "br", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr"}
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._buf: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
        elif tag in self._BLOCK_TAGS:
            self._buf.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag in self._BLOCK_TAGS:
            self._buf.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        self._buf.append(data)

    def text(self) -> str:
        return "".join(self._buf)


def parse_html(source: str) -> str:
    """Extract visible text from an HTML string."""
    parser = _HTMLTextExtractor()
    parser.feed(source)
    parser.close()
    text = html.unescape(parser.text())
    text = _MULTI_WS.sub(" ", text)
    return _MULTI_NL.sub("\n\n", text).strip()


# ---------------------------------------------------------------------------
# pdf — optional pypdf
# ---------------------------------------------------------------------------


class PDFExtractionError(RuntimeError):
    """Raised when PDF extraction fails or the dependency is missing."""


def parse_pdf(source: bytes | Path | str) -> str:
    """Extract text from a PDF using ``pypdf`` if installed.

    ``source`` may be raw bytes or a path. Raises
    :class:`PDFExtractionError` when ``pypdf`` is not installed so the
    caller can fall back (e.g. to OCR) without ambiguity.
    """
    try:
        import pypdf  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover — depends on env
        raise PDFExtractionError(
            "pypdf not installed; `pip install pypdf` or pass a pre-extracted text"
        ) from exc
    if isinstance(source, (str, Path)):
        reader = pypdf.PdfReader(str(source))
    else:
        reader = pypdf.PdfReader(io.BytesIO(source))
    pages: list[str] = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception as exc:  # pragma: no cover — depends on PDF shape
            raise PDFExtractionError(f"pdf page extraction failed: {exc}") from exc
    text = "\n\n".join(p for p in pages if p.strip())
    return parse_text(text)


# ---------------------------------------------------------------------------
# dispatcher
# ---------------------------------------------------------------------------


_EXT_TO_KIND: dict[str, ParserKind] = {
    ".txt": "text",
    ".md": "markdown",
    ".markdown": "markdown",
    ".html": "html",
    ".htm": "html",
    ".pdf": "pdf",
}


def parse_document(
    source: str | bytes | Path, *, kind: ParserKind = "auto"
) -> str:
    """Single entry point.

    When ``kind == "auto"`` and ``source`` is a :class:`Path`, dispatch
    by file extension. With a :class:`str` source the kind defaults to
    ``"text"`` because we cannot infer the format reliably. Raises
    :class:`ValueError` on unknown auto-dispatched extensions.
    """
    if kind == "auto":
        if isinstance(source, Path):
            kind = _EXT_TO_KIND.get(source.suffix.lower(), "text")
        elif isinstance(source, bytes):
            kind = "pdf"  # bytes default to PDF — text/html bytes can be decoded by caller
        else:
            kind = "text"
    if kind == "pdf":
        if isinstance(source, str) and not source.lower().endswith(".pdf"):
            # Treat as raw text if the caller passed in-memory PDF content as str
            raise ValueError("pdf kind requires bytes or a Path")
        return parse_pdf(source if isinstance(source, (bytes, Path)) else Path(source))
    if isinstance(source, (bytes, Path)):
        # text/markdown/html paths or bytes — read the bytes into a str
        if isinstance(source, Path):
            raw = source.read_text(encoding="utf-8")
        else:
            raw = source.decode("utf-8", errors="replace")
    else:
        raw = source
    if kind == "text":
        return parse_text(raw)
    if kind == "markdown":
        return parse_markdown(raw)
    if kind == "html":
        return parse_html(raw)
    raise ValueError(f"unknown parser kind: {kind!r}")


__all__ = [
    "PDFExtractionError",
    "ParserKind",
    "parse_document",
    "parse_html",
    "parse_markdown",
    "parse_pdf",
    "parse_text",
]
