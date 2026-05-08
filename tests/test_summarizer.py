"""Tests for llmesh.privacy.summarizer — PrivacySummarizer."""
from __future__ import annotations
import pytest
from llmesh.classifier.data_level import ClassifiedPayload, DataLevel
from llmesh.privacy.summarizer import PrivacySummarizer, SummarizationError


def _payload(text: str, level: DataLevel) -> ClassifiedPayload:
    return ClassifiedPayload.create(data=text, level=level, policy_decision="pending")


class TestPrivacySummarizer:
    def setup_method(self):
        self.s = PrivacySummarizer(target_level=DataLevel.L1, max_chars=512)

    def test_l3_to_l1(self):
        r = self.s.summarize(_payload("def foo(): pass", DataLevel.L3))
        assert r.summary_level == DataLevel.L1
        assert r.original_level == DataLevel.L3

    def test_payload_level_is_l1(self):
        r = self.s.summarize(_payload("def foo(): pass", DataLevel.L3))
        assert r.payload.level == DataLevel.L1

    def test_already_l1_raises(self):
        with pytest.raises(SummarizationError):
            self.s.summarize(_payload("hello", DataLevel.L1))

    def test_already_l0_raises(self):
        with pytest.raises(SummarizationError):
            self.s.summarize(_payload("hello", DataLevel.L0))

    def test_secret_masked(self):
        text = "password = 'hunter2secret'"
        r = self.s.summarize(_payload(text, DataLevel.L3))
        assert "hunter2secret" not in r.summary
        assert "[REDACTED" in r.summary
        assert r.masks_applied >= 1

    def test_path_anonymized(self):
        text = "load('/etc/app/config/settings.yaml')"
        r = self.s.summarize(_payload(text, DataLevel.L3))
        assert "/etc/app/config" not in r.summary
        assert "[PATH]" in r.summary
        assert r.paths_anonymized >= 1

    def test_python_signatures_extracted(self):
        code = "def add(a, b):\n    return a + b\n\ndef mul(a, b):\n    return a * b\n"
        r = self.s.summarize(_payload(code, DataLevel.L3))
        assert r.signatures_extracted is True
        assert "def add" in r.summary
        assert "def mul" in r.summary
        assert "return a + b" not in r.summary

    def test_truncation_applied(self):
        s = PrivacySummarizer(target_level=DataLevel.L1, max_chars=20)
        r = s.summarize(_payload("x" * 100, DataLevel.L3))
        assert r.truncated is True
        assert "[TRUNCATED]" in r.summary

    def test_no_truncation_when_short(self):
        r = self.s.summarize(_payload("short text", DataLevel.L3))
        assert r.truncated is False

    def test_lineage_contains_summarize_step(self):
        r = self.s.summarize(_payload("def f(): pass", DataLevel.L3))
        assert any("summarize:" in step for step in r.lineage)

    def test_summarize_text_convenience(self):
        r = self.s.summarize_text("api_key = 'abc123xyz456def789'", DataLevel.L3)
        assert r.summary_level == DataLevel.L1

    def test_to_dict_has_required_keys(self):
        r = self.s.summarize(_payload("def f(): pass", DataLevel.L3))
        d = r.to_dict()
        assert set(d.keys()) >= {"original_level", "summary_level", "summary",
                                   "masks_applied", "paths_anonymized",
                                   "signatures_extracted", "truncated"}

    def test_l4_to_l1(self):
        r = self.s.summarize(_payload("secret data", DataLevel.L4))
        assert r.summary_level == DataLevel.L1
        assert r.original_level == DataLevel.L4

    def test_non_python_code_no_sig_extraction(self):
        text = "plain english description without code structure"
        r = self.s.summarize(_payload(text, DataLevel.L3))
        assert r.signatures_extracted is False

    def test_payload_lineage_preserved(self):
        p = ClassifiedPayload.create(
            data="def f(): pass", level=DataLevel.L3,
            lineage=["firewall:layer1_clean"], policy_decision="pending"
        )
        r = self.s.summarize(p)
        assert "firewall:layer1_clean" in r.lineage
