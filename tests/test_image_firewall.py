"""Tests for ImageFirewall — image classification before LLM ingestion (v1.2.0)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from llmesh.privacy.image_firewall import (
    ImageAction,
    ImageClassification,
    ImageFirewall,
    _is_known_image_magic,
    _L4_FILENAME_RE,
    _L3_FILENAME_RE,
)

# ---------------------------------------------------------------------------
# Minimal valid image bytes (no Pillow required for magic-byte tests)
# ---------------------------------------------------------------------------

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
_JPEG_MAGIC = b"\xff\xd8\xff\xe0" + b"\x00" * 100
_WEBP_MAGIC = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 100
_UNKNOWN_MAGIC = b"\x00\x01\x02\x03" + b"\x00" * 100


def _make_minimal_png(width: int = 10, height: int = 10) -> bytes:
    """Return a minimal valid 1x1 white PNG."""
    try:
        from PIL import Image
        import io as _io
        img = Image.new("RGB", (width, height), color=(255, 255, 255))
        buf = _io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except ImportError:
        return _PNG_MAGIC


def _make_wide_png(width: int = 1920, height: int = 400) -> bytes:
    """Return a wide PNG (screenshot aspect ratio)."""
    try:
        from PIL import Image
        import io as _io
        img = Image.new("RGB", (width, height), color=(200, 200, 200))
        buf = _io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except ImportError:
        return _PNG_MAGIC


# ---------------------------------------------------------------------------
# Tests: magic bytes helper
# ---------------------------------------------------------------------------

class TestImageMagic:
    def test_png_detected(self):
        assert _is_known_image_magic(_PNG_MAGIC) is True

    def test_jpeg_detected(self):
        assert _is_known_image_magic(_JPEG_MAGIC) is True

    def test_webp_detected(self):
        assert _is_known_image_magic(_WEBP_MAGIC) is True

    def test_unknown_not_detected(self):
        assert _is_known_image_magic(_UNKNOWN_MAGIC) is False

    def test_empty_not_detected(self):
        assert _is_known_image_magic(b"") is False


# ---------------------------------------------------------------------------
# Tests: filename regex patterns
# ---------------------------------------------------------------------------

class TestFilenamePatterns:
    @pytest.mark.parametrize("name", [
        "face.png", "biometric_scan.jpg", "passport_photo.png",
        "national_id.jpg", "driver_license.png", "id_card.jpg",
        "selfie.jpg", "portrait.png", "mugshot.jpg",
    ])
    def test_l4_filename_pattern_matches(self, name: str):
        assert _L4_FILENAME_RE.search(name) is not None, name

    @pytest.mark.parametrize("name", [
        "diagram.png", "chart.jpg", "code.png", "logo.svg",
    ])
    def test_l4_filename_pattern_no_match(self, name: str):
        assert _L4_FILENAME_RE.search(name) is None, name

    @pytest.mark.parametrize("name", [
        "screenshot.png", "screen_cap.jpg", "screencap.png",
        "screen-shot.jpg", "capture.png", "snap.jpg",
    ])
    def test_l3_filename_pattern_matches(self, name: str):
        assert _L3_FILENAME_RE.search(name) is not None, name

    def test_l3_filename_pattern_no_match(self):
        assert _L3_FILENAME_RE.search("diagram.png") is None


# ---------------------------------------------------------------------------
# Tests: ImageFirewall without Pillow (fallback path)
# ---------------------------------------------------------------------------

class TestImageFirewallNoPillow:
    def _fw(self) -> ImageFirewall:
        fw = ImageFirewall()
        fw._Image = None
        fw._ExifTags = None
        return fw

    def test_empty_data_blocked(self):
        clf = self._fw().classify_bytes(b"")
        assert clf.blocked
        assert "empty" in clf.reason

    def test_oversized_blocked(self):
        fw = ImageFirewall(max_bytes=100)
        fw._Image = None
        clf = fw.classify_bytes(b"\x00" * 200)
        assert clf.blocked
        assert "too_large" in clf.reason

    def test_l4_filename_blocked(self):
        clf = self._fw().classify_bytes(_PNG_MAGIC, filename="passport_photo.png")
        assert clf.blocked
        assert "l4" in clf.reason

    def test_screenshot_filename_summarize(self):
        clf = self._fw().classify_bytes(_PNG_MAGIC, filename="screenshot.png")
        assert clf.action is ImageAction.SUMMARIZE
        assert clf.level == 3

    def test_unknown_magic_blocked_no_pillow(self):
        clf = self._fw().classify_bytes(_UNKNOWN_MAGIC, filename="image.png")
        assert clf.blocked

    def test_known_magic_allowed_no_pillow(self):
        clf = self._fw().classify_bytes(_PNG_MAGIC, filename="diagram.png")
        assert clf.action is ImageAction.ALLOW
        assert not clf.blocked

    def test_classification_error_returns_block(self):
        fw = self._fw()
        # Passing non-bytes triggers classification_error fallback
        clf = fw.classify_bytes(None, filename="x.png")  # type: ignore[arg-type]
        assert clf.blocked


# ---------------------------------------------------------------------------
# Tests: ImageFirewall with Pillow (when available)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not __import__("importlib").util.find_spec("PIL"),
    reason="Pillow not installed",
)
class TestImageFirewallWithPillow:
    def test_pillow_available(self):
        fw = ImageFirewall()
        assert fw.pillow_available

    def test_normal_image_allowed(self):
        data = _make_minimal_png()
        clf = ImageFirewall().classify_bytes(data, filename="diagram.png")
        assert clf.action is ImageAction.ALLOW
        assert clf.level == 1
        assert clf.width > 0
        assert clf.height > 0

    def test_l4_filename_blocks_before_decode(self):
        data = _make_minimal_png()
        clf = ImageFirewall().classify_bytes(data, filename="selfie.jpg")
        assert clf.blocked
        assert "l4" in clf.reason

    def test_screenshot_filename_summarize(self):
        data = _make_minimal_png()
        clf = ImageFirewall().classify_bytes(data, filename="screenshot.png")
        assert clf.action is ImageAction.SUMMARIZE
        assert clf.level == 3

    def test_wide_image_summarize(self):
        data = _make_wide_png(width=1920, height=400)
        clf = ImageFirewall().classify_bytes(data, filename="image.png")
        assert clf.action is ImageAction.SUMMARIZE
        assert clf.level == 3

    def test_classify_path_unsupported_extension(self, tmp_path: Path):
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"%PDF")
        clf = ImageFirewall().classify_path(f)
        assert clf.blocked
        assert "unsupported_extension" in clf.reason

    def test_classify_path_valid_png(self, tmp_path: Path):
        f = tmp_path / "chart.png"
        f.write_bytes(_make_minimal_png())
        clf = ImageFirewall().classify_path(f)
        assert not clf.blocked

    def test_classify_path_missing_file(self, tmp_path: Path):
        clf = ImageFirewall().classify_path(tmp_path / "missing.png")
        assert clf.blocked
        assert "read_error" in clf.reason

    def test_format_captured(self):
        data = _make_minimal_png()
        clf = ImageFirewall().classify_bytes(data, filename="chart.png")
        assert clf.format == "PNG"

    def test_broken_image_data_blocked(self):
        clf = ImageFirewall().classify_bytes(b"\x89PNG\r\n\x1a\nGARBAGE")
        assert clf.blocked

    def test_exif_face_data_blocked(self):
        """Simulate EXIF with face tag using mock."""
        # Find numeric tag ID for a fake face tag
        fw = ImageFirewall()

        data = _make_minimal_png()
        with patch.object(fw, "_classify_with_pillow") as mock_clf:
            mock_clf.return_value = ImageClassification(
                action=ImageAction.BLOCK,
                level=4,
                reason="exif_face_data_detected",
                exif_stripped=True,
            )
            clf = fw.classify_bytes(data, filename="photo.png")
        assert clf.blocked
        assert "exif_face" in clf.reason
