"""llrepr — a typed Representation Intermediate Representation ("LLVM-for-expression").

llrepr is the *expression contract* at the heart of FullSense: LLM output is
emitted as a typed node tree once, then any number of consumers (Markdown for
articles, SVG for the web, TUI for ``llove``, manga panels for ``manga-md``)
render it.  The tree travels over MCP as ordinary JSON ``structuredContent``,
with a Markdown degrade always co-located in a ``text`` block so non-llrepr-aware
clients never break (see ``mcp_result.build_mcp_result``).

Design lineage:
- **glTF** — a small *closed* core node set plus ``extensionsUsed`` /
  ``extensionsRequired`` declarations.  A consumer that cannot satisfy a
  *required* extension must refuse (fail-closed); unknown *non-required*
  extensions degrade silently.
- **LLVM** — one IR, many backends (writers).  Adding a renderer never touches
  the producer.
- **General MIDI / SVC** — capability negotiation with graceful degrade.

This module defines the node catalog (L1), the :class:`Document` envelope, and
fail-closed structural validation.  It has **no third-party dependencies** so it
stays in the stdlib-only base install (heavy renderers may add extras later).

Canonical form is JSON-compatible ``dict``; the dataclasses are ergonomic
builders with ``to_dict`` / ``from_dict`` round-tripping.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar

LLREPR_SCHEMA_VERSION = "llrepr/0.1"
"""Versioned schema identifier carried by every :class:`Document`.

Bump the minor for additive changes, the major for breaking ones.  A consumer
compares this against the versions it understands before rendering.
"""

# The L1 closed core node catalog.  Anything outside this set must arrive via an
# extension (declared in ``extensionsUsed`` / ``extensionsRequired``).
NODE_TYPES: frozenset[str] = frozenset(
    {
        "text",
        "heading",
        "list",
        "table",
        "code_block",
        "figure",
        "panel",
        "container",
    }
)

# Recognised Container layout tags.  ``block`` is the neutral default (renders as
# a paragraph-like grouping); the rest are hints renderers may honour or ignore.
CONTAINER_TAGS: frozenset[str] = frozenset(
    {"document", "section", "block", "row", "column"}
)


# ---------------------------------------------------------------------------
# Exceptions (fail-closed)
# ---------------------------------------------------------------------------

class LlreprError(Exception):
    """Base class for all llrepr errors."""


class LlreprValidationError(LlreprError):
    """A node tree or document violates the llrepr structural contract."""


class LlreprCapabilityError(LlreprError):
    """A renderer cannot satisfy a *required* extension or node type.

    Raised by writers (not by the model) so the producer learns the consumer
    refused rather than silently dropping required semantics.
    """


# ---------------------------------------------------------------------------
# Value types
# ---------------------------------------------------------------------------

_ALIGN_VALUES = frozenset({"left", "center", "right", "justify"})


@dataclass(frozen=True)
class Style:
    """Inline/block styling attached to a node via its ``style`` field.

    Every field is optional; ``None`` means "renderer default".  Kept minimal on
    purpose — richer styling belongs in an extension, not the closed core.
    """

    bold: bool | None = None
    italic: bool | None = None
    color: str | None = None  # CSS-ish token, e.g. "#1a1a1a" or "red"
    align: str | None = None  # one of _ALIGN_VALUES
    size: str | None = None   # renderer-interpreted token, e.g. "lg", "sm"

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key in ("bold", "italic", "color", "align", "size"):
            val = getattr(self, key)
            if val is not None:
                out[key] = val
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Style":
        if not isinstance(data, dict):
            raise LlreprValidationError(f"style must be an object, got {type(data).__name__}")
        align = data.get("align")
        if align is not None and align not in _ALIGN_VALUES:
            raise LlreprValidationError(f"style.align invalid: {align!r}")
        return cls(
            bold=data.get("bold"),
            italic=data.get("italic"),
            color=data.get("color"),
            align=align,
            size=data.get("size"),
        )

    def is_empty(self) -> bool:
        return not self.to_dict()


# ---------------------------------------------------------------------------
# Node base
# ---------------------------------------------------------------------------

@dataclass
class Node:
    """Base for all llrepr nodes.

    Common, optional fields:
    - ``style`` — a :class:`Style` value object.
    - ``extensions`` — per-node extension payloads keyed by extension name; the
      document declares which are used/required.
    """

    type: ClassVar[str] = ""

    style: Style | None = None
    extensions: dict[str, Any] = field(default_factory=dict)

    # -- serialisation ------------------------------------------------------

    def _base_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"type": self.type}
        if self.style is not None and not self.style.is_empty():
            out["style"] = self.style.to_dict()
        if self.extensions:
            out["extensions"] = self.extensions
        return out

    def to_dict(self) -> dict[str, Any]:  # pragma: no cover - overridden
        raise NotImplementedError

    def validate(self) -> None:
        """Raise :class:`LlreprValidationError` if this node is malformed."""

    # -- helpers for subclasses --------------------------------------------

    @staticmethod
    def _common_kwargs(data: dict[str, Any]) -> dict[str, Any]:
        kw: dict[str, Any] = {}
        if "style" in data and data["style"] is not None:
            kw["style"] = Style.from_dict(data["style"])
        ext = data.get("extensions")
        if ext:
            if not isinstance(ext, dict):
                raise LlreprValidationError("node.extensions must be an object")
            kw["extensions"] = ext
        return kw


# ---------------------------------------------------------------------------
# Leaf / inline nodes
# ---------------------------------------------------------------------------

@dataclass
class Text(Node):
    """An inline text run."""

    type: ClassVar[str] = "text"
    text: str = ""

    def to_dict(self) -> dict[str, Any]:
        out = self._base_dict()
        out["text"] = self.text
        return out

    def validate(self) -> None:
        if not isinstance(self.text, str):
            raise LlreprValidationError("text.text must be a string")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Text":
        return cls(text=str(data.get("text", "")), **cls._common_kwargs(data))


@dataclass
class Heading(Node):
    """A section heading, level 1–6, with inline children."""

    type: ClassVar[str] = "heading"
    level: int = 1
    children: list[Node] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        out = self._base_dict()
        out["level"] = self.level
        out["children"] = [c.to_dict() for c in self.children]
        return out

    def validate(self) -> None:
        if not isinstance(self.level, int) or not 1 <= self.level <= 6:
            raise LlreprValidationError(f"heading.level must be 1–6, got {self.level!r}")
        for child in self.children:
            child.validate()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Heading":
        return cls(
            level=int(data.get("level", 1)),
            children=[node_from_dict(c) for c in data.get("children", [])],
            **cls._common_kwargs(data),
        )


@dataclass
class CodeBlock(Node):
    """A fenced code block with an optional language tag."""

    type: ClassVar[str] = "code_block"
    code: str = ""
    language: str = ""

    def to_dict(self) -> dict[str, Any]:
        out = self._base_dict()
        out["code"] = self.code
        out["language"] = self.language
        return out

    def validate(self) -> None:
        if not isinstance(self.code, str):
            raise LlreprValidationError("code_block.code must be a string")
        if not isinstance(self.language, str):
            raise LlreprValidationError("code_block.language must be a string")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CodeBlock":
        return cls(
            code=str(data.get("code", "")),
            language=str(data.get("language", "")),
            **cls._common_kwargs(data),
        )


@dataclass
class Figure(Node):
    """A figure referenced by URI.

    Binary/large payloads never travel inline (the MCP layer caps tool results at
    512 KB); a ``figure`` carries a ``src`` URI that consumers resolve, matching
    the compat-doc ``resource_link`` / side-channel guidance.
    """

    type: ClassVar[str] = "figure"
    src: str = ""
    caption: str = ""
    alt: str = ""

    def to_dict(self) -> dict[str, Any]:
        out = self._base_dict()
        out["src"] = self.src
        if self.caption:
            out["caption"] = self.caption
        if self.alt:
            out["alt"] = self.alt
        return out

    def validate(self) -> None:
        if not isinstance(self.src, str) or not self.src:
            raise LlreprValidationError("figure.src must be a non-empty URI string")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Figure":
        return cls(
            src=str(data.get("src", "")),
            caption=str(data.get("caption", "")),
            alt=str(data.get("alt", "")),
            **cls._common_kwargs(data),
        )


# ---------------------------------------------------------------------------
# Composite / block nodes
# ---------------------------------------------------------------------------

@dataclass
class ListNode(Node):
    """An ordered or unordered list; each item is a list of nodes."""

    type: ClassVar[str] = "list"
    ordered: bool = False
    items: list[list[Node]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        out = self._base_dict()
        out["ordered"] = self.ordered
        out["items"] = [[n.to_dict() for n in item] for item in self.items]
        return out

    def validate(self) -> None:
        if not isinstance(self.ordered, bool):
            raise LlreprValidationError("list.ordered must be a boolean")
        for item in self.items:
            if not isinstance(item, list):
                raise LlreprValidationError("list.items entries must be arrays of nodes")
            for node in item:
                node.validate()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ListNode":
        raw_items = data.get("items", [])
        if not isinstance(raw_items, list):
            raise LlreprValidationError("list.items must be an array")
        items = [[node_from_dict(n) for n in item] for item in raw_items]
        return cls(ordered=bool(data.get("ordered", False)), items=items, **cls._common_kwargs(data))


@dataclass
class Table(Node):
    """A simple table; PoC cells are plain strings for clean degrade."""

    type: ClassVar[str] = "table"
    headers: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        out = self._base_dict()
        out["headers"] = list(self.headers)
        out["rows"] = [list(r) for r in self.rows]
        return out

    def validate(self) -> None:
        if not all(isinstance(h, str) for h in self.headers):
            raise LlreprValidationError("table.headers must be strings")
        width = len(self.headers)
        for r_idx, row in enumerate(self.rows):
            if not isinstance(row, list):
                raise LlreprValidationError(f"table.rows[{r_idx}] must be an array")
            if width and len(row) != width:
                raise LlreprValidationError(
                    f"table.rows[{r_idx}] has {len(row)} cells, expected {width}"
                )
            if not all(isinstance(c, str) for c in row):
                raise LlreprValidationError(f"table.rows[{r_idx}] cells must be strings")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Table":
        return cls(
            headers=[str(h) for h in data.get("headers", [])],
            rows=[[str(c) for c in row] for row in data.get("rows", [])],
            **cls._common_kwargs(data),
        )


@dataclass
class Panel(Node):
    """A manga/comic panel — the ``manga-md`` consumer's first-class node.

    ``dialogue`` is an ordered list of ``{"speaker", "text"}`` balloons;
    ``characters`` lists character identifiers present in the panel.
    """

    type: ClassVar[str] = "panel"
    caption: str = ""
    dialogue: list[dict[str, str]] = field(default_factory=list)
    characters: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        out = self._base_dict()
        if self.caption:
            out["caption"] = self.caption
        out["dialogue"] = [dict(d) for d in self.dialogue]
        out["characters"] = list(self.characters)
        return out

    def validate(self) -> None:
        for d in self.dialogue:
            if not isinstance(d, dict) or "text" not in d:
                raise LlreprValidationError("panel.dialogue entries need a 'text' field")
        if not all(isinstance(c, str) for c in self.characters):
            raise LlreprValidationError("panel.characters must be strings")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Panel":
        dialogue = []
        for d in data.get("dialogue", []):
            if not isinstance(d, dict):
                raise LlreprValidationError("panel.dialogue entries must be objects")
            dialogue.append({"speaker": str(d.get("speaker", "")), "text": str(d.get("text", ""))})
        return cls(
            caption=str(data.get("caption", "")),
            dialogue=dialogue,
            characters=[str(c) for c in data.get("characters", [])],
            **cls._common_kwargs(data),
        )


@dataclass
class Container(Node):
    """Generic grouping with a layout ``tag`` hint and child nodes.

    A ``document`` container is the usual document root; ``block`` is the neutral
    paragraph-like grouping; ``section`` / ``row`` / ``column`` are layout hints
    renderers may honour or flatten.
    """

    type: ClassVar[str] = "container"
    tag: str = "block"
    children: list[Node] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        out = self._base_dict()
        out["tag"] = self.tag
        out["children"] = [c.to_dict() for c in self.children]
        return out

    def validate(self) -> None:
        if self.tag not in CONTAINER_TAGS:
            raise LlreprValidationError(
                f"container.tag {self.tag!r} not in {sorted(CONTAINER_TAGS)}"
            )
        for child in self.children:
            child.validate()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Container":
        return cls(
            tag=str(data.get("tag", "block")),
            children=[node_from_dict(c) for c in data.get("children", [])],
            **cls._common_kwargs(data),
        )


# ---------------------------------------------------------------------------
# Node dispatch
# ---------------------------------------------------------------------------

_NODE_REGISTRY: dict[str, type[Node]] = {
    Text.type: Text,
    Heading.type: Heading,
    ListNode.type: ListNode,
    Table.type: Table,
    CodeBlock.type: CodeBlock,
    Figure.type: Figure,
    Panel.type: Panel,
    Container.type: Container,
}


def node_from_dict(data: dict[str, Any]) -> Node:
    """Deserialise a single node, dispatching on its ``type`` (fail-closed)."""
    if not isinstance(data, dict):
        raise LlreprValidationError(f"node must be an object, got {type(data).__name__}")
    node_type = data.get("type")
    if node_type not in _NODE_REGISTRY:
        raise LlreprValidationError(
            f"unknown node type {node_type!r}; core catalog is {sorted(NODE_TYPES)}"
        )
    return _NODE_REGISTRY[node_type].from_dict(data)  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Document envelope
# ---------------------------------------------------------------------------

@dataclass
class Document:
    """The llrepr envelope: a versioned schema id, extension declarations, and a root node.

    ``extensions_required`` lists extensions a consumer **must** understand to
    render faithfully; a writer that cannot raises
    :class:`LlreprCapabilityError`.  ``extensions_used`` is the superset
    (required ∪ optional).
    """

    root: Node
    rep_schema: str = LLREPR_SCHEMA_VERSION
    extensions_used: list[str] = field(default_factory=list)
    extensions_required: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"repSchema": self.rep_schema, "root": self.root.to_dict()}
        if self.extensions_used:
            out["extensionsUsed"] = list(self.extensions_used)
        if self.extensions_required:
            out["extensionsRequired"] = list(self.extensions_required)
        return out

    def validate(self) -> None:
        """Fail-closed structural validation of the whole document."""
        if not isinstance(self.rep_schema, str) or not self.rep_schema.startswith("llrepr/"):
            raise LlreprValidationError(f"repSchema must be 'llrepr/<version>', got {self.rep_schema!r}")
        if not isinstance(self.root, Node):
            raise LlreprValidationError("document root must be a Node")
        # required must be a subset of used (glTF invariant)
        missing = set(self.extensions_required) - set(self.extensions_used)
        if missing:
            raise LlreprValidationError(
                f"extensionsRequired not in extensionsUsed: {sorted(missing)}"
            )
        self.root.validate()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Document":
        if not isinstance(data, dict):
            raise LlreprValidationError("document must be an object")
        if "root" not in data:
            raise LlreprValidationError("document missing 'root'")
        doc = cls(
            root=node_from_dict(data["root"]),
            rep_schema=str(data.get("repSchema", LLREPR_SCHEMA_VERSION)),
            extensions_used=list(data.get("extensionsUsed", [])),
            extensions_required=list(data.get("extensionsRequired", [])),
        )
        doc.validate()
        return doc

    # -- ergonomic constructor ---------------------------------------------

    @classmethod
    def of(cls, *children: Node, **kwargs: Any) -> "Document":
        """Build a ``document``-tagged container root from top-level children."""
        return cls(root=Container(tag="document", children=list(children)), **kwargs)
