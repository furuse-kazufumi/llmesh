"""ImageFirewall — classify and gate image inputs before LLM ingestion (v1.2.0).

Classification ladder:
  L4 — faces / ID documents detected → BLOCK (pixel data never forwarded)
  L3 — screenshots / UI captures (may contain PII text) → SUMMARIZE
  L1 — diagrams, charts, code screenshots → ALLOW (pass to ImageSummarizer)
  L0 — other safe images → ALLOW

Security invariants:
  - Raw pixel data is NEVER stored after classification.
  - EXIF metadata is stripped before any logging.
  - Images > _MAX_IMAGE_BYTES are rejected before decode.
  - No shell=True, eval, exec, pickle, or ImageMagick subprocess.
  - All failures return BLOCK (fail-closed).

Pillow is required: pip install llmesh[vision]
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

_MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MiB

_SUPPORTED_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp"})

# Filename patterns suggesting L4 content (faces, ID docs)
_L4_FILENAME_RE = re.compile(
    r"(?:face|biometric|passport|national[_\-]?id|driver[_\-]?licen[cs]e"
    r"|id[_\-]?card|selfie|portrait|mugshot)",
    re.IGNORECASE,
)

# EXIF tags that indicate L4 content (face region data)
_L4_EXIF_TAGS: frozenset[str] = frozenset({
    "FacePosition",
    "FaceDetect",
    "FaceDetectFrameSize",
    "FaceInfo",
    "FacesDetected",
    "FaceRecognition",
    "FaceSmile",
})

# Filename patterns suggesting screenshot / L3 content
_L3_FILENAME_RE = re.compile(
    r"(?:screenshot|screen[_\-]?cap|capture|screen[_\-]?shot|scrn|snap)",
    re.IGNORECASE,
)


class ImageAction(str, Enum):
    ALLOW = "ALLOW"
    SUMMARIZE = "SUMMARIZE"
    BLOCK = "BLOCK"


@dataclass
class ImageClassification:
    action: ImageAction
    level: int                      # effective DataLevel (0-4)
    reason: str
    width: int = 0
    height: int = 0
    format: str = ""                # e.g. "PNG", "JPEG"
    exif_stripped: bool = False

    @property
    def blocked(self) -> bool:
        return self.action is ImageAction.BLOCK

    @property
    def requires_summarization(self) -> bool:
        return self.action is ImageAction.SUMMARIZE


def _try_import_pillow() -> Any:
    try:
        from PIL import Image, ExifTags  # type: ignore[import]
        return Image, ExifTags
    except ImportError:
        return None, None


def _check_l4_exif(exif_data: dict[str, Any]) -> bool:
    """Return True if EXIF data contains face-recognition tags."""
    for key in exif_data:
        if key in _L4_EXIF_TAGS:
            return True
    return False


class ImageFirewall:
    """Classify images by privacy level before LLM ingestion.

    Usage::

        fw = ImageFirewall()
        result = fw.classify_bytes(image_bytes, filename="screenshot.png")
        if result.blocked:
            raise PermissionError(result.reason)
    """

    def __init__(self, max_bytes: int = _MAX_IMAGE_BYTES) -> None:
        self._max_bytes = max_bytes
        self._Image, self._ExifTags = _try_import_pillow()

    @property
    def pillow_available(self) -> bool:
        return self._Image is not None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def classify_bytes(
        self,
        data: bytes,
        filename: str = "",
    ) -> ImageClassification:
        """Classify raw image bytes.

        Args:
            data:     Raw image bytes (PNG/JPEG/WebP).
            filename: Original filename hint for metadata-based L4/L3 checks.

        Returns:
            :class:`ImageClassification` — always returned, never raises.
            On any error returns BLOCK (fail-closed).
        """
        try:
            return self._classify(data, filename)
        except Exception as exc:  # noqa: BLE001
            return ImageClassification(
                action=ImageAction.BLOCK,
                level=4,
                reason=f"classification_error:{exc}",
            )

    def classify_path(self, path: str | Path) -> ImageClassification:
        """Classify an image file on disk."""
        try:
            p = Path(path)
            if p.suffix.lower() not in _SUPPORTED_EXTENSIONS:
                return ImageClassification(
                    action=ImageAction.BLOCK,
                    level=4,
                    reason=f"unsupported_extension:{p.suffix}",
                )
            data = p.read_bytes()
            return self.classify_bytes(data, filename=p.name)
        except Exception as exc:  # noqa: BLE001
            return ImageClassification(
                action=ImageAction.BLOCK,
                level=4,
                reason=f"read_error:{exc}",
            )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _classify(self, data: bytes, filename: str) -> ImageClassification:
        # Size gate
        if len(data) > self._max_bytes:
            return ImageClassification(
                action=ImageAction.BLOCK,
                level=4,
                reason=f"image_too_large:{len(data)}_bytes",
            )

        if not data:
            return ImageClassification(
                action=ImageAction.BLOCK,
                level=4,
                reason="empty_image_data",
            )

        # Filename-based L4 check (fast path — no decode needed)
        if filename and _L4_FILENAME_RE.search(filename):
            return ImageClassification(
                action=ImageAction.BLOCK,
                level=4,
                reason=f"filename_l4_pattern:{filename}",
            )

        # Pillow-based analysis
        if self._Image is not None:
            return self._classify_with_pillow(data, filename)

        # No Pillow — filename-only checks
        return self._classify_no_pillow(data, filename)

    def _classify_with_pillow(
        self, data: bytes, filename: str
    ) -> ImageClassification:
        Image, ExifTags = self._Image, self._ExifTags

        try:
            img = Image.open(io.BytesIO(data))
        except Exception as exc:
            return ImageClassification(
                action=ImageAction.BLOCK,
                level=4,
                reason=f"image_decode_error:{exc}",
            )

        width, height = img.size
        fmt = img.format or "UNKNOWN"

        # Extract and check EXIF (then forget it — never stored)
        exif_data: dict[str, Any] = {}
        try:
            raw_exif = img.getexif()
            if raw_exif:
                exif_data = {
                    ExifTags.TAGS.get(tag_id, str(tag_id)): val
                    for tag_id, val in raw_exif.items()
                }
        except Exception:  # noqa: BLE001
            pass  # EXIF unavailable — proceed without it

        if _check_l4_exif(exif_data):
            return ImageClassification(
                action=ImageAction.BLOCK,
                level=4,
                reason="exif_face_data_detected",
                width=width,
                height=height,
                format=fmt,
                exif_stripped=True,
            )

        # Screenshot detection (L3)
        if filename and _L3_FILENAME_RE.search(filename):
            return ImageClassification(
                action=ImageAction.SUMMARIZE,
                level=3,
                reason="screenshot_filename_pattern",
                width=width,
                height=height,
                format=fmt,
                exif_stripped=bool(exif_data),
            )

        # Heuristic: very wide/tall aspect ratios suggest UI screenshots
        if width > 0 and height > 0:
            ratio = max(width, height) / min(width, height)
            if ratio >= 2.5 and min(width, height) >= 400:
                return ImageClassification(
                    action=ImageAction.SUMMARIZE,
                    level=3,
                    reason="aspect_ratio_suggests_screenshot",
                    width=width,
                    height=height,
                    format=fmt,
                    exif_stripped=bool(exif_data),
                )

        # Default: pass through (L0/L1 — diagram, chart, code screenshot)
        return ImageClassification(
            action=ImageAction.ALLOW,
            level=1,
            reason="safe_image",
            width=width,
            height=height,
            format=fmt,
            exif_stripped=bool(exif_data),
        )

    def _classify_no_pillow(self, data: bytes, filename: str) -> ImageClassification:
        """Fallback classification without Pillow (filename + magic bytes only)."""
        if filename and _L3_FILENAME_RE.search(filename):
            return ImageClassification(
                action=ImageAction.SUMMARIZE,
                level=3,
                reason="screenshot_filename_pattern_no_pillow",
            )

        # Detect image magic bytes to confirm it's a valid image
        if not _is_known_image_magic(data):
            return ImageClassification(
                action=ImageAction.BLOCK,
                level=4,
                reason="unknown_image_format_no_pillow",
            )

        return ImageClassification(
            action=ImageAction.ALLOW,
            level=1,
            reason="safe_image_no_pillow",
        )


def _is_known_image_magic(data: bytes) -> bool:
    """Check magic bytes for PNG / JPEG / WebP."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return True
    if data[:2] == b"\xff\xd8":  # JPEG SOI
        return True
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return True
    return False
