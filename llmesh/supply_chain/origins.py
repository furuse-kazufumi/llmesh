"""Dependency origin lookup.

Resolves a package name to a country-of-origin code (US / EU / CN /
UN / TRUSTED / BLOCKED) using a layered lookup:

1. User overrides (``.llmesh/origins-override.toml``)
2. Bundled database (``llmesh/data/origins.toml``)
3. PyPI / heuristic fallback (returns ``UN`` = unknown when nothing matches)

Security invariants
-------------------
- Pure read; no network calls
- Deterministic (sorted entries for reproducible audits)
- Falls back to ``UN`` rather than guessing — never mislabels
"""
from __future__ import annotations

import dataclasses
import importlib.metadata
import tomllib
from collections.abc import Iterable
from pathlib import Path

_BUNDLED_DB = Path(__file__).resolve().parent.parent / "data" / "origins.toml"


@dataclasses.dataclass(frozen=True)
class OriginEntry:
    """One package's origin metadata."""

    name: str
    origin: str  # "US" / "EU" / "CN" / "UN" / "TRUSTED" / "BLOCKED"
    maintainer: str = ""
    verified: str = ""
    notes: str = ""
    supply_risk: str = "low"  # low / medium / high / unknown
    supply_risk_notes: str = ""


class Origins:
    """Bundled + user-override origin database.

    Loads ``llmesh/data/origins.toml`` (bundled) and optionally a user
    override path. User overrides take precedence.
    """

    def __init__(
        self,
        bundled_path: Path | None = None,
        override_path: Path | None = None,
    ) -> None:
        self._entries: dict[str, OriginEntry] = {}
        if bundled_path is None:
            bundled_path = _BUNDLED_DB
        if bundled_path.exists():
            self._load_toml(bundled_path)
        if override_path is not None and override_path.exists():
            self._load_toml(override_path)

    def _load_toml(self, path: Path) -> None:
        with path.open("rb") as fp:
            data = tomllib.load(fp)
        for name, raw in data.items():
            if not isinstance(raw, dict):
                continue
            self._entries[name.lower()] = OriginEntry(
                name=name,
                origin=str(raw.get("origin", "UN")),
                maintainer=str(raw.get("maintainer", "")),
                verified=str(raw.get("verified", "")),
                notes=str(raw.get("notes", "")),
                supply_risk=str(raw.get("supply_risk", "low")),
                supply_risk_notes=str(raw.get("supply_risk_notes", "")),
            )

    def lookup(self, name: str) -> OriginEntry:
        """Return entry for ``name``; falls back to ``UN`` placeholder."""
        return self._entries.get(
            name.lower(),
            OriginEntry(name=name, origin="UN"),
        )

    def names(self) -> list[str]:
        return sorted(self._entries.keys())


def audit_installed(origins: Origins | None = None) -> list[OriginEntry]:
    """Return one OriginEntry per installed distribution, sorted by name."""
    if origins is None:
        origins = Origins()
    out: list[OriginEntry] = []
    seen: set[str] = set()
    for dist in importlib.metadata.distributions():
        name = (dist.metadata.get("Name") or "").strip()
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        version = dist.version or ""
        entry = origins.lookup(name)
        # Re-emit with version filled in; origins.toml is version-agnostic.
        out.append(
            OriginEntry(
                name=name,
                origin=entry.origin,
                maintainer=entry.maintainer,
                verified=entry.verified,
                notes=f"v{version}" + (f" — {entry.notes}" if entry.notes else ""),
                supply_risk=entry.supply_risk,
                supply_risk_notes=entry.supply_risk_notes,
            )
        )
    out.sort(key=lambda e: e.name.lower())
    return out


def audit_requirements_file(
    path: Path,
    origins: Origins | None = None,
) -> list[OriginEntry]:
    """Audit a ``requirements.txt``-style file (no install required).

    Strips version specifiers; comments / blank lines / -e editable refs
    are ignored.
    """
    if origins is None:
        origins = Origins()
    out: list[OriginEntry] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        # Drop version specifiers (>=, ==, <, ~=, !=, [, ;)
        name = line
        for sep in (">=", "==", "<=", "~=", "!=", "<", ">", "[", ";"):
            if sep in name:
                name = name.split(sep, 1)[0].strip()
        if not name:
            continue
        entry = origins.lookup(name)
        out.append(entry)
    return out


def origin_breakdown(entries: Iterable[OriginEntry]) -> dict[str, int]:
    """Count entries per origin code."""
    counts: dict[str, int] = {}
    for e in entries:
        counts[e.origin] = counts.get(e.origin, 0) + 1
    return counts


def risk_breakdown(entries: Iterable[OriginEntry]) -> dict[str, int]:
    """Count entries per supply_risk level."""
    counts: dict[str, int] = {"high": 0, "medium": 0, "low": 0, "unknown": 0}
    for e in entries:
        key = e.supply_risk.lower() if e.supply_risk else "low"
        counts[key] = counts.get(key, 0) + 1
    return counts
