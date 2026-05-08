"""VLMFeatureExtractor — v3-N15 image → numerical feature pipeline.

Two-stage extraction:

1. **ImageFirewall gate** — every image is classified by the existing
   ``llmesh.privacy.image_firewall.ImageFirewall``. L4 → BLOCK
   (extractor returns an empty result), L3 → SUMMARIZE (the caller is
   expected to route the produced summary through PrivacySummarizer
   before forwarding the features to a backend), L0/L1 → pass through.
2. **Vision LLM caption + feature parsing** — the (sanitised) caption
   string from a Vision LLM is mapped to a fixed-length numerical
   feature vector that downstream SPC / MT charts can consume.

LLM and image-decoder dependencies are **fully optional**. When the
caller does not wire in a real Vision LLM, ``MockVisionCaptioner``
produces a deterministic caption from raw pixel statistics
(when Pillow is available) or from a SHA-256 of the input bytes
(stdlib only). The extractor itself depends on stdlib + optional
Pillow.

Output
------
:class:`VLMFeature` carries:

- ``vector``     — tuple[float, ...] of length ``dimension``
- ``caption``    — the caption string used for parsing
- ``allowed``    — ``False`` when ImageFirewall blocked the image
- ``action``     — "ALLOW" | "SUMMARIZE" | "BLOCK"
- ``reason``     — machine-readable tag from the firewall
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Callable, Iterable, Protocol


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class VLMFeature:
    """Numerical feature derived from an image + Vision LLM caption."""

    vector: tuple[float, ...]
    caption: str
    allowed: bool
    action: str        # "ALLOW" | "SUMMARIZE" | "BLOCK"
    reason: str

    @property
    def blocked(self) -> bool:
        return self.action == "BLOCK"


# ---------------------------------------------------------------------------
# VisionCaptioner protocol
# ---------------------------------------------------------------------------

class VisionCaptioner(Protocol):
    """Anything that turns image bytes into a caption string."""

    def caption(self, image_bytes: bytes) -> str: ...


class MockVisionCaptioner:
    """Deterministic offline captioner for tests and air-gapped runs.

    When Pillow is installed, basic pixel statistics are used to produce
    a stable, image-content-aware string. Otherwise the SHA-256 of the
    input bytes is used. Both modes are deterministic — feeding the
    same bytes always yields the same caption.
    """

    def caption(self, image_bytes: bytes) -> str:
        try:
            from PIL import Image  # noqa: PLC0415
            from io import BytesIO
            img = Image.open(BytesIO(image_bytes))
            img.load()
            w, h = img.size
            mode = img.mode
            # Thumbnail to ~32x32 before reading pixel data so a 4K image
            # does not pull millions of bytes into memory just to compute
            # 64 sample-pixel statistics.
            preview = img.convert("L")
            preview.thumbnail((32, 32))
            sample = list(preview.getdata())
            mean = sum(sample) / max(len(sample), 1)
            return (
                f"image size={w}x{h} mode={mode} "
                f"luminance_mean={mean:.1f} count_dark={sum(1 for s in sample if s < 64)}"
            )
        except Exception:
            digest = hashlib.sha256(image_bytes).hexdigest()
            return f"opaque image sha256={digest[:16]} bytes={len(image_bytes)}"


# ---------------------------------------------------------------------------
# Default feature parser
# ---------------------------------------------------------------------------

_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")
_DEFECT_KEYWORDS = (
    "defect", "crack", "scratch", "anomaly", "fault",
    "burn", "missing", "warped", "discolor",
)


def _default_parse(caption: str, dimension: int) -> tuple[float, ...]:
    """Map a caption string to a fixed-length numeric vector.

    The first half of the vector is filled from numeric tokens parsed
    out of the caption (e.g. OCR digits, dimensions, percentages). The
    second half is a count of defect-related keywords plus simple
    string-length / character-class statistics. The mapping is
    intentionally simple — the goal is a *stable* numerical embedding
    that downstream SPC charts can monitor for drift.
    """
    if dimension <= 0:
        raise ValueError("dimension must be positive")
    numbers = [float(m.group()) for m in _NUMBER_RE.finditer(caption)]
    half = dimension // 2 if dimension >= 2 else dimension
    head = numbers[:half] + [0.0] * (half - len(numbers[:half]))
    lower = caption.lower()
    keyword_hits = float(sum(lower.count(k) for k in _DEFECT_KEYWORDS))
    length = float(len(caption))
    digits = float(sum(1 for c in caption if c.isdigit()))
    letters = float(sum(1 for c in caption if c.isalpha()))
    tail = [keyword_hits, length, digits, letters]
    while len(tail) < dimension - half:
        tail.append(0.0)
    return tuple(head + tail[: dimension - half])


# ---------------------------------------------------------------------------
# Extractor
# ---------------------------------------------------------------------------

ImageFirewallProtocol = Callable[[bytes], "object"]
"""Anything callable that returns an object with the image-firewall
   surface — see :class:`llmesh.privacy.image_firewall.ImageFirewall`."""


class VLMFeatureExtractor:
    """Image → SPC-ready numerical feature vector (v2.14+).

    Parameters
    ----------
    captioner:
        A VisionCaptioner implementation. Defaults to
        :class:`MockVisionCaptioner` so the extractor is usable
        out of the box.
    image_firewall:
        Optional :class:`llmesh.privacy.image_firewall.ImageFirewall`
        instance. When present, every input image is classified before
        captioning. ``None`` disables the gate (useful only when the
        caller has already classified the image).
    parser:
        Caption → vector function. Defaults to :func:`_default_parse`.
    dimension:
        Output vector dimension. Defaults to 16.
    """

    def __init__(
        self,
        captioner: VisionCaptioner | None = None,
        *,
        image_firewall=None,
        parser: Callable[[str, int], tuple[float, ...]] | None = None,
        dimension: int = 16,
    ) -> None:
        if dimension <= 0:
            raise ValueError("dimension must be positive")
        self._captioner = captioner or MockVisionCaptioner()
        self._firewall = image_firewall
        self._parser = parser or _default_parse
        self._dim = int(dimension)

    @property
    def dimension(self) -> int:
        return self._dim

    # ------------------------------------------------------------------
    # Single image
    # ------------------------------------------------------------------

    def extract(self, image_bytes: bytes) -> VLMFeature:
        action, reason = self._firewall_decision(image_bytes)
        if action == "BLOCK":
            return VLMFeature(
                vector=tuple([0.0] * self._dim),
                caption="",
                allowed=False,
                action="BLOCK",
                reason=reason,
            )
        try:
            caption = self._captioner.caption(image_bytes)
        except Exception:
            return VLMFeature(
                vector=tuple([0.0] * self._dim),
                caption="",
                allowed=False,
                action="BLOCK",
                reason="captioner_error_fail_closed",
            )
        if not isinstance(caption, str):
            return VLMFeature(
                vector=tuple([0.0] * self._dim),
                caption="",
                allowed=False,
                action="BLOCK",
                reason="captioner_returned_non_string",
            )
        vector = self._parser(caption, self._dim)
        if len(vector) != self._dim:
            raise RuntimeError(
                f"parser returned {len(vector)} values, expected {self._dim}"
            )
        return VLMFeature(
            vector=tuple(float(v) for v in vector),
            caption=caption,
            allowed=True,
            action=action,
            reason=reason,
        )

    def extract_many(self, images: Iterable[bytes]) -> list[VLMFeature]:
        return [self.extract(b) for b in images]

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _firewall_decision(self, image_bytes: bytes) -> tuple[str, str]:
        if self._firewall is None:
            return ("ALLOW", "no_image_firewall")
        # We accept either: (a) a callable returning an object with
        # ``.action`` / ``.reason`` (ImageFirewall.classify), or (b) a
        # bare callable returning a 2-tuple. Both feel natural in tests.
        try:
            decision = self._firewall(image_bytes)
        except Exception:
            return ("BLOCK", "image_firewall_error_fail_closed")
        action = getattr(decision, "action", None)
        reason = getattr(decision, "reason", "")
        if action is None and isinstance(decision, tuple) and len(decision) == 2:
            action, reason = decision
        if action not in ("ALLOW", "SUMMARIZE", "BLOCK"):
            return ("BLOCK", "image_firewall_unknown_decision")
        return (str(action), str(reason or ""))
