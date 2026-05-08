"""SerialAdapter — RS-232 / RS-485 serial sensor input for LLMesh Industrial (v1.4.0).

Reads raw bytes from a serial port and converts each frame into a SensorEvent
for the unified industrial pipeline.

Frame modes
-----------
line    — readline() split on ``\\n`` (ASCII instruments, e.g. NMEA-style sensors)
fixed   — read exactly ``frame_size`` bytes per frame
delimited — read until a custom byte delimiter (e.g. 0x0D for CR)

Usage::

    adapter = SerialAdapter(
        port="/dev/ttyUSB0",
        baud_rate=9600,
        frame_mode="line",
        sensor_id="weight_01",
        sensor_type="weight",
        unit="g",
        device_id="scale_a",
    )
    adapter.on_event(lambda ev: print(ev))
    adapter.start()
    # ... adapter reads in background thread until stop() is called
    adapter.stop()

Security invariants
-------------------
- port path is validated against allowed patterns; never passed to shell.
- No shell=True, eval, exec, pickle anywhere.
- pyserial is optional; missing it raises a clear RuntimeError.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Literal

from llmesh.industrial.sensor_event import Priority, SensorEvent

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional pyserial import
# ---------------------------------------------------------------------------

try:
    import serial  # type: ignore[import-untyped]
    _PYSERIAL_AVAILABLE = True
except ImportError:
    _PYSERIAL_AVAILABLE = False
    serial = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

FrameMode = Literal["line", "fixed", "delimited"]
EventCallback = Callable[[SensorEvent], None]

_VALID_PORT_PREFIXES = (
    "/dev/tty", "/dev/rfcomm", "/dev/serial",
    "COM",  # Windows COMn
)


def _validate_port(port: str) -> None:
    if not any(port.startswith(p) for p in _VALID_PORT_PREFIXES):
        raise ValueError(
            f"Unsupported serial port path: {port!r}. "
            f"Expected /dev/tty*, /dev/rfcomm*, /dev/serial*, or COMn."
        )


# ---------------------------------------------------------------------------
# SerialAdapter
# ---------------------------------------------------------------------------

class SerialAdapter:
    """Read frames from a serial port and emit SensorEvents.

    Parameters
    ----------
    port        : serial device path ("/dev/ttyUSB0", "COM3", …)
    baud_rate   : bits-per-second (1200 to 921600)
    frame_mode  : "line" | "fixed" | "delimited"
    frame_size  : bytes per frame (required for "fixed" mode)
    delimiter   : single byte value for "delimited" mode (default: 0x0D = CR)
    sensor_id   : identifies the sensor in SensorEvent
    sensor_type : semantic type ("weight", "temperature", "vibration", …)
    unit        : SI unit string
    device_id   : parent device identifier
    priority    : default Priority for emitted events
    timeout_s   : serial read timeout in seconds
    encoding    : if set, decode raw bytes and store as str in metadata["text"]
    """

    def __init__(
        self,
        port: str,
        baud_rate: int = 9600,
        *,
        frame_mode: FrameMode = "line",
        frame_size: int = 0,
        delimiter: int = 0x0D,
        sensor_id: str = "serial_sensor",
        sensor_type: str = "",
        unit: str = "",
        device_id: str = "",
        priority: Priority = Priority.NORMAL,
        timeout_s: float = 1.0,
        encoding: str | None = None,
    ) -> None:
        if not _PYSERIAL_AVAILABLE:
            raise RuntimeError(
                "pyserial is not installed — run: pip install llmesh[industrial]"
            )
        _validate_port(port)
        if frame_mode == "fixed" and frame_size <= 0:
            raise ValueError("frame_size must be > 0 when frame_mode='fixed'")
        if not (0 <= delimiter <= 255):
            raise ValueError("delimiter must be a byte value 0-255")

        self._port = port
        self._baud_rate = baud_rate
        self._frame_mode: FrameMode = frame_mode
        self._frame_size = frame_size
        self._delimiter = delimiter
        self._sensor_id = sensor_id
        self._sensor_type = sensor_type
        self._unit = unit
        self._device_id = device_id
        self._priority = priority
        self._timeout_s = timeout_s
        self._encoding = encoding

        self._callbacks: list[EventCallback] = []
        self._ser: serial.Serial | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def on_event(self, callback: EventCallback) -> None:
        """Register a callback invoked with each new SensorEvent."""
        self._callbacks.append(callback)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Open the serial port and begin reading in a background thread."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._ser = serial.Serial(
            port=self._port,
            baudrate=self._baud_rate,
            timeout=self._timeout_s,
        )
        self._thread = threading.Thread(
            target=self._read_loop,
            daemon=True,
            name=f"serial_adapter_{self._port}",
        )
        self._thread.start()
        logger.info("SerialAdapter: started on %s @ %d baud", self._port, self._baud_rate)

    def stop(self) -> None:
        """Signal the read loop to stop and close the port."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self._timeout_s + 1.0)
            self._thread = None
        if self._ser is not None and self._ser.is_open:
            try:
                self._ser.close()
            except Exception:
                pass
            self._ser = None
        logger.info("SerialAdapter: stopped (%s)", self._port)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _read_loop(self) -> None:
        assert self._ser is not None
        while not self._stop_event.is_set():
            try:
                raw = self._read_frame()
            except Exception as exc:
                if not self._stop_event.is_set():
                    logger.warning("SerialAdapter read error on %s: %s", self._port, exc)
                continue

            if not raw:
                continue

            meta: dict = {"port": self._port, "baud_rate": self._baud_rate}
            if self._encoding:
                try:
                    meta["text"] = raw.decode(self._encoding).strip()
                except UnicodeDecodeError:
                    meta["decode_error"] = True

            event = SensorEvent.create(
                sensor_id=self._sensor_id,
                protocol="serial",
                payload=raw,
                priority=self._priority,
                device_id=self._device_id,
                sensor_type=self._sensor_type,
                unit=self._unit,
                metadata=meta,
            )
            self._emit(event)

    def _read_frame(self) -> bytes:
        assert self._ser is not None
        if self._frame_mode == "line":
            return self._ser.readline()
        if self._frame_mode == "fixed":
            return self._ser.read(self._frame_size)
        # delimited
        buf = bytearray()
        delim = bytes([self._delimiter])
        while not self._stop_event.is_set():
            ch = self._ser.read(1)
            if not ch:
                break
            if ch == delim:
                break
            buf.extend(ch)
        return bytes(buf)

    def _emit(self, event: SensorEvent) -> None:
        for cb in self._callbacks:
            try:
                cb(event)
            except Exception as exc:
                logger.error("SerialAdapter callback error: %s", exc)
