"""IndustrialConfig — manufacturing/industrial settings for LLMesh (v1.3.0).

Stored under the [industrial] section of llmesh.toml.  All fields have
safe defaults so existing non-industrial deployments are unaffected.

Security invariants:
  - No shell=True, eval, exec, pickle.
  - Path values (unit_space_dir) are never interpolated into shell commands.
  - Unknown keys are silently ignored (forward-compatible).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Canonical vocabulary sets — used by Setup Wizard and validation.
SUPPORTED_PROTOCOLS: frozenset[str] = frozenset([
    "modbus",       # Modbus TCP / RTU (pymodbus)
    "serial",       # RS-232 / RS-485 (pyserial)
    "opcua",        # OPC Unified Architecture (asyncua)
    "mqtt",         # MQTT v3/v5 (paho-mqtt)
    "ethercat",     # EtherCAT real-time fieldbus
    "canbus",       # CAN / CANopen (python-can)
    "mcp3d",        # 3D/spatial sensors via mcp-3d SDK
    "snmp",         # SNMP (already in LLMesh core)
    "ros2",         # ROS 2 (already in LLMesh core)
    "ros1",         # ROS 1 (already in LLMesh core)
])

SUPPORTED_DEVICE_TYPES: frozenset[str] = frozenset([
    "smt_machine",      # 実装機 / chip mounter
    "aoi",              # 外観検査装置 / automated optical inspection
    "press",            # プレス機
    "robot_arm",        # ロボットアーム
    "cnc",              # CNC machining center
    "conveyor",         # コンベア
    "custom",           # user-defined
])

SUPPORTED_ANALYSIS_METHODS: frozenset[str] = frozenset([
    "mt_method",    # Mahalanobis-Taguchi method (numpy/scipy)
    "spc",          # Statistical Process Control (Xbar-R, CUSUM)
    "llm_report",   # LLM natural-language anomaly report
])

NETWORK_POLICIES: frozenset[str] = frozenset([
    "local_only",   # no egress to the internet — factory default
    "edge_cloud",   # hybrid: local inference + cloud telemetry
])


@dataclass
class IndustrialConfig:
    """Industrial deployment settings.

    Usage (from toml_config.py)::

        cfg = LLMeshTomlConfig.load()
        if cfg.industrial.is_configured():
            protocols = cfg.industrial.protocols
    """

    domain: str = ""                        # "manufacturing", "logistics", "medical", "other"
    device_types: list[str] = field(default_factory=list)
    protocols: list[str] = field(default_factory=list)
    analysis_methods: list[str] = field(default_factory=list)
    network_policy: str = "local_only"
    data_retention_days: int = 90
    unit_space_dir: str = "unit_spaces"     # directory for MT-method normal-unit-space files

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "IndustrialConfig":
        return cls(
            domain=str(d.get("domain", "")),
            device_types=[str(x) for x in d.get("device_types", [])],
            protocols=[str(x) for x in d.get("protocols", [])],
            analysis_methods=[str(x) for x in d.get("analysis_methods", [])],
            network_policy=str(d.get("network_policy", "local_only")),
            data_retention_days=int(d.get("data_retention_days", 90)),
            unit_space_dir=str(d.get("unit_space_dir", "unit_spaces")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "device_types": self.device_types,
            "protocols": self.protocols,
            "analysis_methods": self.analysis_methods,
            "network_policy": self.network_policy,
            "data_retention_days": self.data_retention_days,
            "unit_space_dir": self.unit_space_dir,
        }

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def is_configured(self) -> bool:
        """Return True if the [industrial] section has been populated."""
        return bool(self.domain and self.protocols)

    def uses_protocol(self, name: str) -> bool:
        return name in self.protocols

    def uses_analysis(self, name: str) -> bool:
        return name in self.analysis_methods
