"""Tests for PresidioDetector (Layer 1.5 — E-2.1 / v2.13+).

The detector must work in three regimes:
- Presidio not installed   → ALLOW (no-op).
- Presidio raises          → BLOCK (fail-closed).
- Presidio finds an entity → SUMMARIZE or BLOCK depending on the type.

These tests use a small fake engine (``FakeAnalyzer``) so they run even
when ``presidio-analyzer`` is not installed in the test environment.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from llmesh.classifier.data_level import DataLevel
from llmesh.privacy.presidio_detector import (
    PresidioDetector,
    PresidioResult,
    _DEFAULT_BLOCK_ENTITIES,
    _DEFAULT_SUMMARIZE_ENTITIES,
)


# ---------------------------------------------------------------------------
# Fake Presidio backend (matches the duck-typed surface we depend on)
# ---------------------------------------------------------------------------

@dataclass
class _FakeRecognizerResult:
    entity_type: str
    score: float = 0.9


class _FakeAnalyzer:
    """Minimal stand-in for ``presidio_analyzer.AnalyzerEngine``."""

    def __init__(self, results=None, raise_on_analyze: bool = False):
        self._results = list(results or [])
        self._raise = raise_on_analyze

    def analyze(self, *, text, language, score_threshold=0.0):  # noqa: ARG002
        if self._raise:
            raise RuntimeError("simulated presidio failure")
        return [r for r in self._results if r.score >= score_threshold]


def _detector_with(results=None, raise_on_analyze: bool = False, **kw) -> PresidioDetector:
    """Build a PresidioDetector that does not call the real Presidio."""
    d = PresidioDetector(**kw)
    d._engine = _FakeAnalyzer(results=results, raise_on_analyze=raise_on_analyze)
    return d


# ---------------------------------------------------------------------------
# Defaults / availability
# ---------------------------------------------------------------------------

class TestDefaults:
    def test_default_block_set_includes_high_sensitivity_pii(self):
        for entity in ("CREDIT_CARD", "US_SSN", "IBAN_CODE", "MEDICAL_LICENSE"):
            assert entity in _DEFAULT_BLOCK_ENTITIES

    def test_default_summarize_set_includes_basic_identifiers(self):
        for entity in ("PERSON", "EMAIL_ADDRESS", "PHONE_NUMBER", "LOCATION"):
            assert entity in _DEFAULT_SUMMARIZE_ENTITIES

    def test_block_and_summarize_sets_disjoint(self):
        assert not (_DEFAULT_BLOCK_ENTITIES & _DEFAULT_SUMMARIZE_ENTITIES)


# ---------------------------------------------------------------------------
# Unavailable backend → ALLOW
# ---------------------------------------------------------------------------

class TestUnavailable:
    def test_engine_load_failure_yields_unavailable(self, monkeypatch):
        """If AnalyzerEngine cannot be imported, available is False."""
        monkeypatch.setattr(
            PresidioDetector, "_try_load_engine",
            staticmethod(lambda: None),
        )
        d = PresidioDetector()
        assert d.available is False

    def test_unavailable_returns_allow(self, monkeypatch):
        monkeypatch.setattr(
            PresidioDetector, "_try_load_engine",
            staticmethod(lambda: None),
        )
        d = PresidioDetector()
        result = d.detect("Hi, my SSN is 123-45-6789")
        assert result.allowed is True
        assert result.reason == "presidio_unavailable"
        assert result.level == DataLevel.L0


# ---------------------------------------------------------------------------
# Engine error → BLOCK (fail closed)
# ---------------------------------------------------------------------------

class TestFailClosed:
    def test_analyzer_exception_returns_block(self):
        d = _detector_with(raise_on_analyze=True)
        result = d.detect("anything")
        assert result.blocked is True
        assert result.reason == "presidio_error_fail_closed"
        assert result.level == DataLevel.L4


# ---------------------------------------------------------------------------
# BLOCK entities → L4 BLOCK
# ---------------------------------------------------------------------------

class TestBlock:
    def test_credit_card_blocks(self):
        d = _detector_with(results=[_FakeRecognizerResult("CREDIT_CARD", 0.95)])
        r = d.detect("card 4111-1111-1111-1111")
        assert r.blocked is True
        assert r.reason == "presidio_block:CREDIT_CARD"
        assert r.entities == ("CREDIT_CARD",)
        assert r.level == DataLevel.L4

    def test_us_ssn_blocks(self):
        d = _detector_with(results=[_FakeRecognizerResult("US_SSN", 0.99)])
        r = d.detect("ssn: 123-45-6789")
        assert r.blocked is True
        assert "US_SSN" in r.reason

    def test_block_takes_priority_over_summarize(self):
        d = _detector_with(results=[
            _FakeRecognizerResult("PERSON", 0.9),
            _FakeRecognizerResult("CREDIT_CARD", 0.95),
        ])
        r = d.detect("Alice's card 4111-1111-1111-1111")
        assert r.blocked is True
        assert "CREDIT_CARD" in r.reason


# ---------------------------------------------------------------------------
# SUMMARIZE entities → L3 SUMMARIZE
# ---------------------------------------------------------------------------

class TestSummarize:
    def test_person_summarizes(self):
        d = _detector_with(results=[_FakeRecognizerResult("PERSON", 0.85)])
        r = d.detect("My name is Alice")
        assert r.requires_summarization is True
        assert r.reason == "presidio_summarize:PERSON"
        assert r.level == DataLevel.L3

    def test_email_summarizes(self):
        d = _detector_with(results=[_FakeRecognizerResult("EMAIL_ADDRESS", 0.95)])
        r = d.detect("contact me at alice@example.com")
        assert r.requires_summarization is True
        assert "EMAIL_ADDRESS" in r.reason

    def test_low_score_filtered_out(self):
        d = _detector_with(
            results=[_FakeRecognizerResult("PERSON", 0.3)],
            score_threshold=0.5,
        )
        r = d.detect("Alice")
        assert r.allowed is True
        assert r.reason == "presidio_clean"


# ---------------------------------------------------------------------------
# Custom entity sets
# ---------------------------------------------------------------------------

class TestCustomSets:
    def test_custom_block_entities(self):
        d = _detector_with(
            results=[_FakeRecognizerResult("CUSTOM_TYPE", 0.99)],
            block_entities={"CUSTOM_TYPE"},
        )
        r = d.detect("trigger")
        assert r.blocked is True

    def test_custom_summarize_entities(self):
        d = _detector_with(
            results=[_FakeRecognizerResult("FOO", 0.99)],
            summarize_entities={"FOO"},
            block_entities=set(),  # disable defaults
        )
        r = d.detect("trigger")
        assert r.requires_summarization is True

    def test_unknown_entity_passes_through(self):
        d = _detector_with(results=[_FakeRecognizerResult("WHATEVER", 0.99)])
        r = d.detect("text")
        assert r.allowed is True
        assert r.reason == "presidio_clean"
