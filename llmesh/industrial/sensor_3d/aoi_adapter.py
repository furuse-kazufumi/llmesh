"""AoiAdapter — AOI (Automated Optical Inspection) camera adapter for LLMesh Industrial (v1.7.0).

Watches a drop directory for incoming inspection images (JPEG / PNG) produced
by an AOI system, converts each image into a SensorEvent, and optionally
attaches defect metadata parsed from a co-located JSON sidecar file.

Sidecar convention::

    image file:   <timestamp>.<ext>           (e.g. 20260507_120000.jpg)
    sidecar file: <timestamp>.aoi.json        (optional)

Sidecar JSON schema (all fields optional)::

    {
        "result": "ok" | "ng",
        "defects": [
            {"label": "scratch", "confidence": 0.92,
             "bbox": [x, y, w, h]}
        ],
        "board_id": "BOARD-001"
    }

Usage::

    adapter = AoiAdapter("/data/aoi_drop", device_id="smt_aoi_01")
    adapter.on_event(lambda ev: print(ev.metadata))
    await adapter.start()
    await adapter.stop()

Security invariants
-------------------
- No shell=True, eval, exec, pickle anywhere.
- File paths from the watch directory are never passed to shell commands.
- Sidecar JSON is parsed with strict size limit (64 KiB max).
- Pillow is an optional dependency — raw bytes are used when absent.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from llmesh.industrial.sensor_event import Priority, SensorEvent

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

# Maximum size accepted for an AOI sidecar JSON file.  Larger sidecars are
# skipped to defend against malformed / malicious drop-folder content.
_SIDECAR_MAX_BYTES = 65_536  # 64 KiB

# File extensions recognised as AOI inspection images.  Anything else in the
# drop directory is ignored (e.g. .csv, .txt, partial uploads).
_SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp"}

# Upper bound on the in-memory "already-processed filenames" set.  Without a
# cap, a long-running watcher would leak memory linear to the number of
# images ever seen.  When the set exceeds this size it is rotated.
_SEEN_SET_MAX = 10_000

# After processing a file we wait one extra poll cycle before considering its
# size *stable* — this defends against reading half-written uploads.  A file
# whose size changed between consecutive polls is skipped this round.
_STABILITY_TOLERANCE_BYTES = 0  # exact match required between polls

EventCallback = Callable[[SensorEvent], None]


@dataclass
class AoiResult:
    """Parsed AOI inspection result from a sidecar JSON file."""

    result: str = "unknown"        # "ok" | "ng" | "unknown"
    defects: list[dict[str, Any]] = field(default_factory=list)
    board_id: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AoiResult:
        return cls(
            result=str(d.get("result", "unknown")).lower(),
            defects=list(d.get("defects", [])),
            board_id=str(d.get("board_id", "")),
            raw=d,
        )

    @property
    def is_ok(self) -> bool:
        return self.result == "ok"

    @property
    def defect_count(self) -> int:
        return len(self.defects)


class AoiAdapter:
    """Watch a directory for AOI inspection images and emit SensorEvents.

    Parameters
    ----------
    watch_dir:
        Directory to monitor for incoming ``.jpg``/``.png`` files.
    device_id:
        Identifier of the AOI camera or inspection station.
    poll_interval_s:
        How often to scan the drop directory.
    move_processed_to:
        If set, processed files are moved here instead of deleted.
    delete_after:
        If True and *move_processed_to* is not set, processed files are deleted.
    priority_fn:
        Optional callable ``(AoiResult) -> Priority`` for dynamic priority.
    """

    def __init__(
        self,
        watch_dir: str | Path,
        *,
        device_id: str = "",
        poll_interval_s: float = 0.5,
        move_processed_to: str | Path | None = None,
        delete_after: bool = False,
        priority_fn: Callable[[AoiResult], Priority] | None = None,
    ) -> None:
        self._watch_dir = Path(watch_dir)
        self._device_id = device_id
        self._poll_interval_s = max(0.1, poll_interval_s)
        self._move_to: Path | None = Path(move_processed_to) if move_processed_to else None
        self._delete_after = delete_after
        self._priority_fn = priority_fn or _default_priority
        self._callbacks: list[EventCallback] = []
        # Already-processed filenames.  Capped at _SEEN_SET_MAX entries; when
        # the cap is exceeded the oldest half is discarded (FIFO rotation).
        self._seen: set[str] = set()
        # Last-observed file size keyed by name — used to skip files whose
        # size is still changing (i.e. upload still in progress).
        self._last_size: dict[str, int] = {}
        self._task: asyncio.Task | None = None  # type: ignore[type-arg]
        self._running = False

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def on_event(self, callback: EventCallback) -> None:
        """Register a callback invoked with each new SensorEvent."""
        self._callbacks.append(callback)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Begin watching the drop directory."""
        if self._running:
            return
        self._watch_dir.mkdir(parents=True, exist_ok=True)
        if self._move_to is not None:
            self._move_to.mkdir(parents=True, exist_ok=True)
        self._running = True
        self._task = asyncio.create_task(self._watch_loop(), name="aoi_watch")

    async def stop(self) -> None:
        """Stop watching."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _watch_loop(self) -> None:
        while self._running:
            try:
                for path in sorted(self._watch_dir.iterdir()):
                    if path.suffix.lower() not in _SUPPORTED_EXTENSIONS:
                        continue
                    key = path.name
                    if key in self._seen:
                        continue
                    if not self._is_size_stable(path):
                        continue  # still being written — try again next poll
                    self._record_seen(key)
                    await self._process_image(path)
                await asyncio.sleep(self._poll_interval_s)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("AoiAdapter watch error: %s", exc)
                await asyncio.sleep(self._poll_interval_s)

    def _is_size_stable(self, path: Path) -> bool:
        """Return True if *path* has the same size as the previous poll.

        Defends against reading an upload still in progress: the file is
        only considered ready when two consecutive polls agree on its size.
        """
        try:
            current = path.stat().st_size
        except OSError:
            return False
        previous = self._last_size.get(path.name)
        self._last_size[path.name] = current
        if previous is None:
            return False  # first observation — wait one more cycle
        return abs(current - previous) <= _STABILITY_TOLERANCE_BYTES

    def _record_seen(self, key: str) -> None:
        """Add *key* to the seen-set and rotate it if the cap is hit."""
        self._seen.add(key)
        self._last_size.pop(key, None)
        if len(self._seen) > _SEEN_SET_MAX:
            # Drop the oldest half (sorted by name; deterministic).
            for k in sorted(self._seen)[: _SEEN_SET_MAX // 2]:
                self._seen.discard(k)

    async def _process_image(self, path: Path) -> None:
        try:
            payload = await asyncio.get_event_loop().run_in_executor(
                None, path.read_bytes
            )
        except OSError as exc:
            logger.warning("AoiAdapter: cannot read %s: %s", path.name, exc)
            return

        aoi_result = self._load_sidecar(path)
        priority = self._priority_fn(aoi_result)

        meta: dict[str, Any] = {
            "filename": path.name,
            "size_bytes": len(payload),
            "aoi_result": aoi_result.result,
            "defect_count": aoi_result.defect_count,
        }
        if aoi_result.board_id:
            meta["board_id"] = aoi_result.board_id
        if aoi_result.defects:
            meta["defects"] = aoi_result.defects

        event = SensorEvent.create(
            sensor_id=path.stem,
            protocol="aoi",
            payload=payload,
            priority=priority,
            device_id=self._device_id,
            sensor_type="aoi_image",
            metadata=meta,
        )
        self._emit(event)
        self._post_process(path)

    def _load_sidecar(self, image_path: Path) -> AoiResult:
        """Load and parse the optional JSON sidecar file."""
        sidecar = image_path.with_suffix(".aoi.json")
        if not sidecar.exists():
            return AoiResult()
        try:
            raw = sidecar.read_bytes()
            if len(raw) > _SIDECAR_MAX_BYTES:
                logger.warning("AoiAdapter: sidecar %s exceeds 64 KiB — skipping", sidecar.name)
                return AoiResult()
            return AoiResult.from_dict(json.loads(raw))
        except Exception as exc:
            logger.warning("AoiAdapter: sidecar parse error for %s: %s", sidecar.name, exc)
            return AoiResult()

    def _post_process(self, path: Path) -> None:
        try:
            if self._move_to is not None:
                dest = self._move_to / path.name
                path.rename(dest)
                # Move sidecar too if present
                sidecar = path.with_suffix(".aoi.json")
                if sidecar.exists():
                    sidecar.rename(self._move_to / sidecar.name)
            elif self._delete_after:
                path.unlink(missing_ok=True)
                sidecar = path.with_suffix(".aoi.json")
                sidecar.unlink(missing_ok=True)
        except Exception as exc:
            logger.warning("AoiAdapter: post-process error for %s: %s", path.name, exc)

    def _emit(self, event: SensorEvent) -> None:
        for cb in self._callbacks:
            try:
                cb(event)
            except Exception as exc:
                logger.error("AoiAdapter callback error: %s", exc)


def _default_priority(result: AoiResult) -> Priority:
    """NG → HIGH priority; unknown/ok → NORMAL."""
    if result.result == "ng":
        return Priority.HIGH
    return Priority.NORMAL
