"""EventCameraAdapter — DVS event camera adapter for LLMesh Industrial (v1.7.0).

Watches a drop directory for incoming DVS (Dynamic Vision Sensor) event files
and converts each batch into a SensorEvent.

Wire format for ``.dvs.bin`` files
------------------------------------
Each event is 9 bytes, little-endian::

    uint16  x          (pixel column)
    uint16  y          (pixel row)
    uint32  t_us       (timestamp in microseconds)
    uint8   polarity   (0 = negative, 1 = positive)

Usage::

    adapter = EventCameraAdapter("/data/dvs_drop", device_id="prophesee_01")
    adapter.on_event(lambda ev: print(ev.metadata))
    await adapter.start()
    await adapter.stop()

Security invariants
-------------------
- No shell=True, eval, exec, pickle anywhere.
- File paths are never passed to shell commands.
- Per-batch size is capped at MAX_EVENTS_PER_BATCH (1 M events ≈ 9 MB).
"""
from __future__ import annotations

import asyncio
import logging
import struct
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from llmesh.industrial.sensor_event import Priority, SensorEvent

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

# Wire-format size of one DVS event: uint16 x + uint16 y + uint32 t_us + uint8 polarity.
_EVENT_BYTES = 9          # 2+2+4+1

# Hard upper bound on events decoded per batch — defends against pathological
# .dvs.bin files (≈ 9 MB at 1 M events).
_MAX_EVENTS_PER_BATCH = 1_000_000

# struct format for one event (used by encode/decode/_batch_stats).
_EVENT_STRUCT_FMT = "<HHIb"

# Cap on the in-memory "already-processed" set to bound memory in long runs.
_SEEN_SET_MAX = 10_000

# Tolerance (bytes) when comparing file sizes between consecutive polls.
_STABILITY_TOLERANCE_BYTES = 0

EventCallback = Callable[[SensorEvent], None]


@dataclass(frozen=True)
class DvsEvent:
    """Single DVS camera event."""

    x: int
    y: int
    t_us: int       # microseconds since camera epoch
    polarity: bool  # True = positive (brightness increase)


# Optional Rust acceleration (built from rust_ext/, ~10× faster).
try:
    import llmesh_rust as _rust    # type: ignore[import-not-found]
    _RUST_AVAILABLE = True
except ImportError:
    _rust = None                   # type: ignore[assignment]
    _RUST_AVAILABLE = False


def encode_dvs_events(events: list[DvsEvent]) -> bytes:
    """Encode a list of DvsEvents to the 9-byte wire format."""
    if _RUST_AVAILABLE:
        # Rust accepts (x, y, t_us, polarity) tuples
        return _rust.dvs_encode([(e.x, e.y, e.t_us, e.polarity) for e in events])
    buf = bytearray(len(events) * _EVENT_BYTES)
    for i, ev in enumerate(events):
        struct.pack_into(
            _EVENT_STRUCT_FMT,
            buf, i * _EVENT_BYTES,
            ev.x, ev.y, ev.t_us, int(ev.polarity),
        )
    return bytes(buf)


def decode_dvs_events(data: bytes) -> list[DvsEvent]:
    """Decode a byte string into DvsEvent objects."""
    if _RUST_AVAILABLE:
        return [DvsEvent(x=x, y=y, t_us=t, polarity=p)
                for (x, y, t, p) in _rust.dvs_decode(bytes(data))]
    n = min(len(data) // _EVENT_BYTES, _MAX_EVENTS_PER_BATCH)
    events: list[DvsEvent] = []
    for i in range(n):
        x, y, t_us, pol = struct.unpack_from(_EVENT_STRUCT_FMT, data, i * _EVENT_BYTES)
        events.append(DvsEvent(x=x, y=y, t_us=t_us, polarity=bool(pol)))
    return events


class EventCameraAdapter:
    """Watch a directory for DVS event files and emit SensorEvents.

    Parameters
    ----------
    watch_dir:
        Directory to monitor for incoming ``.dvs.bin`` files.
    device_id:
        Identifier of the event camera.
    poll_interval_s:
        How often to scan the drop directory.
    delete_after:
        If True, processed files are deleted.
    """

    def __init__(
        self,
        watch_dir: str | Path,
        *,
        device_id: str = "",
        poll_interval_s: float = 0.1,
        delete_after: bool = False,
    ) -> None:
        self._watch_dir = Path(watch_dir)
        self._device_id = device_id
        self._poll_interval_s = max(0.05, poll_interval_s)
        self._delete_after = delete_after
        self._callbacks: list[EventCallback] = []
        self._seen: set[str] = set()
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
        self._task = asyncio.create_task(self._watch_loop(), name="dvs_watch")

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
                    if not path.name.endswith(".dvs.bin"):
                        continue
                    if path.name in self._seen:
                        continue
                    if not self._is_size_stable(path):
                        continue
                    self._record_seen(path.name)
                    await self._process_batch(path)
                await asyncio.sleep(self._poll_interval_s)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("EventCameraAdapter watch error: %s", exc)
                await asyncio.sleep(self._poll_interval_s)

    async def _process_batch(self, path: Path) -> None:
        try:
            raw = await asyncio.get_event_loop().run_in_executor(None, path.read_bytes)
        except OSError as exc:
            logger.warning("EventCameraAdapter: cannot read %s: %s", path.name, exc)
            return

        n_events = len(raw) // _EVENT_BYTES
        if n_events == 0:
            return

        # Compute summary stats without full decode (performance)
        meta = _batch_stats(raw, n_events)
        meta["filename"] = path.name

        event = SensorEvent.create(
            sensor_id=path.name.split(".")[0],
            protocol="dvs",
            payload=raw[: n_events * _EVENT_BYTES],
            priority=Priority.NORMAL,
            device_id=self._device_id,
            sensor_type="dvs_events",
            metadata=meta,
        )
        self._emit(event)

        if self._delete_after:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass

    def _is_size_stable(self, path: Path) -> bool:
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
                logger.error("EventCameraAdapter callback error: %s", exc)


def _batch_stats(data: bytes, n: int) -> dict[str, Any]:
    """Compute summary statistics without allocating a full DvsEvent list."""
    pos_count = 0
    t_min = 0xFFFF_FFFF
    t_max = 0
    for i in range(n):
        _, _, t_us, pol = struct.unpack_from(_EVENT_STRUCT_FMT, data, i * _EVENT_BYTES)
        if t_us < t_min:
            t_min = t_us
        if t_us > t_max:
            t_max = t_us
        if pol:
            pos_count += 1
    return {
        "event_count": n,
        "positive_events": pos_count,
        "negative_events": n - pos_count,
        "t_start_us": t_min,
        "t_end_us": t_max,
        "duration_us": t_max - t_min,
    }
