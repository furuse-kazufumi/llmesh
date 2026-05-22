"""llmesh doctor — environment health check (v2.7 — G-4.1).

Runs a battery of diagnostics that verify the host environment is
ready for LLMesh:

    * Python version & architecture
    * Optional dependency installation (pymodbus / asyncua / paho-mqtt /
      pysoem / python-can / bacpypes3 / Pillow / numpy / scipy / hypothesis)
    * Rust extension (llmesh_rust) detection
    * Memory tier recommendation
    * NTP clock drift (best-effort)
    * Required network ports availability for adapters

Output: human-readable text or JSON (`--json`).

Security invariants
-------------------
- Read-only checks; no writes, no shell evaluation.
- No external network unless explicitly enabled (`--check-ntp`).
"""
from __future__ import annotations

import argparse
import importlib
import json
import platform
import socket
import sys
from dataclasses import dataclass, field, asdict
from typing import Any

# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

# Required for core LLMesh.  None of these may be skipped.
_REQUIRED_PACKAGES: tuple[str, ...] = (
    "cryptography",
    "jsonschema",
    "fastapi",
    "uvicorn",
)

# Optional packages keyed by extras; reporting is informational.
_OPTIONAL_PACKAGES: dict[str, str] = {
    "pymodbus":     "industrial",
    "pyserial":     "industrial",
    "asyncua":      "industrial",
    "paho.mqtt":    "industrial",
    "numpy":        "industrial",
    "scipy":        "industrial",
    "pysoem":       "ethercat",
    "can":          "can",
    "bacpypes3":    "bacnet",
    "PIL":          "vision",
    "hypothesis":   "dev",
    "ruff":         "dev",
    "bandit":       "dev",
    "coverage":     "dev",
    "llmesh_rust":  "rust acceleration",
}

# Default ports to probe; only checks that bind succeeds (no traffic sent).
_PORT_PROBES: dict[str, int] = {
    "http":   8000,
    "metrics": 9100,
    "mcp":    50051,
}


@dataclass
class DoctorCheck:
    name: str
    status: str         # "ok" | "warn" | "fail" | "skip"
    detail: str = ""


@dataclass
class DoctorReport:
    python_version: str
    platform: str
    machine: str
    checks: list[DoctorCheck] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.status != "fail" for c in self.checks)

    def add(self, name: str, status: str, detail: str = "") -> None:
        self.checks.append(DoctorCheck(name=name, status=status, detail=detail))

    def to_dict(self) -> dict[str, Any]:
        return {
            "python_version": self.python_version,
            "platform": self.platform,
            "machine": self.machine,
            "ok": self.ok,
            "checks": [asdict(c) for c in self.checks],
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _try_import(name: str) -> bool:
    try:
        importlib.import_module(name)
        return True
    except ImportError:
        return False


def _probe_port_bindable(port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def run_doctor(check_ntp: bool = False, check_ports: bool = True) -> DoctorReport:
    """Execute every diagnostic check; returns a DoctorReport."""
    report = DoctorReport(
        python_version=sys.version.split()[0],
        platform=platform.system(),
        machine=platform.machine(),
    )

    # Python version
    if sys.version_info < (3, 11):
        report.add("python>=3.11", "fail",
                   f"got {report.python_version}, need ≥ 3.11")
    else:
        report.add("python>=3.11", "ok", report.python_version)

    # Required packages
    for pkg in _REQUIRED_PACKAGES:
        if _try_import(pkg):
            report.add(f"required: {pkg}", "ok")
        else:
            report.add(f"required: {pkg}", "fail",
                       f"pip install {pkg} (or 'pip install -e .')")

    # Optional packages
    for pkg, extras in _OPTIONAL_PACKAGES.items():
        if _try_import(pkg):
            report.add(f"optional: {pkg}", "ok", f"({extras})")
        else:
            report.add(f"optional: {pkg}", "warn",
                       f"missing — install with: pip install llmesh[{extras}]")

    # Edge profile recommendation
    try:
        from llmesh.industrial.edge_profile import detect_recommended_preset
        rec = detect_recommended_preset()
        report.add("edge profile", "ok", f"recommended: {rec.value}")
    except Exception as exc:
        report.add("edge profile", "skip", f"detection failed: {exc}")

    # Port probes
    if check_ports:
        for name, port in _PORT_PROBES.items():
            ok = _probe_port_bindable(port)
            if ok:
                report.add(f"port {name}/{port}", "ok", "bindable")
            else:
                report.add(f"port {name}/{port}", "warn", "in use — adapter may fail")

    # NTP clock check (best-effort, only if requested)
    if check_ntp:
        try:
            from llmesh.security.clock import check_drift_ok  # type: ignore[import-not-found]
            ok, drift = check_drift_ok()
            if ok:
                report.add("ntp drift", "ok", f"drift={drift:.2f}s")
            else:
                report.add("ntp drift", "warn", f"drift={drift:.2f}s exceeds threshold")
        except Exception as exc:
            report.add("ntp drift", "skip", f"check failed: {exc}")

    return report


def render_text(report: DoctorReport) -> str:
    lines = [
        "LLMesh doctor",
        "=" * 40,
        f"Python   : {report.python_version}",
        f"Platform : {report.platform}",
        f"Machine  : {report.machine}",
        "",
        "Checks:",
    ]
    for c in report.checks:
        icon = {"ok": "OK ", "warn": "WARN", "fail": "FAIL", "skip": "SKIP"}.get(c.status, "?")
        line = f"  [{icon}] {c.name}"
        if c.detail:
            line += f" — {c.detail}"
        lines.append(line)
    lines.append("")
    lines.append("RESULT: " + ("HEALTHY" if report.ok else "ISSUES DETECTED"))
    return "\n".join(lines)


def _ensure_utf8_stdout() -> None:
    """Force stdout to UTF-8 so Windows cp932 doesn't choke on em-dashes etc.

    Mirrors the helper in ``llmesh.cli.sbom`` / ``llmesh.cli.deps_audit``.
    Without this, ``render_text`` containing ``\\u2014`` (em-dash) crashes
    with UnicodeEncodeError when launched from the Windows default console.
    """
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, ValueError):  # pragma: no cover — older Python / pipes
        pass


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_stdout()
    p = argparse.ArgumentParser(description="LLMesh environment doctor")
    p.add_argument("--json", action="store_true", help="output JSON")
    p.add_argument("--check-ntp", action="store_true", help="probe NTP drift")
    p.add_argument("--no-ports", action="store_true", help="skip port probes")
    args = p.parse_args(argv)

    rep = run_doctor(check_ntp=args.check_ntp, check_ports=not args.no_ports)
    if args.json:
        print(json.dumps(rep.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(render_text(rep))
    return 0 if rep.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
