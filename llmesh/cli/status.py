"""llmesh status — runtime status snapshot (v2.7 — G-1.1).

Inspects the running process to report adapter / pipeline / metrics
state.  Designed to be cheap to call repeatedly; suitable for cron
or `watch -n 1 llmesh status`.

Output schema (JSON mode)::

    {
        "version": "2.7.0",
        "python": "3.11.x",
        "platform": "Windows",
        "rust_extension": true,
        "adapters_imported": ["modbus", "opcua", ...],
        "default_metrics_port": 9100,
        "edge_profile": "workstation"
    }

Security invariants
-------------------
- Read-only inspection; no mutation of running state.
"""
from __future__ import annotations

import argparse
import importlib
import json
import platform
import sys
from dataclasses import dataclass, asdict
from typing import Any

# Adapter modules to probe (matches REQUIREMENTS Volume A–L).
_ADAPTER_MODULES: tuple[str, ...] = (
    "llmesh.industrial.modbus_adapter",
    "llmesh.industrial.serial_adapter",
    "llmesh.industrial.opcua_adapter",
    "llmesh.industrial.mqtt_adapter",
    "llmesh.industrial.ethercat_adapter",
    "llmesh.industrial.can_adapter",
    "llmesh.industrial.bacnet_adapter",
)


@dataclass
class StatusSnapshot:
    version: str
    python: str
    platform: str
    machine: str
    rust_extension: bool
    rust_version: str
    adapters_importable: list[str]
    edge_profile: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _llmesh_version() -> str:
    try:
        from importlib.metadata import version
        return version("llmesh")
    except Exception:
        return "0.0.0+local"


def run_status() -> StatusSnapshot:
    rust_avail = False
    rust_ver = ""
    try:
        import llmesh_rust
        rust_avail = True
        rust_ver = getattr(llmesh_rust, "__version__", "?")
    except ImportError:
        pass

    importable: list[str] = []
    for mod in _ADAPTER_MODULES:
        try:
            importlib.import_module(mod)
            importable.append(mod.rsplit(".", 1)[1].replace("_adapter", ""))
        except Exception:
            pass

    try:
        from llmesh.industrial.edge_profile import detect_recommended_preset
        edge = detect_recommended_preset().value
    except Exception:
        edge = "unknown"

    return StatusSnapshot(
        version=_llmesh_version(),
        python=sys.version.split()[0],
        platform=platform.system(),
        machine=platform.machine(),
        rust_extension=rust_avail,
        rust_version=rust_ver,
        adapters_importable=importable,
        edge_profile=edge,
    )


def render_text(snap: StatusSnapshot) -> str:
    rust_line = (f"Rust ext  : {snap.rust_version} (active)"
                 if snap.rust_extension else "Rust ext  : not built (pure-Python)")
    return "\n".join([
        f"LLMesh    : {snap.version}",
        f"Python    : {snap.python}",
        f"Platform  : {snap.platform} ({snap.machine})",
        rust_line,
        f"Edge tier : {snap.edge_profile}",
        f"Adapters  : {', '.join(snap.adapters_importable) or '(none importable)'}",
    ])


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="LLMesh status snapshot")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    snap = run_status()
    if args.json:
        print(json.dumps(snap.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(render_text(snap))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
