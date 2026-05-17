"""Supply chain incident database lookup.

Tracks known supply chain attacks / suspicious-maintainer incidents that
should bump a package's supply_risk level above whatever the static
origin DB says. Bundled DB lives at ``llmesh/data/risk-db.toml``.

This is intentionally minimal in α; expansion to OSV / Sonatype OSS
Index integration belongs to Phase 2.
"""
from __future__ import annotations

import dataclasses
import tomllib
from pathlib import Path

_BUNDLED_RISK_DB = Path(__file__).resolve().parent.parent / "data" / "risk-db.toml"


@dataclasses.dataclass(frozen=True)
class RiskEntry:
    """One known incident."""

    name: str
    severity: str  # "HIGH" / "MEDIUM" / "LOW"
    incident_date: str = ""
    summary: str = ""
    affected_versions: str = ""


class SupplyRisk:
    """Bundled incident database."""

    def __init__(self, path: Path | None = None) -> None:
        self._entries: dict[str, RiskEntry] = {}
        if path is None:
            path = _BUNDLED_RISK_DB
        if path.exists():
            with path.open("rb") as fp:
                data = tomllib.load(fp)
            for name, raw in data.items():
                if not isinstance(raw, dict):
                    continue
                self._entries[name.lower()] = RiskEntry(
                    name=name,
                    severity=str(raw.get("severity", "LOW")),
                    incident_date=str(raw.get("incident_date", "")),
                    summary=str(raw.get("summary", "")),
                    affected_versions=str(raw.get("affected_versions", "")),
                )

    def get(self, name: str) -> RiskEntry | None:
        return self._entries.get(name.lower())

    def names(self) -> list[str]:
        return sorted(self._entries.keys())
