"""Tests for ImageSummarizer — L3 image → privacy-safe text (v1.2.0)."""
from __future__ import annotations

import base64
import io
import json
from unittest.mock import MagicMock, patch
import urllib.error

import pytest

from llmesh.privacy.image_summarizer import (
    ImageSummarizer,
    ImageSummarizationError,
    _parse_captioner,
    _call_ollama,
    _strip_exif_and_encode,
)

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100


def _make_png_bytes() -> bytes:
    try:
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (8, 8), color=(100, 150, 200)).save(buf, format="PNG")
        return buf.getvalue()
    except ImportError:
        return _PNG_MAGIC


# ---------------------------------------------------------------------------
# Tests: _parse_captioner
# ---------------------------------------------------------------------------

class TestParseCaptioner:
    def test_valid_ollama_llava(self):
        backend, model = _parse_captioner("ollama/llava")
        assert backend == "ollama"
        assert model == "llava"

    def test_valid_ollama_with_tag(self):
        backend, model = _parse_captioner("ollama/llava:7b")
        assert backend == "ollama"
        assert model == "llava:7b"

    def test_invalid_spec_raises(self):
        with pytest.raises(ImageSummarizationError, match="invalid_captioner_spec"):
            _parse_captioner("no-slash-here")


# ---------------------------------------------------------------------------
# Tests: summarize() — always-return contract (fail-closed)
# ---------------------------------------------------------------------------

class TestImageSummarizerFailClosed:
    def test_empty_data_blocked(self):
        s = ImageSummarizer()
        result = s.summarize(b"", original_level=3)
        assert result.blocked
        assert result.block_reason

    def test_pillow_unavailable_blocked(self):
        s = ImageSummarizer()
        with patch("llmesh.privacy.image_summarizer._try_import_pillow", return_value=None):
            result = s.summarize(_make_png_bytes(), original_level=3)
        assert result.blocked
        assert "Pillow" in result.block_reason

    def test_captioner_unreachable_blocked(self):
        s = ImageSummarizer(captioner="ollama/llava", base_url="http://127.0.0.1:9")
        result = s.summarize(_make_png_bytes(), original_level=3)
        assert result.blocked
        assert result.block_reason

    def test_unsupported_backend_blocked(self):
        s = ImageSummarizer(captioner="openai/gpt4v")
        with patch("llmesh.privacy.image_summarizer._strip_exif_and_encode", return_value="abc"):
            result = s.summarize(_make_png_bytes(), original_level=3)
        assert result.blocked
        assert "unsupported_captioner_backend" in result.block_reason

    def test_unexpected_exception_blocked(self):
        s = ImageSummarizer()
        with patch.object(s, "_summarize", side_effect=RuntimeError("boom")):
            result = s.summarize(_make_png_bytes(), original_level=3)
        assert result.blocked
        assert "unexpected_error" in result.block_reason


# ---------------------------------------------------------------------------
# Tests: _call_ollama mock — success path
# ---------------------------------------------------------------------------

class TestCallOllama:
    def _mock_response(self, caption: str) -> MagicMock:
        resp = MagicMock()
        resp.read.return_value = json.dumps({"response": caption}).encode()
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    def test_returns_caption(self):
        with patch("urllib.request.urlopen", return_value=self._mock_response("A chart.")):
            result = _call_ollama("http://localhost:11434", "llava", "abc==", 5)
        assert result == "A chart."

    def test_caption_truncated_at_max(self):
        long_caption = "x" * 2000
        with patch("urllib.request.urlopen", return_value=self._mock_response(long_caption)):
            result = _call_ollama("http://localhost:11434", "llava", "abc==", 5)
        assert len(result) <= 1024

    def test_empty_caption_raises(self):
        with patch("urllib.request.urlopen", return_value=self._mock_response("")):
            with pytest.raises(ImageSummarizationError, match="empty_caption"):
                _call_ollama("http://localhost:11434", "llava", "abc==", 5)

    def test_url_error_raises(self):
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            with pytest.raises(ImageSummarizationError, match="captioner_unreachable"):
                _call_ollama("http://localhost:11434", "llava", "abc==", 5)

    def test_bad_json_raises(self):
        resp = MagicMock()
        resp.read.return_value = b"not-json"
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=resp):
            with pytest.raises(ImageSummarizationError, match="captioner_bad_json"):
                _call_ollama("http://localhost:11434", "llava", "abc==", 5)


# ---------------------------------------------------------------------------
# Tests: full summarize() happy path (mocked captioner)
# ---------------------------------------------------------------------------

class TestImageSummarizerHappyPath:
    def _mock_ollama(self, caption: str):
        resp = MagicMock()
        resp.read.return_value = json.dumps({"response": caption}).encode()
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        return patch("urllib.request.urlopen", return_value=resp)

    @pytest.mark.skipif(
        not __import__("importlib").util.find_spec("PIL"),
        reason="Pillow not installed",
    )
    def test_summarize_returns_description(self):
        s = ImageSummarizer(captioner="ollama/llava")
        with self._mock_ollama("A simple diagram."):
            result = s.summarize(_make_png_bytes(), original_level=3)
        assert not result.blocked
        assert result.description == "A simple diagram."
        assert result.summary_level == 1
        assert result.original_level == 3

    @pytest.mark.skipif(
        not __import__("importlib").util.find_spec("PIL"),
        reason="Pillow not installed",
    )
    def test_env_var_captioner_used(self, monkeypatch):
        monkeypatch.setenv("LLMESH_IMAGE_CAPTIONER", "ollama/llava:13b")
        s = ImageSummarizer()
        assert "llava:13b" in s._captioner

    def test_timeout_env_var_parsed(self, monkeypatch):
        monkeypatch.setenv("LLMESH_CAPTIONER_TIMEOUT", "45")
        s = ImageSummarizer()
        assert s._timeout == 45

    def test_invalid_timeout_env_uses_default(self, monkeypatch):
        monkeypatch.setenv("LLMESH_CAPTIONER_TIMEOUT", "not-a-number")
        s = ImageSummarizer()
        assert s._timeout == 30  # default


# ---------------------------------------------------------------------------
# Tests: strip_exif_and_encode (Pillow path)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not __import__("importlib").util.find_spec("PIL"),
    reason="Pillow not installed",
)
class TestStripExifAndEncode:
    def test_returns_valid_base64(self):
        b64 = _strip_exif_and_encode(_make_png_bytes())
        decoded = base64.b64decode(b64)
        assert decoded[:8] == b"\x89PNG\r\n\x1a\n"

    def test_corrupt_image_raises(self):
        with pytest.raises(ImageSummarizationError, match="image_encode_error"):
            _strip_exif_and_encode(b"not-an-image")
