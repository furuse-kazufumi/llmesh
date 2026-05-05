"""Prompt Firewall — fail-closed Layer 1/2 implementation.

Layer 1: Static/Regex secret scanner (API keys, JWTs, private keys, etc.)
Layer 2: Structural classifier (absolute paths, oversized payloads, etc.)

CRITICAL: Any unhandled exception returns L4/BLOCK. Never fail open.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Sequence

from llmesh.classifier.data_level import DataLevel, ClassifiedPayload

if TYPE_CHECKING:
    from llmesh.audit import AuditTrace


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FirewallDecision:
    action: str           # "ALLOW" | "BLOCK"
    reason: str
    level: DataLevel
    triggered_layer: int  # 0 = none, 1 = Layer1, 2 = Layer2

    @property
    def blocked(self) -> bool:
        return self.action == "BLOCK"


_FAIL_CLOSED = FirewallDecision(
    action="BLOCK",
    reason="firewall_error_fail_closed",
    level=DataLevel.L4,
    triggered_layer=0,
)

# ---------------------------------------------------------------------------
# Layer 1 — secret patterns (gitleaks-inspired subset)
# ---------------------------------------------------------------------------

_L1_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("api_key_generic",  re.compile(r'(?i)(api[_-]?key|apikey)\s*[:=]\s*["\']?[A-Za-z0-9+/=_\-]{16,}', re.MULTILINE)),
    ("aws_access_key",   re.compile(r'(?<![A-Z0-9])AKIA[0-9A-Z]{16}(?![A-Z0-9])')),
    ("aws_secret_key",   re.compile(r'(?i)aws[_\-]?secret[_\-]?access[_\-]?key\s*[:=]\s*["\']?[A-Za-z0-9+/=]{40}')),
    ("private_key_pem",  re.compile(r'-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----')),
    ("jwt_token",        re.compile(r'eyJ[A-Za-z0-9\-_]+\.eyJ[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+')),
    ("bearer_token",     re.compile(r'(?i)bearer\s+[A-Za-z0-9\-_\.]{20,}')),
    ("password_assign",  re.compile(r'(?i)(password|passwd|pwd)\s*[:=]\s*["\']?.{6,}["\']?')),
    ("gh_token",         re.compile(r'gh[pousr]_[A-Za-z0-9]{36}')),
    ("slack_token",      re.compile(r'xox[baprs]-[0-9A-Za-z\-]+')),
    ("anthropic_key",    re.compile(r'sk-ant-[A-Za-z0-9\-_]{40,}')),
    ("openai_key",       re.compile(r'sk-[A-Za-z0-9]{48}')),
    ("generic_secret",   re.compile(r'(?i)(secret|token|credential)\s*[:=]\s*["\']?[A-Za-z0-9+/=_\-]{16,}["\']?')),
]

# ---------------------------------------------------------------------------
# Layer 2 — structural patterns
# ---------------------------------------------------------------------------

_ABSOLUTE_PATH_RE = re.compile(r'(?:^|[\s"\'`])(/[a-zA-Z0-9_\-\.]+){3,}|[A-Za-z]:\\[^\s"\']{10,}')
_INTERNAL_IMPORT_RE = re.compile(r'(?:import|from)\s+(?:company|corp|internal|private|proprietary)\.[^\s]+', re.IGNORECASE)

_MAX_PAYLOAD_CHARS = 16_384  # Layer 2 blocks payloads over this size


# ---------------------------------------------------------------------------
# Firewall
# ---------------------------------------------------------------------------

class PromptFirewall:
    """Two-layer prompt firewall with fail-closed exception handling."""

    def __init__(
        self,
        extra_patterns: Sequence[tuple[str, re.Pattern]] | None = None,
        max_payload_chars: int = _MAX_PAYLOAD_CHARS,
        audit_trace: "AuditTrace | None" = None,
    ) -> None:
        self._patterns = list(_L1_PATTERNS) + list(extra_patterns or [])
        self._max_chars = max_payload_chars
        self._audit = audit_trace

    def classify(
        self,
        prompt: str,
        node_id: str = "",
        task_id: str = "",
    ) -> FirewallDecision:
        """Classify prompt. Returns BLOCK on any exception (fail-closed).

        If an AuditTrace was supplied at construction time, the decision is
        logged as ``firewall_allow`` or ``firewall_block``.  node_id and
        task_id are forwarded to the audit entry so the log can be correlated
        with a specific request.
        """
        try:
            decision = self._run_pipeline(prompt)
        except Exception:
            decision = _FAIL_CLOSED

        if self._audit is not None:
            content_sha = hashlib.sha256(prompt.encode()).hexdigest()
            event = "firewall_block" if decision.blocked else "firewall_allow"
            self._audit.log(
                event_type=event,
                node_id=node_id,
                task_id=task_id,
                policy_decision=decision.action,
                output_sha256=content_sha,
                data_level=int(decision.level),
            )

        return decision

    def _run_pipeline(self, prompt: str) -> FirewallDecision:
        layer1 = self._layer1(prompt)
        if layer1.blocked:
            return layer1
        return self._layer2(prompt)

    def _layer1(self, prompt: str) -> FirewallDecision:
        for name, pattern in self._patterns:
            if pattern.search(prompt):
                return FirewallDecision(
                    action="BLOCK",
                    reason=f"layer1_secret_detected:{name}",
                    level=DataLevel.L4,
                    triggered_layer=1,
                )
        return FirewallDecision(
            action="ALLOW", reason="layer1_clean",
            level=DataLevel.L0, triggered_layer=1,
        )

    def _layer2(self, prompt: str) -> FirewallDecision:
        if len(prompt) > self._max_chars:
            return FirewallDecision(
                action="BLOCK", reason="layer2_payload_too_large",
                level=DataLevel.L4, triggered_layer=2,
            )
        if _ABSOLUTE_PATH_RE.search(prompt):
            return FirewallDecision(
                action="BLOCK", reason="layer2_absolute_path_detected",
                level=DataLevel.L3, triggered_layer=2,
            )
        if _INTERNAL_IMPORT_RE.search(prompt):
            return FirewallDecision(
                action="BLOCK", reason="layer2_internal_import_detected",
                level=DataLevel.L3, triggered_layer=2,
            )
        return FirewallDecision(
            action="ALLOW", reason="layer2_clean",
            level=DataLevel.L1, triggered_layer=2,
        )

    def wrap(self, prompt: str) -> ClassifiedPayload:
        """Classify and return a ClassifiedPayload (BLOCK → L4, ALLOW → decision level)."""
        decision = self.classify(prompt)
        return ClassifiedPayload.create(
            data=prompt,
            level=decision.level,
            lineage=[f"firewall:{decision.reason}"],
            policy_decision=decision.action,
        )
