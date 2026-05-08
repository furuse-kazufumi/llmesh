"""DepthCameraAdapter — RGB-D depth camera adapter for LLMesh Industrial (v1.7.0).

Watches a drop directory for incoming depth frame files and converts each frame
into a SensorEvent containing the raw depth data as a PointCloud.

Supported frame formats
-----------------------
*.depth.bin
    Raw binary: 4-byte little-endian uint32 header (width × height), followed by
    width×height float32 depth values in metres, row-major order.

*.depth.npy  (requires numpy)
    NumPy .npy file containing a 2-D float32 array (H × W) in metres.

Usage::

    adapter = DepthCameraAdapter("/data/depth_drop", device_id="realsense_01")
    adapter.on_event(lambda ev: print(ev.metadata))
    await adapter.start()
    await adapter.stop()

Security invariants
-------------------
- No shell=True, eval, exec, pickle anywhere.
- File paths are never passed to shell commands.
- .npy files are loaded with allow_pickle=False.
"""
from __future__ import annotations

import asyncio
import logging
import struct
from collections.abc import Callable
from pathlib import Path
from typing import Any

from llmesh.industrial.sensor_event import Priority, SensorEvent
from llmesh.industrial.sensor_3d.point_cloud import PointCloud

logger = logging.getLogger(__name__)

_SUPPORTED_EXTENSIONS = {".bin", ".npy"}

# Cap on the in-memory "already-processed" set to bound memory in long runs.
_SEEN_SET_MAX = 10_000

# Tolerance (bytes) when comparing file sizes between consecutive polls to
# detect a stable upload.  Zero requires an exact match (safest default).
_STABILITY_TOLERANCE_BYTES = 0

EventCallback = Callable[[SensorEvent], None]


try:
    import numpy as _np
    _NUMPY_AVAILABLE = True
except ImportError:
    _np = None          # type: ignore[assignment]
    _NUMPY_AVAILABLE = False


class DepthCameraAdapter:
    """Watch a directory for depth frame files and emit SensorEvents.

    Parameters
    ----------
    watch_dir:
        Directory to monitor for incoming ``.depth.bin`` / ``.depth.npy`` files.
    device_id:
        Identifier of the depth camera.
    poll_interval_s:
        How often to scan the drop directory.
    max_range_m:
        Depth values exceeding this range are clipped (NaN / 0 treated as invalid).
    delete_after:
        If True, processed files are deleted.
    """

    def __init__(
        self,
        watch_dir: str | Path,
        *,
        device_id: str = "",
        poll_interval_s: float = 0.2,
        max_range_m: float = 10.0,
        delete_after: bool = False,
    ) -> None:
        self._watch_dir = Path(watch_dir)
        self._device_id = device_id
        self._poll_interval_s = max(0.05, poll_interval_s)
        self._max_range_m = max_range_m
        self._delete_after = delete_after
        self._callbacks: list[EventCallback] = []
        # Already-processed filenames; capped at _SEEN_SET_MAX (FIFO rotation).
        self._seen: set[str] = set()
        # Per-file size from the previous poll — used for stability detection.
        self._last_size: dict[str, int] = {}
        self._task: asyncio.Task | None = None  # type: ignore[type-arg]
        self._running = False

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def on_event(self, callback: EventCallback) -> None:
        self._callbacks.append(callback)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if self._running:
            return
        self._watch_dir.mkdir(parents=True, exist_ok=True)
        self._running = True
        self._task = asyncio.create_task(self._watch_loop(), name="depth_watch")

    async def stop(self) -> None:
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
                    # Accept .depth.bin and .depth.npy double-extensions
                    name = path.name
                    if not (name.endswith(".depth.bin") or name.endswith(".depth.npy")):
                        continue
                    if name in self._seen:
                        continue
                    if not self._is_size_stable(path):
                        continue
                    self._record_seen(name)
                    await self._process_frame(path)
                await asyncio.sleep(self._poll_interval_s)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("DepthCameraAdapter watch error: %s", exc)
                await asyncio.sleep(self._poll_interval_s)

    async def _process_frame(self, path: Path) -> None:
        try:
            raw = await asyncio.get_event_loop().run_in_executor(None, path.read_bytes)
        except OSError as exc:
            logger.warning("DepthCameraAdapter: cannot read %s: %s", path.name, exc)
            return

        try:
            if path.name.endswith(".depth.bin"):
                pc, meta = self._decode_bin(raw)
            else:
                pc, meta = self._decode_npy(raw)
        except Exception as exc:
            logger.warning("DepthCameraAdapter: decode error for %s: %s", path.name, exc)
            return

        meta["filename"] = path.name
        payload = pc.to_bytes()

        event = SensorEvent.create(
            sensor_id=path.name.split(".")[0],
            protocol="depth",
            payload=payload,
            priority=Priority.NORMAL,
            device_id=self._device_id,
            sensor_type="depth_frame",
            metadata=meta,
        )
        self._emit(event)

        if self._delete_after:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass

    def _decode_bin(self, data: bytes) -> tuple[PointCloud, dict[str, Any]]:
        """Decode raw .depth.bin format: uint32 width, uint32 height, float32 grid."""
        if len(data) < 8:
            raise ValueError("depth.bin too short for header")
        width, height = struct.unpack_from("<II", data, 0)
        expected = 8 + width * height * 4
        if len(data) < expected:
            raise ValueError(f"depth.bin too short: expected {expected}, got {len(data)}")
        points: list[tuple[float, float, float]] = []
        offset = 8
        for row in range(height):
            for col in range(width):
                d = struct.unpack_from("<f", data, offset)[0]
                offset += 4
                if d <= 0 or d > self._max_range_m:
                    continue
                # Convert pixel position + depth to normalised XYZ (no intrinsics assumed)
                x = col / max(width - 1, 1)
                y = row / max(height - 1, 1)
                points.append((x, y, d))
        meta: dict[str, Any] = {"width": width, "height": height, "point_count": len(points)}
        return PointCloud(points=points), meta

    def _decode_npy(self, data: bytes) -> tuple[PointCloud, dict[str, Any]]:
        """Decode numpy .npy depth map (H×W float32, metres)."""
        if not _NUMPY_AVAILABLE:
            raise RuntimeError("numpy is required for .depth.npy files — pip install numpy")
        import io
        arr = _np.load(io.BytesIO(data), allow_pickle=False)
        if arr.ndim != 2:
            raise ValueError(f"expected 2-D depth array, got shape {arr.shape}")
        height, width = arr.shape
        points: list[tuple[float, float, float]] = []
        for row in range(height):
            for col in range(width):
                d = float(arr[row, col])
                if d <= 0 or d > self._max_range_m:
                    continue
                x = col / max(width - 1, 1)
                y = row / max(height - 1, 1)
                points.append((x, y, d))
        meta: dict[str, Any] = {"width": int(width), "height": int(height), "point_count": len(points)}
        return PointCloud(points=points), meta

    def _is_size_stable(self, path: Path) -> bool:
        """Return True if size matches the previous poll (defends against partial writes)."""
        try:
            current = path.stat().st_size
        except OSError:
            return False
        previous = self._last_size.get(path.name)
        self._last_size[path.name] = current
        if previous is None:
            return False
        return abs(current - previous) <= _STABILITY_TOLERANCE_BYTES

    def _record_seen(self, name: str) -> None:
        self._seen.add(name)
        self._last_size.pop(name, None)
        if len(self._seen) > _SEEN_SET_MAX:
            for k in sorted(self._seen)[: _SEEN_SET_MAX // 2]:
                self._seen.discard(k)

    def _emit(self, event: SensorEvent) -> None:
        for cb in self._callbacks:
            try:
                cb(event)
            except Exception as exc:
                logger.error("DepthCameraAdapter callback error: %s", exc)
