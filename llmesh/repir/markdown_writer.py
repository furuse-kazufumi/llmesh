"""RepIR → Markdown writer — the minimum-guarantee degrade.

Markdown is RepIR's *floor*: every core node renders to it, and the MCP layer
always co-locates this output in a ``text`` block so a client that ignores
``structuredContent`` still receives faithful, human-readable content (the
backwards-compatibility pattern the MCP spec recommends).

This writer understands no extensions, so it refuses any document that *requires*
one (fail-closed via :meth:`Writer.check_capabilities`).
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


class MarkdownWriter(Writer):
    """Render a RepIR document to GitHub-flavoured Markdown."""

    format_name = "markdown"
    supported_extensions = frozenset()

    def render(self, doc: Document) -> str:
        self.check_capabilities(doc)
        doc.validate()
        return self._block(doc.root).strip() + "\n"

    # -- inline -------------------------------------------------------------

    def _inline(self, node: Node) -> str:
        """Render a node in an inline context (text runs; others degrade)."""
        if isinstance(node, Text):
            return self._style_text(node)
        # Non-text node in an inline slot: degrade to its block form, flattened.
        return self._block(node).replace("\n", " ").strip()

    @staticmethod
    def _style_text(node: Text) -> str:
        text = node.text
        if node.style is not None:
            if node.style.bold and node.style.italic:
                text = f"***{text}***"
            elif node.style.bold:
                text = f"**{text}**"
            elif node.style.italic:
                text = f"*{text}*"
        return text

    def _inline_join(self, children: list[Node]) -> str:
        return "".join(self._inline(c) for c in children)

    # -- block --------------------------------------------------------------

    def _block(self, node: Node) -> str:
        if isinstance(node, Text):
            return self._style_text(node)
        if isinstance(node, Heading):
            return "#" * node.level + " " + self._inline_join(node.children)
        if isinstance(node, CodeBlock):
            return f"```{node.language}\n{node.code}\n```"
        if isinstance(node, Figure):
            line = f"![{node.alt}]({node.src})"
            if node.caption:
                line += f"\n\n*{node.caption}*"
            return line
        if isinstance(node, ListNode):
            return self._list(node)
        if isinstance(node, Table):
            return self._table(node)
        if isinstance(node, Panel):
            return self._panel(node)
        if isinstance(node, Container):
            return self._container(node)
        # Should be unreachable: model.node_from_dict rejects unknown types.
        return ""

    def _list(self, node: ListNode, depth: int = 0) -> str:
        indent = "  " * depth
        lines: list[str] = []
        for i, item in enumerate(node.items, start=1):
            marker = f"{i}." if node.ordered else "-"
            # First inline-ish content on the marker line; nested blocks indented.
            head_parts: list[str] = []
            tail_blocks: list[str] = []
            for child in item:
                if isinstance(child, ListNode):
                    tail_blocks.append(self._list(child, depth + 1))
                elif isinstance(child, (Text, Heading)):
                    head_parts.append(self._inline(child))
                else:
                    tail_blocks.append(self._block(child))
            head = " ".join(p for p in head_parts if p)
            lines.append(f"{indent}{marker} {head}".rstrip())
            lines.extend(tail_blocks)
        return "\n".join(lines)

    @staticmethod
    def _escape_cell(value: str) -> str:
        return value.replace("|", "\\|").replace("\n", " ")

    def _table(self, node: Table) -> str:
        if not node.headers:
            # Headerless table: synthesise a blank header row so Markdown renders.
            width = max((len(r) for r in node.rows), default=0)
            headers = [""] * width
        else:
            headers = node.headers
        lines = ["| " + " | ".join(self._escape_cell(h) for h in headers) + " |"]
        lines.append("| " + " | ".join("---" for _ in headers) + " |")
        for row in node.rows:
            lines.append("| " + " | ".join(self._escape_cell(c) for c in row) + " |")
        return "\n".join(lines)

    def _panel(self, node: Panel) -> str:
        lines: list[str] = []
        if node.caption:
            lines.append(f"> **{node.caption}**")
        for balloon in node.dialogue:
            speaker = balloon.get("speaker", "")
            text = balloon.get("text", "")
            lines.append(f"> {speaker}: {text}" if speaker else f"> {text}")
        if node.characters:
            lines.append(f"> _(characters: {', '.join(node.characters)})_")
        return "\n".join(lines) if lines else "> "

    def _container(self, node: Container) -> str:
        rendered = [self._block(c) for c in node.children]
        rendered = [r for r in rendered if r]
        # Block-level children are separated by a blank line; this is also fine for
        # row/column tags since Markdown is inherently linear.
        return "\n\n".join(rendered)
