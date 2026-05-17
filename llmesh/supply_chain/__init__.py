"""llmesh supply_chain — dependency origin + supply risk audit.

Provides EAR-clean / sanction-clean dependency verification for L1-L3
regulated enterprise users who need to prove that an llmesh deployment
does not rely on banned or untrusted upstream packages.

This module is the structural differentiator vs LiteLLM / Portkey /
Tabby — US-based competitors structurally cannot ship this feature
because doing so would expose their own US origin as a weakness.

Public API
----------
- :class:`Origins`         — load the bundled origin database
- :class:`SupplyRisk`      — load known supply chain incident database
- :func:`audit_installed`  — audit the currently installed environment

Strategy reference: ``D:/projects/audit/STRATEGY_EAR_LOCAL_LLM_2026-05-17_PART6_DEPS_AUDIT.md``
"""
from __future__ import annotations

from .origins import (
    Origins,
    OriginEntry,
    audit_installed,
    audit_requirements_file,
)
from .risk import SupplyRisk, RiskEntry

__all__ = [
    "Origins",
    "OriginEntry",
    "SupplyRisk",
    "RiskEntry",
    "audit_installed",
    "audit_requirements_file",
]
