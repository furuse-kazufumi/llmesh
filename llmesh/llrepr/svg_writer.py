"""llrepr → SVG writer — a typed renderer for web / article embedding.

Unlike the Markdown floor, this writer produces a *typed* visual layout: it walks
the node tree and emits positioned SVG primitives with a running vertical cursor.
Output is a single self-contained ``<svg>`` document (no external CSS/JS), suitable
for inlining in HTML, READMEs, or the animated-SVG pipeline (a later consumer can
attach SMIL to the emitted elements).

PoC scope: a single-column top-to-bottom flow with fixed width and no text
wrapping (long runs are truncated visually by the viewport).  Good enough to prove
"one IR, many backends"; richer layout is future work.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .model import (
    CodeBlock,
    Container,
    Document,
    Figure,
    Heading,
    ListNode,
    Node,
    Panel,
    Table,
    Text,
)
from .writer_base import Writer

_WIDTH = 680
_PAD = 20
_LINE = 24
_BODY_SIZE = 15
_MONO = "ui-monospace, SFMono-Regular, Menlo, monospace"
_SANS = "system-ui, -apple-system, Segoe UI, Roboto, sans-serif"


def _xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


@dataclass
class _Cursor:
    """Mutable layout state threaded through the recursive walk."""

    y: float = float(_PAD)
    elements: list[str] = field(default_factory=list)

    def advance(self, dy: float) -> None:
        self.y += dy

    def text(self, x: float, content: str, *, size: int = _BODY_SIZE,
             weight: str = "normal", family: str = _SANS, fill: str = "#1a1a1a") -> None:
        self.elements.append(
            f'<text x="{x:.0f}" y="{self.y:.0f}" font-family="{family}" '
            f'font-size="{size}" font-weight="{weight}" fill="{fill}" '
            f'xml:space="preserve">{_xml_escape(content)}</text>'
        )

    def rect(self, x: float, y: float, w: float, h: float, *,
             fill: str = "none", stroke: str = "#d0d0d0", rx: int = 4) -> None:
        self.elements.append(
            f'<rect x="{x:.0f}" y="{y:.0f}" width="{w:.0f}" height="{h:.0f}" '
            f'rx="{rx}" fill="{fill}" stroke="{stroke}"/>'
        )


class SvgWriter(Writer):
    """Render a llrepr document to a self-contained SVG string."""

    format_name = "svg"
    supported_extensions = frozenset()

    def render(self, doc: Document) -> str:
        self.check_capabilities(doc)
        doc.validate()
        cur = _Cursor()
        self._block(doc.root, cur, x=_PAD)
        height = int(cur.y + _PAD)
        body = "\n".join(cur.elements)
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{_WIDTH}" '
            f'height="{height}" viewBox="0 0 {_WIDTH} {height}" '
            f'font-family="{_SANS}">\n'
            f'<rect width="{_WIDTH}" height="{height}" fill="#ffffff"/>\n'
            f"{body}\n</svg>\n"
        )

    # -- dispatch -----------------------------------------------------------

    def _block(self, node: Node, cur: _Cursor, x: float) -> None:
        if isinstance(node, Container):
            for child in node.children:
                self._block(child, cur, x)
            return
        if isinstance(node, Heading):
            size = max(16, 30 - node.level * 2)
            cur.advance(size)
            cur.text(x, self._inline_text(node.children), size=size, weight="700")
            cur.advance(10)
            return
        if isinstance(node, Text):
            cur.advance(_LINE)
            cur.text(x, node.text, weight="700" if node.style and node.style.bold else "normal")
            return
        if isinstance(node, CodeBlock):
            self._code(node, cur, x)
            return
        if isinstance(node, ListNode):
            self._list(node, cur, x)
            return
        if isinstance(node, Table):
            self._table(node, cur, x)
            return
        if isinstance(node, Figure):
            self._figure(node, cur, x)
            return
        if isinstance(node, Panel):
            self._panel(node, cur, x)
            return

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _inline_text(children: list[Node]) -> str:
        return "".join(c.text for c in children if isinstance(c, Text))

    def _code(self, node: CodeBlock, cur: _Cursor, x: float) -> None:
        lines = node.code.split("\n")
        box_top = cur.y + 6
        box_h = len(lines) * 18 + 16
        cur.rect(x, box_top, _WIDTH - 2 * _PAD, box_h, fill="#f6f8fa", stroke="#e0e0e0")
        cur.advance(24)
        for line in lines:
            cur.text(x + 10, line, size=13, family=_MONO, fill="#24292f")
            cur.advance(18)
        cur.advance(12)

    def _list(self, node: ListNode, cur: _Cursor, x: float) -> None:
        for i, item in enumerate(node.items, start=1):
            marker = f"{i}." if node.ordered else "•"
            text = "".join(n.text for n in item if isinstance(n, Text))
            cur.advance(_LINE)
            cur.text(x + 6, f"{marker} {text}")

    def _table(self, node: Table, cur: _Cursor, x: float) -> None:
        cols = max(len(node.headers), max((len(r) for r in node.rows), default=0))
        if cols == 0:
            return
        col_w = (_WIDTH - 2 * _PAD) / cols
        row_h = 26
        rows = ([node.headers] if node.headers else []) + node.rows
        top = cur.y + 6
        for r_idx, row in enumerate(rows):
            row_y = top + r_idx * row_h
            for c_idx in range(cols):
                cell = row[c_idx] if c_idx < len(row) else ""
                cur.rect(x + c_idx * col_w, row_y, col_w, row_h, stroke="#d0d0d0")
                self.elements_text_at(cur, x + c_idx * col_w + 6, row_y + 17, cell,
                                      bold=(r_idx == 0 and bool(node.headers)))
        cur.y = top + len(rows) * row_h + 12

    @staticmethod
    def elements_text_at(cur: _Cursor, x: float, y: float, content: str, *, bold: bool) -> None:
        cur.elements.append(
            f'<text x="{x:.0f}" y="{y:.0f}" font-size="13" '
            f'font-weight="{"700" if bold else "normal"}" fill="#1a1a1a" '
            f'xml:space="preserve">{_xml_escape(content)}</text>'
        )

    def _figure(self, node: Figure, cur: _Cursor, x: float) -> None:
        w = _WIDTH - 2 * _PAD
        h = 160
        top = cur.y + 6
        cur.elements.append(
            f'<image x="{x:.0f}" y="{top:.0f}" width="{w:.0f}" height="{h}" '
            f'href="{_xml_escape(node.src)}" preserveAspectRatio="xMidYMid meet"/>'
        )
        cur.rect(x, top, w, h, stroke="#d0d0d0")
        cur.y = top + h + 8
        if node.caption:
            cur.advance(18)
            cur.text(x, node.caption, size=13, fill="#666666")
        cur.advance(10)

    def _panel(self, node: Panel, cur: _Cursor, x: float) -> None:
        w = _WIDTH - 2 * _PAD
        top = cur.y + 6
        inner_lines = ([node.caption] if node.caption else []) + [
            f'{b.get("speaker", "")}: {b.get("text", "")}'.lstrip(": ")
            for b in node.dialogue
        ]
        h = max(60, len(inner_lines) * 22 + 20)
        cur.rect(x, top, w, h, fill="#fffdf5", stroke="#333333", rx=2)
        ty = top + 24
        if node.caption:
            cur.elements.append(
                f'<text x="{x + 12:.0f}" y="{ty:.0f}" font-size="14" font-weight="700" '
                f'fill="#1a1a1a">{_xml_escape(node.caption)}</text>'
            )
            ty += 22
        for b in node.dialogue:
            speaker = b.get("speaker", "")
            line = f"{speaker}: {b.get('text', '')}" if speaker else b.get("text", "")
            cur.elements.append(
                f'<text x="{x + 12:.0f}" y="{ty:.0f}" font-size="13" '
                f'fill="#333333">{_xml_escape(line)}</text>'
            )
            ty += 22
        cur.y = top + h + 12
