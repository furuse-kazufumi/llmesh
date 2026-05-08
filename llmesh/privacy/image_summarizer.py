"""ImageSummarizer — convert L3 images to privacy-safe text descriptions (v1.2.0).

For L3 images (screenshots, UI captures), this module generates a text caption
via a local Vision-capable LLM (e.g. Ollama + LLaVA).  The text description —
never the raw pixels — is passed to the main LLM backend.

Security invariants:
  - Raw pixel data is NEVER stored or logged after summarisation.
  - EXIF metadata is stripped before encoding.
  - No shell=True, eval, exec, or pickle.
  - All failures return BLOCK (fail-closed).
  - The captioner URL is read from env vars only (no user-controlled input).
  - SSRF guard: captioner URL is restricted to localhost and RFC 1918 addresses.

Configuration:
  LLMESH_IMAGE_CAPTIONER  — backend URL + model, format: "ollama/llava" (default)
                            or "ollama/llava:7b" for a specific tag.
  LLMESH_CAPTIONER_URL    — base URL override (default: http://localhost:11434)
  LLMESH_CAPTIONER_TIMEOUT — request timeout in seconds (default: 30)

Pillow is required: pip install llmesh[vision]
"""
from __future__ import annotations

import base64
import io
import ipaddress
import json
import os
import urllib.parse
import urllib.request
import urllib.error
from dataclasses import dataclass

_DEFAULT_CAPTIONER = "ollama/llava"
_DEFAULT_URL = "http://localhost:11434"
_DEFAULT_TIMEOUT = 30
_MAX_CAPTION_CHARS = 1024


class ImageSummarizationError(Exception):
    """Raised when captioning fails and the image must be blocked."""


@dataclass
class ImageSummary:
    """Result of image summarisation."""

    original_level: int          # DataLevel before summarisation (typically 3)
    summary_level: int = 1       # output DataLevel (always <= 1)
    description: str = ""        # safe text for LLM prompt injection
    blocked: bool = False
    block_reason: str = ""


def _try_import_pillow():  # type: ignore[return]
    try:
        from PIL import Image  # type: ignore[import]
        return Image
    except ImportError:
        return None


def _strip_exif_and_encode(data: bytes) -> str:
    """Strip EXIF and return base64-encoded PNG bytes."""
    Image = _try_import_pillow()
    if Image is None:
        raise ImageSummarizationError("Pillow not installed — pip install llmesh[vision]")

    try:
        img = Image.open(io.BytesIO(data))
        # Re-save as PNG without EXIF (Pillow drops EXIF by default on save without exif= kwarg)
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception as exc:
        raise ImageSummarizationError(f"image_encode_error:{exc}") from exc


def _validate_captioner_url(url: str) -> None:
    """Restrict captioner URL to localhost and RFC 1918 addresses (SSRF guard)."""
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception as exc:
        raise ImageSummarizationError(f"invalid_captioner_url:{exc}") from exc
    if parsed.scheme not in ("http", "https"):
        raise ImageSummarizationError(f"captioner_url_bad_scheme:{parsed.scheme!r}")
    hostname = (parsed.hostname or "").lower().strip("[]")
    if hostname in ("localhost", "127.0.0.1", "::1"):
        return
    try:
        addr = ipaddress.ip_address(hostname)
    except ValueError:
        raise ImageSummarizationError(f"captioner_url_non_local_host:{hostname!r}")
    if not addr.is_private:
        raise ImageSummarizationError(f"captioner_url_non_private:{hostname!r}")


def _parse_captioner(spec: str) -> tuple[str, str]:
    """Parse 'ollama/llava' or 'ollama/llava:7b' → (backend, model)."""
    parts = spec.split("/", 1)
    if len(parts) != 2:
        raise ImageSummarizationError(f"invalid_captioner_spec:{spec!r}")
    return parts[0].strip().lower(), parts[1].strip()


def _call_ollama(base_url: str, model: str, b64: str, timeout: int) -> str:
    """POST to Ollama's /api/generate with a vision prompt."""
    url = f"{base_url.rstrip('/')}/api/generate"
    payload = json.dumps({
        "model": model,
        "prompt": (
            "Describe this image in one or two sentences. "
            "Focus on the content and purpose. "
            "Do NOT include any personal data, names, or identifiable information."
        ),
        "images": [b64],
        "stream": False,
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    from llmesh.security.http_limits import (  # local import — avoids cycles
        DEFAULT_LLM_RESPONSE_BYTES,
        ResponseTooLargeError,
        read_capped,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            raw = read_capped(resp, max_bytes=DEFAULT_LLM_RESPONSE_BYTES)
            body = json.loads(raw.decode("utf-8"))
            caption = body.get("response", "").strip()
            if not caption:
                raise ImageSummarizationError("empty_caption_from_captioner")
            return caption[:_MAX_CAPTION_CHARS]
    except ResponseTooLargeError as exc:
        raise ImageSummarizationError(f"captioner_response_too_large:{exc.cap}") from exc
    except urllib.error.URLError as exc:
        raise ImageSummarizationError(f"captioner_unreachable:{exc}") from exc
    except json.JSONDecodeError as exc:
        raise ImageSummarizationError(f"captioner_bad_json:{exc}") from exc


class ImageSummarizer:
    """Summarise L3 images into privacy-safe text descriptions.

    Usage::

        summarizer = ImageSummarizer()
        summary = summarizer.summarize(image_bytes, original_level=3)
        if summary.blocked:
            raise PermissionError(summary.block_reason)
        text_for_llm = summary.description
    """

    def __init__(
        self,
        captioner: str | None = None,
        base_url: str | None = None,
        timeout: int | None = None,
    ) -> None:
        self._captioner = captioner or os.environ.get(
            "LLMESH_IMAGE_CAPTIONER", _DEFAULT_CAPTIONER
        )
        self._base_url = base_url or os.environ.get(
            "LLMESH_CAPTIONER_URL", _DEFAULT_URL
        )
        try:
            self._timeout = timeout or int(
                os.environ.get("LLMESH_CAPTIONER_TIMEOUT", str(_DEFAULT_TIMEOUT))
            )
        except ValueError:
            self._timeout = _DEFAULT_TIMEOUT
        _validate_captioner_url(self._base_url)

    def summarize(self, data: bytes, original_level: int = 3) -> ImageSummary:
        """Generate a text description of *data* (image bytes).

        Always returns an :class:`ImageSummary`; never raises.
        On any error returns blocked=True (fail-closed).
        """
        try:
            return self._summarize(data, original_level)
        except ImageSummarizationError as exc:
            return ImageSummary(
                original_level=original_level,
                blocked=True,
                block_reason=str(exc),
            )
        except Exception as exc:  # noqa: BLE001
            return ImageSummary(
                original_level=original_level,
                blocked=True,
                block_reason=f"unexpected_error:{exc}",
            )

    def _summarize(self, data: bytes, original_level: int) -> ImageSummary:
        if not data:
            raise ImageSummarizationError("empty_image_data")

        # Strip EXIF and encode (Pillow required)
        b64 = _strip_exif_and_encode(data)

        backend, model = _parse_captioner(self._captioner)

        if backend == "ollama":
            caption = _call_ollama(self._base_url, model, b64, self._timeout)
        else:
            raise ImageSummarizationError(f"unsupported_captioner_backend:{backend!r}")

        return ImageSummary(
            original_level=original_level,
            summary_level=1,
            description=caption,
            blocked=False,
        )
