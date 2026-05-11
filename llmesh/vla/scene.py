"""Scene representation + text observation parser (Phase 9).

Phase 9's PoC operates on a text-only observation: a string such as

    turtle at (2.0, 3.5), red_flag at (5.0, 5.0), wall at (4.0, 4.0)

This module parses that string into :class:`SceneState` + a list of
:class:`SceneObject` references, giving the rule-based mock agent a
structured world model to reason about.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Match ``name at (x, y)`` with floats, e.g. ``turtle at (2.0, 3.5)``.
_OBJ_RE = re.compile(
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s+at\s+\(\s*"
    r"(?P<x>-?\d+(?:\.\d+)?)\s*,\s*"
    r"(?P<y>-?\d+(?:\.\d+)?)\s*\)"
)


@dataclass(frozen=True)
class SceneObject:
    """One named object with a 2-D position."""

    name: str
    x: float
    y: float


@dataclass(frozen=True)
class SceneState:
    """Parsed scene.

    ``self_object`` is the agent's own body (``turtle`` by convention)
    and ``objects`` is every other detected object. Walls live in
    ``walls`` when their name starts with ``wall``.
    """

    self_object: SceneObject | None
    objects: tuple[SceneObject, ...] = ()
    walls: tuple[SceneObject, ...] = ()
    raw: str = ""

    @property
    def has_self(self) -> bool:
        return self.self_object is not None

    def find(self, name_prefix: str) -> SceneObject | None:
        """Return the first non-self object whose name starts with ``name_prefix``."""
        pfx = name_prefix.lower()
        for obj in self.objects:
            if obj.name.lower().startswith(pfx):
                return obj
        return None


def parse_scene_text(text: str) -> SceneState:
    """Extract ``SceneObject`` tuples from ``"turtle at (x, y), ..."`` text.

    Unrecognised tokens are ignored. The first match for ``turtle``
    (case-insensitive prefix) becomes :attr:`SceneState.self_object`;
    everything else lands in ``objects`` (walls are also surfaced in
    the dedicated ``walls`` field).
    """
    self_obj: SceneObject | None = None
    others: list[SceneObject] = []
    walls: list[SceneObject] = []
    for match in _OBJ_RE.finditer(text or ""):
        name = match.group("name")
        try:
            x = float(match.group("x"))
            y = float(match.group("y"))
        except ValueError:
            continue
        obj = SceneObject(name=name, x=x, y=y)
        if name.lower().startswith("turtle") and self_obj is None:
            self_obj = obj
            continue
        others.append(obj)
        if name.lower().startswith("wall"):
            walls.append(obj)
    return SceneState(
        self_object=self_obj,
        objects=tuple(others),
        walls=tuple(walls),
        raw=text or "",
    )


__all__ = ["SceneObject", "SceneState", "parse_scene_text"]
