"""Microsoft Presidio integration — Layer 1.5 PII detection (E-2.1 / v2.13+).

Optional dependency: ``pip install llmesh[presidio]`` (pulls
``presidio-analyzer`` and ``spacy`` with the ``en_core_web_sm`` model).

Why a separate layer?
---------------------
The legacy ``PromptFirewall`` Layer 1 catches **credentials and secrets**
(API keys, JWTs, private keys). Presidio catches **personally-identifiable
information** (PII) such as names, phone numbers, SSNs, credit card
numbers, IBANs, and IP addresses. Mixing them in Layer 1 would conflate
two different policy domains; instead, we expose a lightweight detector
that ``PromptFirewall`` calls between Layer 1 and Layer 2.

Fail-closed contract
--------------------
- If Presidio is **not installed** → ``ALLOW`` with reason
  ``presidio_unavailable``. The legacy firewall regexes still apply.
- If Presidio raises an exception → ``BLOCK`` (L4) with reason
  ``presidio_error_fail_closed``.
- If a configured ``block`` entity is detected above the score threshold
  → ``BLOCK`` (L4).
- If a configured ``summarize`` entity is detected above the score
  threshold → ``SUMMARIZE`` (L3).
- Otherwise → ``ALLOW`` with reason ``presidio_clean``.

Default classification
----------------------
``BLOCK_ENTITIES``:    high-sensitivity, regulated PII that cannot be
  summarized safely (credit card, SSN, IBAN, medical license, US driver
  license, US passport, US ITIN, crypto wallet).

``SUMMARIZE_ENTITIES``: identifiers that ``PrivacySummarizer`` can redact
  by replacing with placeholders (person names, emails, phone numbers,
  locations, IP addresses, dates).

Both sets are configurable per instance.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from llmesh.classifier.data_level import DataLevel

if TYPE_CHECKING:
    from presidio_analyzer import AnalyzerEngine, RecognizerResult


# Default entity taxonomy — see Presidio's supported entities:
# https://microsoft.github.io/presidio/supported_entities/
_DEFAULT_BLOCK_ENTITIES = frozenset({
    "CREDIT_CARD",
    "US_SSN",
    "US_ITIN",
    "US_PASSPORT",
    "US_DRIVER_LICENSE",
    "IBAN_CODE",
    "MEDICAL_LICENSE",
    "CRYPTO",
})

_DEFAULT_SUMMARIZE_ENTITIES = frozenset({
    "PERSON",
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "LOCATION",
    "IP_ADDRESS",
    "DATE_TIME",
    "URL",
    "NRP",  # nationality / religious / political
})


@dataclass(frozen=True)
class PresidioResult:
    """Result of running Presidio on a single prompt."""

    action: str           # "ALLOW" | "SUMMARIZE" | "BLOCK"
    reason: str           # machine-readable reason tag
    level: DataLevel
    entities: tuple[str, ...] = field(default_factory=tuple)

    @property
    def blocked(self) -> bool:
        return self.action == "BLOCK"

    @property
    def requires_summarization(self) -> bool:
        return self.action == "SUMMARIZE"

    @property
    def allowed(self) -> bool:
        return self.action == "ALLOW"


class PresidioDetector:
    """Optional Layer 1.5 PII detector backed by ``presidio-analyzer``.

    Parameters
    ----------
    block_entities:
        Entity types that force ``BLOCK`` (L4). Defaults to high-sensitivity
        regulated PII.
    summarize_entities:
        Entity types that force ``SUMMARIZE`` (L3). Defaults to identifiers
        that ``PrivacySummarizer`` can redact safely.
    score_threshold:
        Minimum confidence (0.0–1.0) for a Presidio match to count.
        Defaults to ``0.5`` to match Presidio's own default.
    language:
        Presidio language code; defaults to ``"en"``.
    """

    def __init__(
        self,
        *,
        block_entities: set[str] | frozenset[str] | None = None,
        summarize_entities: set[str] | frozenset[str] | None = None,
        score_threshold: float = 0.5,
        language: str = "en",
    ) -> None:
        self._block_entities = frozenset(block_entities or _DEFAULT_BLOCK_ENTITIES)
        self._summarize_entities = frozenset(
            summarize_entities or _DEFAULT_SUMMARIZE_ENTITIES
        )
        self._threshold = float(score_threshold)
        self._language = language
        self._engine: "AnalyzerEngine | None" = self._try_load_engine()

    @staticmethod
    def _try_load_engine() -> "AnalyzerEngine | None":
        """Attempt to instantiate Presidio. Return ``None`` if unavailable.

        Import errors and missing-model errors both result in ``None`` —
        the firewall then treats the detector as a no-op.
        """
        try:
            from presidio_analyzer import AnalyzerEngine
            return AnalyzerEngine()
        except Exception:
            return None

    @property
    def available(self) -> bool:
        """True iff ``presidio-analyzer`` and its NLP backend are loaded."""
        return self._engine is not None

    def detect(self, text: str) -> PresidioResult:
        """Run Presidio on ``text`` and return a :class:`PresidioResult`.

        Always fails closed on exception. When Presidio is unavailable,
        returns ``ALLOW`` so the rest of the firewall pipeline runs
        unchanged.
        """
        if self._engine is None:
            return PresidioResult(
                action="ALLOW",
                reason="presidio_unavailable",
                level=DataLevel.L0,
            )

        try:
            results = self._engine.analyze(
                text=text,
                language=self._language,
                score_threshold=self._threshold,
            )
        except Exception:
            return PresidioResult(
                action="BLOCK",
                reason="presidio_error_fail_closed",
                level=DataLevel.L4,
            )

        block_hits = self._filter(results, self._block_entities)
        if block_hits:
            return PresidioResult(
                action="BLOCK",
                reason=f"presidio_block:{block_hits[0]}",
                level=DataLevel.L4,
                entities=tuple(block_hits),
            )

        summarize_hits = self._filter(results, self._summarize_entities)
        if summarize_hits:
            return PresidioResult(
                action="SUMMARIZE",
                reason=f"presidio_summarize:{summarize_hits[0]}",
                level=DataLevel.L3,
                entities=tuple(summarize_hits),
            )

        return PresidioResult(
            action="ALLOW",
            reason="presidio_clean",
            level=DataLevel.L0,
        )

    def _filter(
        self,
        results: list["RecognizerResult"],
        wanted: frozenset[str],
    ) -> list[str]:
        """Return distinct entity types from ``results`` that are in ``wanted``."""
        hits: list[str] = []
        for r in results:
            etype = getattr(r, "entity_type", None)
            score = getattr(r, "score", 0.0)
            if etype is None:
                continue
            if score < self._threshold:
                continue
            if etype in wanted and etype not in hits:
                hits.append(etype)
        return hits
