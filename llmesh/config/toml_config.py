"""LLMeshTomlConfig — unified TOML configuration for LLMesh nodes (v1.0.0).

Replaces scattered environment variables with a single ``llmesh.toml`` file.
Falls back to environment variables and class defaults when the file is absent,
so existing deployments need no changes.

File format example::

    [node]
    id = "my-node"
    data_level = 0

    [adapters]
    enabled = ["http", "tcp"]

    [adapters.http]
    host = "0.0.0.0"
    port = 8080

    [adapters.ssh]
    host = "0.0.0.0"
    port = 2222

    [security]
    ntp_servers = ["pool.ntp.org", "time.cloudflare.com"]
    max_clock_drift_s = 10
    rate_limit_rate = 10.0
    rate_limit_burst = 20.0

    [circuit_breaker]
    failure_threshold = 3
    recovery_timeout = 60.0

    [telnet]
    enabled = false          # still requires env-var double opt-in

Security invariants:
- No shell=True, eval, exec, pickle anywhere.
- Path values from config are never interpolated into shell commands.
- Unknown TOML keys are silently ignored (forward-compatible).
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from llmesh.config.industrial_config import IndustrialConfig

_DEFAULT_TOML_PATH = Path("llmesh.toml")


@dataclass
class AdapterConfig:
    """Per-adapter host/port settings."""
    host: str = "0.0.0.0"
    port: int = 0           # 0 = not configured (adapter uses its own default)
    enabled: bool = True    # individual enable flag (overrides top-level list)
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AdapterConfig":
        return cls(
            host=str(d.get("host", "0.0.0.0")),
            port=int(d.get("port", 0)),
            enabled=bool(d.get("enabled", True)),
            extra={k: v for k, v in d.items() if k not in ("host", "port", "enabled")},
        )


@dataclass
class SecurityConfig:
    """Security-related settings (NTP, rate limit)."""
    ntp_servers: list[str] = field(
        default_factory=lambda: ["pool.ntp.org", "time.cloudflare.com"]
    )
    max_clock_drift_s: int = 10
    ntp_timeout_s: int = 5
    rate_limit_rate: float = 10.0
    rate_limit_burst: float = 20.0

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SecurityConfig":
        return cls(
            ntp_servers=list(d.get("ntp_servers", ["pool.ntp.org", "time.cloudflare.com"])),
            max_clock_drift_s=int(d.get("max_clock_drift_s", 10)),
            ntp_timeout_s=int(d.get("ntp_timeout_s", 5)),
            rate_limit_rate=float(d.get("rate_limit_rate", 10.0)),
            rate_limit_burst=float(d.get("rate_limit_burst", 20.0)),
        )


@dataclass
class CircuitBreakerConfig:
    """Circuit breaker thresholds."""
    failure_threshold: int = 3
    recovery_timeout: float = 60.0

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CircuitBreakerConfig":
        return cls(
            failure_threshold=int(d.get("failure_threshold", 3)),
            recovery_timeout=float(d.get("recovery_timeout", 60.0)),
        )


@dataclass
class LLMeshTomlConfig:
    """Unified LLMesh node configuration loaded from llmesh.toml.

    Usage::

        cfg = LLMeshTomlConfig.load()          # loads llmesh.toml or returns defaults
        cfg = LLMeshTomlConfig.load("custom.toml")
        http_cfg = cfg.adapter("http")         # AdapterConfig(host=..., port=...)
        enabled = cfg.enabled_adapters         # ["http", "tcp"]

    All fields have defaults — if llmesh.toml is absent the node starts with
    the same behaviour as before v1.0.0.
    """

    node_id: str = ""
    data_level: int = 0
    enabled_adapters: list[str] = field(default_factory=list)
    adapters: dict[str, AdapterConfig] = field(default_factory=dict)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    circuit_breaker: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)
    industrial: IndustrialConfig = field(default_factory=IndustrialConfig)
    # Extra top-level sections are preserved for forward compatibility
    _extra: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, path: str | Path | None = None) -> "LLMeshTomlConfig":
        """Load config from *path* (default: ``llmesh.toml`` in cwd).

        Returns defaults if the file is absent or unreadable.
        """
        p = Path(path) if path else _DEFAULT_TOML_PATH
        if not p.exists():
            return cls._from_env()
        try:
            raw = tomllib.loads(p.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, OSError):
            return cls._from_env()
        return cls._from_dict(raw)

    @classmethod
    def _from_env(cls) -> "LLMeshTomlConfig":
        """Build a minimal config from environment variables (legacy fallback)."""
        return cls(
            node_id=os.environ.get("LLMESH_NODE_ID", ""),
            data_level=int(os.environ.get("LLMESH_DATA_LEVEL", "0")),
            security=SecurityConfig(
                ntp_servers=os.environ.get(
                    "LLMESH_NTP_SERVERS", "pool.ntp.org"
                ).split(","),
                max_clock_drift_s=int(
                    os.environ.get("LLMESH_MAX_CLOCK_DRIFT_S", "10")
                ),
                ntp_timeout_s=int(
                    os.environ.get("LLMESH_NTP_TIMEOUT_S", "5")
                ),
            ),
        )

    @classmethod
    def _from_dict(cls, raw: dict[str, Any]) -> "LLMeshTomlConfig":
        node = raw.get("node", {})
        adapters_raw = raw.get("adapters", {})

        enabled: list[str] = list(adapters_raw.get("enabled", []))
        adapter_cfgs: dict[str, AdapterConfig] = {}
        for name, val in adapters_raw.items():
            if name == "enabled":
                continue
            if isinstance(val, dict):
                adapter_cfgs[name] = AdapterConfig.from_dict(val)

        security_raw = raw.get("security", {})
        cb_raw = raw.get("circuit_breaker", {})

        industrial_raw = raw.get("industrial", {})
        known = {"node", "adapters", "security", "circuit_breaker", "industrial"}
        extra = {k: v for k, v in raw.items() if k not in known}

        return cls(
            node_id=str(node.get("id", os.environ.get("LLMESH_NODE_ID", ""))),
            data_level=int(node.get("data_level", 0)),
            enabled_adapters=enabled,
            adapters=adapter_cfgs,
            security=SecurityConfig.from_dict(security_raw),
            circuit_breaker=CircuitBreakerConfig.from_dict(cb_raw),
            industrial=IndustrialConfig.from_dict(industrial_raw),
            _extra=extra,
        )

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def adapter(self, name: str) -> AdapterConfig:
        """Return AdapterConfig for *name*, or a default instance if not configured."""
        return self.adapters.get(name, AdapterConfig())

    def is_adapter_enabled(self, name: str) -> bool:
        """Return True if *name* appears in enabled_adapters (or list is empty = all enabled)."""
        if not self.enabled_adapters:
            return True
        return name in self.enabled_adapters

    def to_dict(self) -> dict[str, Any]:
        """Serialise back to a TOML-compatible dict (for testing / export)."""
        adapters_section: dict[str, Any] = {}
        if self.enabled_adapters:
            adapters_section["enabled"] = self.enabled_adapters
        for name, ac in self.adapters.items():
            entry: dict[str, Any] = {"host": ac.host}
            if ac.port:
                entry["port"] = ac.port
            if not ac.enabled:
                entry["enabled"] = False
            entry.update(ac.extra)
            adapters_section[name] = entry

        d: dict[str, Any] = {
            "node": {"id": self.node_id, "data_level": self.data_level},
            "adapters": adapters_section,
            "security": {
                "ntp_servers": self.security.ntp_servers,
                "max_clock_drift_s": self.security.max_clock_drift_s,
                "ntp_timeout_s": self.security.ntp_timeout_s,
                "rate_limit_rate": self.security.rate_limit_rate,
                "rate_limit_burst": self.security.rate_limit_burst,
            },
            "circuit_breaker": {
                "failure_threshold": self.circuit_breaker.failure_threshold,
                "recovery_timeout": self.circuit_breaker.recovery_timeout,
            },
            **self._extra,
        }
        if self.industrial.is_configured():
            d["industrial"] = self.industrial.to_dict()
        return d
