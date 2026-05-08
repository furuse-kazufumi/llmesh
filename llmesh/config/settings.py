"""LLMeshSettings — persistent configuration for LLMesh node behaviour.

Covers:
  - Circuit breaker thresholds
  - SmartNodeSelector tuning
  - Fairness policy thresholds
  - Fanout / consensus parameters

All fields have sensible defaults so the file is optional — if absent the
defaults apply everywhere.  Dotted key notation is supported for the CLI:
``cb.failure_threshold`` resolves to the field ``cb_failure_threshold``.

Security invariants:
- No shell=True, eval, exec, pickle anywhere
- Paths never interpolated into shell commands
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields as dc_fields
from pathlib import Path

_DEFAULT_PATH = Path("config") / "settings.json"


@dataclass
class LLMeshSettings:
    # -- Circuit breaker -------------------------------------------------
    cb_failure_threshold: int   = 3
    cb_recovery_timeout:  float = 60.0

    # -- SmartNodeSelector -----------------------------------------------
    candidate_multiplier: int   = 3
    max_latency_ms:       float = 5000.0

    # -- Fairness policy -------------------------------------------------
    fairness_enabled:           bool  = True
    fairness_normal_threshold:  float = 0.5
    fairness_low_priority:      float = 0.3
    fairness_rate_limited:      float = 0.1
    fairness_exclude_after:     int   = 10
    fairness_max_excluded_size: int   = 10_000

    # -- Fanout / consensus ---------------------------------------------
    fanout_k:       int = 1
    fanout_timeout: int = 60

    # -- Adapter plugins ------------------------------------------------
    # Each entry: "module.path:ClassName=protocol_name"
    # Loaded at node startup via NodeClient.apply_settings() or the CLI.
    adapter_plugins: list = field(default_factory=list)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, path: str | Path | None = None) -> "LLMeshSettings":
        """Load settings from JSON file.  Missing keys get class defaults."""
        p = Path(path) if path else _DEFAULT_PATH
        if not p.exists():
            return cls()
        try:
            data = json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            return cls()
        known = {f.name for f in dc_fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self, path: str | Path | None = None) -> None:
        """Write settings to JSON file atomically."""
        p = Path(path) if path else _DEFAULT_PATH
        tmp = p.with_suffix(".tmp")
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(asdict(self), indent=2))
            os.replace(tmp, p)
        except OSError:
            tmp.unlink(missing_ok=True)
            raise

    # ------------------------------------------------------------------
    # Runtime mutation (used by the CLI)
    # ------------------------------------------------------------------

    def set_value(self, key: str, raw_value: str) -> None:
        """Set a field by dotted or flat name and coerce the value to the field type.

        Dotted names (e.g. ``cb.failure_threshold``) are normalised to
        underscore form (``cb_failure_threshold``) before lookup.

        Raises:
            KeyError:   Unknown setting name.
            ValueError: Cannot coerce raw_value to the field's type.
        """
        flat_key = key.replace(".", "_")
        field_map = {f.name: f for f in dc_fields(self)}
        if flat_key not in field_map:
            valid = ", ".join(sorted(field_map))
            raise KeyError(
                f"Unknown setting {key!r}. Valid keys: {valid}"
            )
        current = getattr(self, flat_key)
        if isinstance(current, list):
            raise ValueError(
                f"Setting {key!r} is a list - use 'plugin add/remove' commands instead"
            )
        try:
            if isinstance(current, bool):
                coerced: object = raw_value.lower() in ("true", "1", "yes", "on")
            elif isinstance(current, int):
                coerced = int(raw_value)
            elif isinstance(current, float):
                coerced = float(raw_value)
            else:
                coerced = raw_value
        except ValueError:
            raise ValueError(
                f"Cannot convert {raw_value!r} to {type(current).__name__} "
                f"for setting {key!r}"
            )
        setattr(self, flat_key, coerced)

    def as_table(self) -> str:
        """Return a human-readable table of all settings."""
        rows = []
        for f in dc_fields(self):
            dotted = f.name.replace("_", ".", 1)  # cb_failure_threshold → cb.failure_threshold
            rows.append(f"  {dotted:<40} {getattr(self, f.name)}")
        return "\n".join(rows)
