"""Prompt Firewall — fail-closed Layer 0/1/2 implementation.

Layer 0: Prompt injection detection (adversarial instruction overrides, jailbreaks,
         Unicode bidirectional tricks, ChatML/special-token injection).
Layer 1: Static/Regex secret scanner (API keys, JWTs, private keys, etc.)
Layer 2: Structural classifier (absolute paths, oversized payloads, etc.)

v0.2.0: L3 triggers now return action="SUMMARIZE" instead of "BLOCK" so the
server can route sensitive-but-summarizable prompts through PrivacySummarizer
before reaching the LLM backend.  L4 triggers remain action="BLOCK".

Action semantics:
  "ALLOW"     — prompt is safe, pass to backend unchanged.
  "SUMMARIZE" — prompt is L3 (sensitive), must be summarized before backend.
  "BLOCK"     — prompt is L4 (regulated/secret/adversarial), reject entirely.

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
    from llmesh.privacy.presidio_detector import PresidioDetector


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FirewallDecision:
    action: str           # "ALLOW" | "SUMMARIZE" | "BLOCK"
    reason: str
    level: DataLevel
    triggered_layer: int  # -1 = error, 0 = Layer0 injection, 1 = Layer1 secrets, 2 = Layer2 structure

    @property
    def blocked(self) -> bool:
        """True only for hard L4 blocks. SUMMARIZE is NOT blocked."""
        return self.action == "BLOCK"

    @property
    def requires_summarization(self) -> bool:
        """True when L3 content must be summarized before reaching the backend."""
        return self.action == "SUMMARIZE"

    @property
    def allowed(self) -> bool:
        return self.action == "ALLOW"


_FAIL_CLOSED = FirewallDecision(
    action="BLOCK",
    reason="firewall_error_fail_closed",
    level=DataLevel.L4,
    triggered_layer=-1,  # -1 = internal error, distinct from any real layer
)

# ---------------------------------------------------------------------------
# Layer 0 — prompt injection detection
# Catches adversarial instruction overrides before secret scanning.
# All matches are L4 BLOCK — these are attacks, not data classification issues.
# ---------------------------------------------------------------------------

_L0_INJECTION_PATTERNS: list[tuple[str, re.Pattern]] = [
    # "ignore/forget/override ... (previous/your/system) ... instructions/rules"
    ("pi_ignore_prior", re.compile(
        r"(?i)\b(ignore|disregard|forget|override)\b.{0,30}"
        r"\b(previous|prior|above|earlier|all|your|system|original)\b.{0,30}"
        r"\b(instructions?|directives?|rules?|guidelines?|prompt)\b",
        re.MULTILINE,
    )),
    # DAN mode, jailbreak, "pretend you have no restrictions"
    ("pi_jailbreak", re.compile(
        r"(?i)\b(DAN\s*mode|jailbreak(?:ing|ed)?|unrestricted\s+mode"
        r"|pretend\s+you\s+have\s+no\s+(?:filter|restrict|limit|rule|constraint))\b",
        re.MULTILINE,
    )),
    # "act as an uncensored/unrestricted/unfiltered AI"
    ("pi_act_as", re.compile(
        r"(?i)\bact\s+as\s+(?:if\s+)?(?:you\s+(?:are|were)\s+)?(?:an?\s+)?"
        r"(?:unrestricted|uncensored|unfiltered|jailbroken|evil|malicious|DAN)\b",
        re.MULTILINE,
    )),
    # ChatML / Llama / instruction-tuning special tokens injected into user text
    ("pi_special_tokens", re.compile(
        r"<\|im_start\||<\|im_end\||<<SYS>>|<</SYS>>|\[INST\]|\[/INST\]",
    )),
    # Unicode bidirectional / LTR-override / BOM tricks used to hide injected text
    ("pi_unicode_control", re.compile(
        r"[‮‭﻿]",  # RTL override, LTR override, byte-order mark  # nosec B613 - bidi chars intentionally present to detect them in input.
    )),
]

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

_MAX_PAYLOAD_CHARS = 16_384


# ---------------------------------------------------------------------------
# Firewall
# ---------------------------------------------------------------------------

class PromptFirewall:
    """Three-layer prompt firewall with fail-closed exception handling.

    Layer 0 (prompt injection → L4 BLOCK), Layer 1 (secrets → L4 BLOCK),
    and Layer 2 (sensitive structure → L3 SUMMARIZE or L4 BLOCK for oversized
    payloads). Layers run in order; first match wins.
    """

    def __init__(
        self,
        extra_patterns: Sequence[tuple[str, re.Pattern]] | None = None,
        max_payload_chars: int = _MAX_PAYLOAD_CHARS,
        audit_trace: "AuditTrace | None" = None,
        presidio: "PresidioDetector | None" = None,
    ) -> None:
        self._patterns = list(_L1_PATTERNS) + list(extra_patterns or [])
        self._max_chars = max_payload_chars
        self._audit = audit_trace
        self._presidio = presidio

    def classify(
        self,
        prompt: str,
        node_id: str = "",
        task_id: str = "",
    ) -> FirewallDecision:
        """Classify prompt. Returns BLOCK on any exception (fail-closed).

        Decision is logged to audit trace when one is configured.
        """
        try:
            decision = self._run_pipeline(prompt)
        except Exception:
            decision = _FAIL_CLOSED

        if self._audit is not None:
            content_sha = hashlib.sha256(prompt.encode()).hexdigest()
            if decision.blocked:
                event = "firewall_block"
            elif decision.requires_summarization:
                event = "firewall_summarize"
            else:
                event = "firewall_allow"
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
        layer0 = self._layer0_injection(prompt)
        if layer0.blocked:
            return layer0
        layer1 = self._layer1(prompt)
        if layer1.blocked:
            return layer1
        layer15 = self._layer15_presidio(prompt)
        if layer15.blocked or layer15.requires_summarization:
            return layer15
        return self._layer2(prompt)

    def _layer15_presidio(self, prompt: str) -> FirewallDecision:
        """Optional PII detection via Microsoft Presidio.

        No-op when no detector is wired in (default). When present, the
        detector handles its own fail-closed semantics — see
        :mod:`llmesh.privacy.presidio_detector`.
        """
        if self._presidio is None:
            return FirewallDecision(
                action="ALLOW", reason="layer15_disabled",
                level=DataLevel.L0, triggered_layer=15,
            )
        result = self._presidio.detect(prompt)
        return FirewallDecision(
            action=result.action,
            reason=f"layer15:{result.reason}",
            level=result.level,
            triggered_layer=15,
        )

    def _layer0_injection(self, prompt: str) -> FirewallDecision:
        for name, pattern in _L0_INJECTION_PATTERNS:
            if pattern.search(prompt):
                return FirewallDecision(
                    action="BLOCK",
                    reason=f"layer0_injection_detected:{name}",
                    level=DataLevel.L4,
                    triggered_layer=0,
                )
        return FirewallDecision(
            action="ALLOW", reason="layer0_clean",
            level=DataLevel.L0, triggered_layer=0,
        )

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
            # Oversized payload → L4 hard block (cannot summarize unknown content)
            return FirewallDecision(
                action="BLOCK", reason="layer2_payload_too_large",
                level=DataLevel.L4, triggered_layer=2,
            )
        if _ABSOLUTE_PATH_RE.search(prompt):
            # Absolute path → L3 SUMMARIZE (sensitive but recoverable)
            return FirewallDecision(
                action="SUMMARIZE", reason="layer2_absolute_path_detected",
                level=DataLevel.L3, triggered_layer=2,
            )
        if _INTERNAL_IMPORT_RE.search(prompt):
            # Internal import → L3 SUMMARIZE
            return FirewallDecision(
                action="SUMMARIZE", reason="layer2_internal_import_detected",
                level=DataLevel.L3, triggered_layer=2,
            )
        return FirewallDecision(
            action="ALLOW", reason="layer2_clean",
            level=DataLevel.L1, triggered_layer=2,
        )

    def wrap(self, prompt: str) -> ClassifiedPayload:
        """Classify and return a ClassifiedPayload."""
        decision = self.classify(prompt)
        return ClassifiedPayload.create(
            data=prompt,
            level=decision.level,
            lineage=[f"firewall:{decision.reason}"],
            policy_decision=decision.action,
        )
