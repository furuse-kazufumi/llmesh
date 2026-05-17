"""llmesh deps audit — dependency origin + supply-risk audit CLI.

Usage::

    python -m llmesh.cli.deps_audit                  # audit installed env, table out
    python -m llmesh.cli.deps_audit --json           # JSON output
    python -m llmesh.cli.deps_audit --file requirements.txt   # audit requirements file
    python -m llmesh.cli.deps_audit --fail-on US     # exit 1 if any US package present

Strategy: this command is the L1-market entry point for EAR-clean /
sanction-clean enterprise procurement reviewers. The output deliberately
foregrounds the per-package country-of-origin column so a procurement
officer can scan it in seconds.

Strategy reference: ``D:/projects/audit/STRATEGY_EAR_LOCAL_LLM_2026-05-17_PART6_DEPS_AUDIT.md``
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

from llmesh.supply_chain import (
    Origins,
    OriginEntry,
    SupplyRisk,
    audit_installed,
    audit_requirements_file,
)
from llmesh.supply_chain.origins import origin_breakdown, risk_breakdown


def _merge_risk(entries: list[OriginEntry], risk_db: SupplyRisk) -> list[OriginEntry]:
    """Bump supply_risk based on known incidents from risk-db."""
    out: list[OriginEntry] = []
    for e in entries:
        incident = risk_db.get(e.name)
        if incident is not None and _severity_rank(incident.severity) > _severity_rank(e.supply_risk):
            # Re-emit with elevated risk
            note = e.supply_risk_notes
            extra = f"{incident.incident_date}: {incident.summary}"
            note = f"{note}; {extra}" if note else extra
            out.append(
                OriginEntry(
                    name=e.name,
                    origin=e.origin,
                    maintainer=e.maintainer,
                    verified=e.verified,
                    notes=e.notes,
                    supply_risk=incident.severity.lower(),
                    supply_risk_notes=note,
                )
            )
        else:
            out.append(e)
    return out


def _severity_rank(level: str) -> int:
    table = {"low": 1, "medium": 2, "high": 3, "unknown": 0}
    return table.get(level.lower(), 0)


def _format_table(entries: list[OriginEntry]) -> str:
    """Render a human-readable column table."""
    if not entries:
        return "(no packages found)\n"
    headers = ("PACKAGE", "ORIGIN", "RISK", "NOTES")
    rows: list[tuple[str, str, str, str]] = [headers]
    for e in entries:
        risk = e.supply_risk.upper() if e.supply_risk != "low" else ""
        # Truncate notes for column display
        notes = e.notes
        if len(notes) > 64:
            notes = notes[:61] + "..."
        rows.append((e.name, e.origin, risk, notes))
    widths = [max(len(r[i]) for r in rows) for i in range(len(headers))]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    sep = "  ".join("-" * w for w in widths)
    out_lines = [fmt.format(*rows[0]), sep]
    out_lines.extend(fmt.format(*r) for r in rows[1:])
    return "\n".join(out_lines) + "\n"


def _format_summary(entries: list[OriginEntry]) -> str:
    if not entries:
        return ""
    o_break = origin_breakdown(entries)
    r_break = risk_breakdown(entries)
    parts = [
        f"Total dependencies     : {len(entries)}",
        "Origin breakdown       : "
        + " | ".join(f"{k} {v}" for k, v in sorted(o_break.items())),
        "Supply risk            : "
        + f"HIGH {r_break.get('high', 0)} | MEDIUM {r_break.get('medium', 0)}"
        + f" | LOW {r_break.get('low', 0)} | UNKNOWN {r_break.get('unknown', 0)}",
        f"Audit timestamp        : {datetime.datetime.now(datetime.UTC).isoformat()}",
    ]
    return "\n" + "\n".join(parts) + "\n"


def _to_json(entries: list[OriginEntry]) -> str:
    payload = {
        "metadata": {
            "tool": "llmesh deps audit",
            "version": "0.1.0-alpha",
            "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
        },
        "summary": {
            "total": len(entries),
            "origin_breakdown": origin_breakdown(entries),
            "supply_risk": risk_breakdown(entries),
        },
        "dependencies": [
            {
                "name": e.name,
                "origin": e.origin,
                "maintainer": e.maintainer,
                "verified": e.verified,
                "notes": e.notes,
                "supply_risk": e.supply_risk,
                "supply_risk_notes": e.supply_risk_notes,
            }
            for e in entries
        ],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def _ensure_utf8_stdout() -> None:
    """Force stdout to UTF-8 so Windows cp932 doesn't choke on em-dashes etc."""
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, ValueError):  # pragma: no cover - older Python / pipes
        pass


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_stdout()
    parser = argparse.ArgumentParser(
        prog="llmesh deps audit",
        description="Audit Python dependency origin + supply-chain risk (EAR-clean check).",
    )
    parser.add_argument(
        "--file",
        type=Path,
        default=None,
        help="Audit a requirements.txt instead of the installed environment.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output JSON instead of human-readable table.",
    )
    parser.add_argument(
        "--fail-on",
        default=None,
        help="Exit 1 if any package matches this origin code (e.g. US).",
    )
    parser.add_argument(
        "--override",
        type=Path,
        default=None,
        help="Path to a user origin override TOML.",
    )
    args = parser.parse_args(argv)

    origins = Origins(override_path=args.override)
    risk_db = SupplyRisk()

    if args.file is not None:
        if not args.file.exists():
            print(f"error: file not found: {args.file}", file=sys.stderr)
            return 2
        entries = audit_requirements_file(args.file, origins=origins)
    else:
        entries = audit_installed(origins=origins)

    entries = _merge_risk(entries, risk_db)

    if args.json:
        sys.stdout.write(_to_json(entries) + "\n")
    else:
        sys.stdout.write(_format_table(entries))
        sys.stdout.write(_format_summary(entries))

    if args.fail_on is not None:
        target = args.fail_on.upper()
        for e in entries:
            if e.origin.upper() == target:
                return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
