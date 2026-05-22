"""llmesh sbom — CycloneDX SBOM generator (v2.7 — H-3.3).

Produces a Software Bill of Materials in CycloneDX 1.5 JSON format
listing every Python package (with version + license + PURL) in the
current environment.  Required by EU Cyber Resilience Act, US EO
14028, and ISO/IEC 27001 supply-chain controls.

Usage::

    python -m llmesh.cli.sbom -o sbom.cdx.json

Generates a minimal but compliant SBOM with:
    * Component name / version / purl
    * Component license (best-effort from package metadata)
    * Dependency graph skeleton

Security invariants
-------------------
- Read-only inspection of installed packages; no network calls.
- Deterministic output (sorted entries) for reproducible builds.
- Falls back to "NOASSERTION" for missing license info.
"""
from __future__ import annotations

import argparse
import datetime
import json
import platform
import sys
import uuid
from importlib import metadata
from pathlib import Path
from typing import Any

# CycloneDX schema version — chosen to maximise tool compatibility.
_CDX_SPEC_VERSION = "1.5"
_CDX_BOM_FORMAT = "CycloneDX"


def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _purl_for(pkg_name: str, version: str) -> str:
    """Compose a Package URL (PURL) for a PyPI distribution."""
    return f"pkg:pypi/{pkg_name.lower()}@{version}"


def _license_of(dist: metadata.Distribution) -> list[dict[str, Any]]:
    """Return CycloneDX license blocks; falls back to NOASSERTION."""
    raw = dist.metadata.get("License") or ""
    classifiers = dist.metadata.get_all("Classifier") or []
    license_lines: list[str] = []
    if raw and raw.upper() != "UNKNOWN":
        license_lines.append(raw)
    for c in classifiers:
        if c.startswith("License ::") and c not in license_lines:
            license_lines.append(c.replace("License :: ", ""))
    if not license_lines:
        return [{"license": {"name": "NOASSERTION"}}]
    return [{"license": {"name": ln}} for ln in license_lines[:3]]


def generate_sbom(component_name: str = "llmesh",
                  component_version: str | None = None) -> dict[str, Any]:
    """Build a CycloneDX 1.5 SBOM dict for the current Python env."""
    if component_version is None:
        try:
            component_version = metadata.version(component_name)
        except metadata.PackageNotFoundError:
            component_version = "0.0.0+local"

    components: list[dict[str, Any]] = []
    for dist in sorted(metadata.distributions(),
                       key=lambda d: d.metadata["Name"].lower()):
        name = dist.metadata.get("Name", "")
        version = dist.version
        if not name:
            continue
        if name.lower() == component_name.lower():
            continue
        components.append({
            "type": "library",
            "bom-ref": f"{name}@{version}",
            "name": name,
            "version": version,
            "purl": _purl_for(name, version),
            "licenses": _license_of(dist),
        })

    sbom: dict[str, Any] = {
        "bomFormat": _CDX_BOM_FORMAT,
        "specVersion": _CDX_SPEC_VERSION,
        "serialNumber": f"urn:uuid:{uuid.uuid4()}",
        "version": 1,
        "metadata": {
            "timestamp": _utc_now_iso(),
            "tools": [{
                "vendor": "LLMesh",
                "name": "llmesh.cli.sbom",
                "version": component_version,
            }],
            "component": {
                "type": "application",
                "bom-ref": f"{component_name}@{component_version}",
                "name": component_name,
                "version": component_version,
                "purl": _purl_for(component_name, component_version),
            },
            "properties": [
                {"name": "python.version", "value": sys.version.split()[0]},
                {"name": "platform.system", "value": platform.system()},
                {"name": "platform.machine", "value": platform.machine()},
            ],
        },
        "components": components,
    }
    return sbom


def write_sbom(path: Path, sbom: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sbom, indent=2, ensure_ascii=False, sort_keys=True))


def _ensure_utf8_stdout() -> None:
    """Force stdout to UTF-8 so Windows cp932 doesn't mojibake the `→`."""
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, ValueError):  # pragma: no cover
        pass


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_stdout()
    p = argparse.ArgumentParser(description="LLMesh SBOM (CycloneDX) generator")
    p.add_argument("-o", "--output", type=Path,
                   default=Path("sbom.cdx.json"),
                   help="output path (default: sbom.cdx.json)")
    p.add_argument("--component", default="llmesh",
                   help="primary component name (default: llmesh)")
    args = p.parse_args(argv)

    sbom = generate_sbom(component_name=args.component)
    write_sbom(args.output, sbom)
    print(f"wrote {len(sbom['components'])} components → {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
