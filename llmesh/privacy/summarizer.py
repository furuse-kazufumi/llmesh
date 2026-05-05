"""Privacy-preserving summarizer — reduces L3/L4 data to shareable L1 abstractions.

Pipeline (applied in order):
  1. Secret masking     — replaces detected secrets with [REDACTED:type]
  2. Path anonymization — replaces absolute paths with [PATH]
  3. Signature extraction — for Python code, keeps only def/class lines
  4. Truncation         — caps output at max_chars

The resulting summary is wrapped in a ClassifiedPayload at the target_level
(default L1) and is safe to share over P2P with untrusted peers.

Security invariants (GATE-08):
  - Original L3/L4 prompt text is NEVER stored in audit logs
  - Only the summary (L1) and metadata are persisted
  - No eval, exec, shell=True, or pickle anywhere in this module
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..classifier.data_level import ClassifiedPayload, DataLevel
from .firewall import _L1_PATTERNS  # reuse compiled secret patterns


_DEFAULT_MAX_CHARS = 512
_ABSOLUTE_PATH_RE = re.compile(
    r'(?:^|[\s"\'`])(/[a-zA-Z0-9_\-\.]+){3,}|[A-Za-z]:\\[^\s"\']{10,}'
)
_DEF_CLASS_RE = re.compile(
    r'^[ \t]*((?:async\s+)?def\s+\w[\w\d_]*\s*\([^)]*\)|class\s+\w[\w\d_]*[^:]*):',
    re.MULTILINE,
)


class SummarizationError(Exception):
    """Raised when summarization cannot proceed (e.g. target level >= source level)."""


@dataclass
class SummaryResult:
    """Result of a privacy-preserving summarization pass."""

    original_level: DataLevel
    summary_level: DataLevel
    summary: str                       # abstracted, safe-to-share text
    masks_applied: int                 # number of redaction substitutions
    paths_anonymized: int
    signatures_extracted: bool         # True if code signature extraction ran
    truncated: bool                    # True if output was truncated
    lineage: tuple[str, ...] = field(default_factory=tuple)
    payload: ClassifiedPayload | None = None  # L1 ClassifiedPayload for P2P

    def to_dict(self) -> dict[str, Any]:
        return {
            "original_level": self.original_level.name,
            "summary_level": self.summary_level.name,
            "summary": self.summary,
            "masks_applied": self.masks_applied,
            "paths_anonymized": self.paths_anonymized,
            "signatures_extracted": self.signatures_extracted,
            "truncated": self.truncated,
        }


class PrivacySummarizer:
    """Reduce L3/L4 ClassifiedPayload to a shareable L1 summary.

    Args:
        target_level: Output classification level (default L1).
        max_chars: Maximum length of the summary string.
        extract_code_signatures: If True, Python code is reduced to
            def/class signatures only.
    """

    def __init__(
        self,
        target_level: DataLevel = DataLevel.L1,
        max_chars: int = _DEFAULT_MAX_CHARS,
        extract_code_signatures: bool = True,
    ) -> None:
        self._target = target_level
        self._max_chars = max_chars
        self._extract_sigs = extract_code_signatures

    def summarize(self, payload: ClassifiedPayload) -> SummaryResult:
        """Summarize a ClassifiedPayload down to target_level.

        Raises SummarizationError if the payload level is already <= target_level
        (no summarization needed — caller should not call this unnecessarily).

        Returns a SummaryResult whose .payload is a new ClassifiedPayload
        at target_level, safe for P2P sharing.
        """
        if payload.level <= self._target:
            raise SummarizationError(
                f"payload level {payload.level.name} is already <= "
                f"target {self._target.name}; summarization not needed"
            )

        text = payload.data if isinstance(payload.data, str) else str(payload.data)
        lineage: list[str] = list(payload.lineage) + [
            f"summarize:{payload.level.name}->{self._target.name}"
        ]

        # Step 1: mask secrets
        text, masks = self._mask_secrets(text)

        # Step 2: anonymize paths
        text, paths = self._anonymize_paths(text)

        # Step 3: extract code signatures (optional)
        sig_extracted = False
        if self._extract_sigs and self._looks_like_python(text):
            extracted = self._extract_signatures(text)
            if extracted:
                text = extracted
                sig_extracted = True
                lineage.append("code_signature_extraction")

        # Step 4: truncate
        truncated = False
        if len(text) > self._max_chars:
            text = text[: self._max_chars] + " [TRUNCATED]"
            truncated = True
            lineage.append(f"truncated:max={self._max_chars}")

        if masks:
            lineage.append(f"secrets_masked:{masks}")
        if paths:
            lineage.append(f"paths_anonymized:{paths}")

        summary_payload = ClassifiedPayload.create(
            data=text,
            level=self._target,
            lineage=lineage,
            policy_decision="summarized",
        )

        return SummaryResult(
            original_level=payload.level,
            summary_level=self._target,
            summary=text,
            masks_applied=masks,
            paths_anonymized=paths,
            signatures_extracted=sig_extracted,
            truncated=truncated,
            lineage=tuple(lineage),
            payload=summary_payload,
        )

    def summarize_text(self, text: str, source_level: DataLevel) -> SummaryResult:
        """Convenience wrapper: create a ClassifiedPayload from raw text, then summarize."""
        payload = ClassifiedPayload.create(
            data=text,
            level=source_level,
            policy_decision="pending",
        )
        return self.summarize(payload)

    # ------------------------------------------------------------------
    # Pipeline steps
    # ------------------------------------------------------------------

    @staticmethod
    def _mask_secrets(text: str) -> tuple[str, int]:
        """Replace detected secrets with [REDACTED:type]. Returns (text, count)."""
        count = 0
        for name, pattern in _L1_PATTERNS:
            def _replace(m: re.Match, _name: str = name) -> str:
                nonlocal count
                count += 1
                return f"[REDACTED:{_name}]"

            text = pattern.sub(_replace, text)
        return text, count

    @staticmethod
    def _anonymize_paths(text: str) -> tuple[str, int]:
        """Replace absolute file paths with [PATH]. Returns (text, count)."""
        count = 0

        def _replace(m: re.Match) -> str:
            nonlocal count
            count += 1
            # Preserve leading whitespace/quote that was part of the match group
            full = m.group(0)
            # Find where the path starts (after any leading whitespace/quote)
            stripped = full.lstrip(' \t"\'`')
            prefix = full[: len(full) - len(stripped)]
            return prefix + "[PATH]"

        text = _ABSOLUTE_PATH_RE.sub(_replace, text)
        return text, count

    @staticmethod
    def _looks_like_python(text: str) -> bool:
        """Heuristic: text likely contains Python code."""
        return bool(re.search(r'\bdef\s+\w+\s*\(|\bclass\s+\w+', text))

    @staticmethod
    def _extract_signatures(text: str) -> str:
        """Extract def/class signatures from Python code, discarding bodies."""
        lines = text.splitlines()
        result: list[str] = []
        in_body = False
        sig_indent: int | None = None

        for line in lines:
            stripped = line.lstrip()
            indent = len(line) - len(stripped)

            match = _DEF_CLASS_RE.match(line)
            if match:
                result.append(line.rstrip() + "  # [IMPL REDACTED]")
                in_body = True
                sig_indent = indent
                continue

            if in_body:
                # Body line: skip if indented deeper than signature
                if stripped == "" or indent > sig_indent:  # type: ignore[operator]
                    continue
                else:
                    in_body = False
                    sig_indent = None

            # Keep non-body lines (imports, module-level constants, blank lines)
            if not in_body:
                result.append(line)

        return "\n".join(result).strip()
