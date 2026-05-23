"""llrepr → TUI writer — a typed plain-text renderer for ``llove`` (terminal).

Produces a monospace, box-drawn layout free of Markdown syntax: headings are
underlined, tables and manga panels use Unicode box-drawing, lists are indented.
``llove`` (the FullSense TUI) consumes this directly; colour/animation is layered
by the host, not baked into the IR.

Like the other writers it understands no extensions and refuses documents that
*require* one (fail-closed).
"""
from __future__ import annotations

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

_WIDTH = 78  # target terminal width for box drawing


class TuiWriter(Writer):
    """Render a llrepr document to monospace terminal text."""

    format_name = "tui"
    supported_extensions = frozenset()

    def render(self, doc: Document) -> str:
        self.check_capabilities(doc)
        doc.validate()
        blocks = self._block(doc.root)
        return "\n\n".join(b for b in blocks if b).strip() + "\n"

    # -- dispatch (returns a list of block strings) -------------------------

    def _block(self, node: Node) -> list[str]:
        if isinstance(node, Container):
            out: list[str] = []
            for child in node.children:
                out.extend(self._block(child))
            return out
        if isinstance(node, Heading):
            text = self._inline(node.children)
            underline = ("=" if node.level == 1 else "-") * max(len(text), 1)
            return [f"{text}\n{underline}"]
        if isinstance(node, Text):
            return [self._inline([node])]
        if isinstance(node, CodeBlock):
            tag = f"  [{node.language}]" if node.language else ""
            body = "\n".join("    " + line for line in node.code.split("\n"))
            return [f"{tag}\n{body}".lstrip("\n")]
        if isinstance(node, ListNode):
            return ["\n".join(self._list_lines(node))]
        if isinstance(node, Table):
            return [self._table(node)]
        if isinstance(node, Figure):
            cap = f" — {node.caption}" if node.caption else ""
            return [f"[figure: {node.src}{cap}]"]
        if isinstance(node, Panel):
            return [self._panel(node)]
        return []

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _inline(children: list[Node]) -> str:
        # TUI is plain text: styling tokens are dropped (host applies colour).
        return "".join(c.text for c in children if isinstance(c, Text))

    def _list_lines(self, node: ListNode, depth: int = 0) -> list[str]:
        indent = "  " * depth
        lines: list[str] = []
        for i, item in enumerate(node.items, start=1):
            marker = f"{i}." if node.ordered else "•"
            head = " ".join(n.text for n in item if isinstance(n, Text))
            lines.append(f"{indent}{marker} {head}".rstrip())
            for child in item:
                if isinstance(child, ListNode):
                    lines.extend(self._list_lines(child, depth + 1))
        return lines

    def _table(self, node: Table) -> str:
        rows = ([node.headers] if node.headers else []) + node.rows
        if not rows:
            return ""
        cols = max(len(r) for r in rows)
        widths = [0] * cols
        for row in rows:
            for c in range(cols):
                cell = row[c] if c < len(row) else ""
                widths[c] = max(widths[c], len(cell))

        def hline(left: str, mid: str, right: str) -> str:
            return left + mid.join("─" * (w + 2) for w in widths) + right

        def fmt(row: list[str]) -> str:
            cells = [(row[c] if c < len(row) else "").ljust(widths[c]) for c in range(cols)]
            return "│ " + " │ ".join(cells) + " │"

        out = [hline("┌", "┬", "┐")]
        start = 0
        if node.headers:
            out.append(fmt(node.headers))
            out.append(hline("├", "┼", "┤"))
            start = 1
        for row in rows[start:]:
            out.append(fmt(row))
        out.append(hline("└", "┴", "┘"))
        return "\n".join(out)

    def _panel(self, node: Panel) -> str:
        lines: list[str] = []
        if node.caption:
            lines.append(node.caption)
        for b in node.dialogue:
            speaker = b.get("speaker", "")
            text = b.get("text", "")
            lines.append(f"{speaker}: {text}" if speaker else text)
        if node.characters:
            lines.append(f"({', '.join(node.characters)})")
        width = min(_WIDTH, max((len(line) for line in lines), default=10) + 2)
        top = "╭" + "─" * width + "╮"
        bottom = "╰" + "─" * width + "╯"
        body = [f"│ {line.ljust(width - 1)}│" for line in lines]
        return "\n".join([top, *body, bottom])
