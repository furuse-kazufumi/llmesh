"""DataLevel classification and ClassifiedPayload wrapper.

Every piece of data moving through LLMesh must be wrapped in a
ClassifiedPayload so routing, firewall, and audit layers can
inspect its sensitivity level and lineage without unpacking it.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any


class DataLevel(IntEnum):
    """Sensitivity tier for LLMesh data routing."""

    L0 = 0  # Public — OSS README, public issue, public API spec
    L1 = 1  # Low-risk — abstract error, general design advice
    L2 = 2  # Internal — internal code snippets, unpublished designs
    L3 = 3  # Confidential — customer info, proprietary algorithms
    L4 = 4  # Regulated/Secret — PII, contracts, export-controlled

    @property
    def allows_public_p2p(self) -> bool:
        return self <= DataLevel.L1

    @property
    def allows_trusted_nodes(self) -> bool:
        return self <= DataLevel.L2

    def label(self) -> str:
        labels = {0: "Public", 1: "Low-risk", 2: "Internal",
                  3: "Confidential", 4: "Regulated/Secret"}
        return labels[self.value]


def _sha256(data: str | dict | bytes) -> str:
    if isinstance(data, dict):
        raw = json.dumps(data, sort_keys=True, ensure_ascii=False).encode()
    elif isinstance(data, str):
        raw = data.encode()
    else:
        raw = data
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class ClassifiedPayload:
    """Immutable wrapper carrying data with its security classification.

    All data entering or leaving LLMesh components must be wrapped here.
    Merge two payloads with ``combine_payloads()``; the resulting level
    is always the maximum — classification can only increase.
    """

    data: str | dict
    level: DataLevel
    lineage: tuple[str, ...]         # ordered record of transformations
    policy_decision: str             # e.g. "allowed", "blocked", "masked"
    sha256: str = field(compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.level, DataLevel):
            raise TypeError(f"level must be DataLevel, got {type(self.level)}")
        if not isinstance(self.lineage, tuple):
            object.__setattr__(self, "lineage", tuple(self.lineage))

    @classmethod
    def create(
        cls,
        data: str | dict,
        level: DataLevel,
        lineage: list[str] | tuple[str, ...] = (),
        policy_decision: str = "pending",
    ) -> "ClassifiedPayload":
        return cls(
            data=data,
            level=level,
            lineage=tuple(lineage),
            policy_decision=policy_decision,
            sha256=_sha256(data),
        )

    def with_decision(self, decision: str) -> "ClassifiedPayload":
        return ClassifiedPayload(
            data=self.data,
            level=self.level,
            lineage=self.lineage,
            policy_decision=decision,
            sha256=self.sha256,
        )

    def reclassify(self, new_level: DataLevel, reason: str) -> "ClassifiedPayload":
        """Return a copy with a new (usually higher) level and updated lineage."""
        return ClassifiedPayload(
            data=self.data,
            level=max(self.level, new_level),
            lineage=self.lineage + (f"reclassify:{reason}",),
            policy_decision=self.policy_decision,
            sha256=self.sha256,
        )


def combine_payloads(*payloads: ClassifiedPayload) -> ClassifiedPayload:
    """Merge multiple payloads. Level is the maximum — never decreases."""
    if not payloads:
        raise ValueError("combine_payloads requires at least one payload")

    merged_data: list[Any] = [p.data for p in payloads]
    merged_level = max(p.level for p in payloads)
    merged_lineage: tuple[str, ...] = ()
    for p in payloads:
        merged_lineage += p.lineage
    merged_lineage += ("combined",)

    combined: dict = {"parts": merged_data}
    return ClassifiedPayload(
        data=combined,
        level=merged_level,
        lineage=merged_lineage,
        policy_decision="reclassified_after_merge",
        sha256=_sha256(combined),
    )
