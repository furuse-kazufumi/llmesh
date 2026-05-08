"""Tests for VLMFeatureExtractor — v3-N15 image → numerical feature."""
from __future__ import annotations

import pytest

from llmesh.industrial.vlm_feature_extractor import (
    MockVisionCaptioner,
    VLMFeature,
    VLMFeatureExtractor,
    _default_parse,
)


# ---------------------------------------------------------------------------
# Default parser
# ---------------------------------------------------------------------------

class TestDefaultParser:
    def test_invalid_dimension(self):
        with pytest.raises(ValueError):
            _default_parse("anything", 0)

    def test_returns_fixed_length(self):
        v = _default_parse("the dimensions are 12.5 and 7", 8)
        assert len(v) == 8

    def test_numbers_in_caption_appear(self):
        v = _default_parse("123 456 789", 8)
        assert 123.0 in v
        assert 456.0 in v

    def test_defect_keywords_count_in_tail(self):
        v_clean = _default_parse("smooth surface", 8)
        v_dirty = _default_parse("crack defect scratch", 8)
        # The keyword-hit slot is later in the vector; total vector
        # magnitude should differ.
        assert sum(v_dirty) > sum(v_clean)


# ---------------------------------------------------------------------------
# MockVisionCaptioner
# ---------------------------------------------------------------------------

class TestMockCaptioner:
    def test_deterministic(self):
        cap = MockVisionCaptioner()
        a = cap.caption(b"abc")
        b = cap.caption(b"abc")
        assert a == b

    def test_different_input_different_caption(self):
        cap = MockVisionCaptioner()
        assert cap.caption(b"abc") != cap.caption(b"xyz")


# ---------------------------------------------------------------------------
# Extractor — happy path
# ---------------------------------------------------------------------------

class _StubCaptioner:
    def __init__(self, text):
        self._text = text
    def caption(self, image_bytes):
        return self._text


class TestExtractAllow:
    def test_invalid_dimension_construction(self):
        with pytest.raises(ValueError):
            VLMFeatureExtractor(dimension=0)

    def test_extract_returns_fixed_dim(self):
        ex = VLMFeatureExtractor(_StubCaptioner("123 456"), dimension=8)
        out = ex.extract(b"image")
        assert isinstance(out, VLMFeature)
        assert len(out.vector) == 8
        assert out.allowed is True
        assert out.action == "ALLOW"

    def test_caption_propagates_into_vector(self):
        ex = VLMFeatureExtractor(_StubCaptioner("count=42"), dimension=8)
        out = ex.extract(b"image")
        assert 42.0 in out.vector

    def test_extract_many(self):
        ex = VLMFeatureExtractor(_StubCaptioner("0 0 0"), dimension=4)
        outs = ex.extract_many([b"a", b"b", b"c"])
        assert len(outs) == 3
        assert all(o.allowed for o in outs)


# ---------------------------------------------------------------------------
# Extractor — firewall integration
# ---------------------------------------------------------------------------

class _Decision:
    def __init__(self, action, reason=""):
        self.action = action
        self.reason = reason


class TestFirewall:
    def test_no_firewall_means_allow(self):
        ex = VLMFeatureExtractor(_StubCaptioner("0"), dimension=4)
        out = ex.extract(b"image")
        assert out.action == "ALLOW"
        assert out.reason == "no_image_firewall"

    def test_block_decision_returns_zero_vector(self):
        def fw(_): return _Decision("BLOCK", "l4_face_detected")
        ex = VLMFeatureExtractor(_StubCaptioner("0"), image_firewall=fw, dimension=4)
        out = ex.extract(b"image")
        assert out.blocked is True
        assert out.allowed is False
        assert out.action == "BLOCK"
        assert out.reason == "l4_face_detected"
        assert out.vector == (0.0,) * 4

    def test_summarize_passes_action_through(self):
        def fw(_): return _Decision("SUMMARIZE", "l3_text_present")
        ex = VLMFeatureExtractor(_StubCaptioner("0 0 0"), image_firewall=fw, dimension=4)
        out = ex.extract(b"image")
        assert out.action == "SUMMARIZE"
        assert out.allowed is True   # firewall did not block; caller routes via summarizer

    def test_firewall_exception_fails_closed(self):
        def boom(_): raise RuntimeError("oops")
        ex = VLMFeatureExtractor(_StubCaptioner("0"), image_firewall=boom, dimension=4)
        out = ex.extract(b"image")
        assert out.blocked is True
        assert "fail_closed" in out.reason

    def test_tuple_decision_accepted(self):
        def fw(_): return ("BLOCK", "by_tuple")
        ex = VLMFeatureExtractor(_StubCaptioner("0"), image_firewall=fw, dimension=4)
        out = ex.extract(b"image")
        assert out.blocked is True
        assert out.reason == "by_tuple"

    def test_unknown_action_is_blocked(self):
        def fw(_): return _Decision("WHATEVER", "?")
        ex = VLMFeatureExtractor(_StubCaptioner("0"), image_firewall=fw, dimension=4)
        out = ex.extract(b"image")
        assert out.blocked is True
        assert out.reason == "image_firewall_unknown_decision"


# ---------------------------------------------------------------------------
# Extractor — captioner error
# ---------------------------------------------------------------------------

class _BoomCaptioner:
    def caption(self, _):
        raise RuntimeError("model unavailable")


class _NonStringCaptioner:
    def caption(self, _):
        return 42  # not a str


class TestCaptionerErrors:
    def test_captioner_exception_blocks(self):
        ex = VLMFeatureExtractor(_BoomCaptioner(), dimension=4)
        out = ex.extract(b"image")
        assert out.blocked is True
        assert out.reason == "captioner_error_fail_closed"

    def test_non_string_caption_blocks(self):
        ex = VLMFeatureExtractor(_NonStringCaptioner(), dimension=4)
        out = ex.extract(b"image")
        assert out.blocked is True
        assert out.reason == "captioner_returned_non_string"
