"""Tests for SerialAdapter (v1.4.0)."""
from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import pytest

from llmesh.industrial.sensor_event import Priority, SensorEvent
from llmesh.industrial.serial_adapter import SerialAdapter, _validate_port


# ---------------------------------------------------------------------------
# Port validation
# ---------------------------------------------------------------------------

class TestValidatePort:
    def test_linux_tty(self):
        _validate_port("/dev/ttyUSB0")  # no exception

    def test_linux_ttyS(self):
        _validate_port("/dev/ttyS0")

    def test_rfcomm(self):
        _validate_port("/dev/rfcomm0")

    def test_windows_com(self):
        _validate_port("COM1")
        _validate_port("COM12")

    def test_invalid_path_rejected(self):
        with pytest.raises(ValueError, match="Unsupported serial port"):
            _validate_port("/tmp/evil_path")

    def test_empty_rejected(self):
        with pytest.raises(ValueError, match="Unsupported serial port"):
            _validate_port("")

    def test_relative_path_rejected(self):
        with pytest.raises(ValueError, match="Unsupported serial port"):
            _validate_port("ttyUSB0")


# ---------------------------------------------------------------------------
# Constructor validation
# ---------------------------------------------------------------------------

class TestConstructorValidation:
    def test_fixed_mode_requires_frame_size(self, mock_serial):
        with pytest.raises(ValueError, match="frame_size"):
            SerialAdapter("/dev/ttyUSB0", frame_mode="fixed", frame_size=0)

    def test_invalid_delimiter(self, mock_serial):
        with pytest.raises(ValueError, match="delimiter"):
            SerialAdapter("/dev/ttyUSB0", delimiter=256)

    def test_missing_pyserial_raises(self, monkeypatch):
        monkeypatch.setattr(
            "llmesh.industrial.serial_adapter._PYSERIAL_AVAILABLE", False
        )
        with pytest.raises(RuntimeError, match="pyserial"):
            SerialAdapter("/dev/ttyUSB0")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_serial(monkeypatch):
    """Patch pyserial so tests run without hardware."""
    fake_serial_cls = MagicMock()
    fake_serial_instance = MagicMock()
    fake_serial_instance.is_open = True
    fake_serial_instance.close = MagicMock()
    fake_serial_cls.return_value = fake_serial_instance
    monkeypatch.setattr(
        "llmesh.industrial.serial_adapter._PYSERIAL_AVAILABLE", True
    )
    monkeypatch.setattr(
        "llmesh.industrial.serial_adapter.serial",
        MagicMock(Serial=fake_serial_cls),
    )
    return fake_serial_instance


def _make_line_reader(lines: list[bytes]):
    """Build a readline() mock that returns lines then blocks."""
    idx = 0
    lock = threading.Event()

    def readline():
        nonlocal idx
        if idx < len(lines):
            val = lines[idx]
            idx += 1
            return val
        lock.wait(timeout=0.1)
        return b""

    return readline


# ---------------------------------------------------------------------------
# Line mode
# ---------------------------------------------------------------------------

class TestLineModeEvents:
    def test_line_emits_event(self, mock_serial):
        mock_serial.readline = _make_line_reader([b"123.45\n"])
        adapter = SerialAdapter(
            "/dev/ttyUSB0",
            frame_mode="line",
            sensor_id="weight_01",
            sensor_type="weight",
            unit="g",
            device_id="scale_a",
        )
        events: list[SensorEvent] = []
        adapter.on_event(events.append)
        adapter.start()
        time.sleep(0.15)
        adapter.stop()

        assert len(events) >= 1
        ev = events[0]
        assert ev.sensor_id == "weight_01"
        assert ev.protocol == "serial"
        assert ev.sensor_type == "weight"
        assert ev.unit == "g"
        assert ev.device_id == "scale_a"
        assert ev.payload == b"123.45\n"
        assert ev.metadata["port"] == "/dev/ttyUSB0"

    def test_empty_line_not_emitted(self, mock_serial):
        mock_serial.readline = _make_line_reader([b"", b""])
        adapter = SerialAdapter("/dev/ttyUSB0", frame_mode="line", sensor_id="s")
        events: list[SensorEvent] = []
        adapter.on_event(events.append)
        adapter.start()
        time.sleep(0.15)
        adapter.stop()
        assert events == []

    def test_multiple_lines_emit_multiple_events(self, mock_serial):
        mock_serial.readline = _make_line_reader([b"1\n", b"2\n", b"3\n"])
        adapter = SerialAdapter("/dev/ttyUSB0", frame_mode="line", sensor_id="s")
        events: list[SensorEvent] = []
        adapter.on_event(events.append)
        adapter.start()
        time.sleep(0.2)
        adapter.stop()
        assert len(events) >= 3


# ---------------------------------------------------------------------------
# Fixed frame mode
# ---------------------------------------------------------------------------

class TestFixedModeEvents:
    def test_fixed_frame_emits_event(self, mock_serial):
        mock_serial.read = MagicMock(side_effect=[b"\x01\x02\x03\x04", b""])
        adapter = SerialAdapter(
            "/dev/ttyUSB0",
            frame_mode="fixed",
            frame_size=4,
            sensor_id="sensor_fixed",
        )
        events: list[SensorEvent] = []
        adapter.on_event(events.append)
        adapter.start()
        time.sleep(0.15)
        adapter.stop()

        assert len(events) >= 1
        assert events[0].payload == b"\x01\x02\x03\x04"


# ---------------------------------------------------------------------------
# Delimited mode
# ---------------------------------------------------------------------------

class TestDelimitedModeEvents:
    def test_delimited_frame(self, mock_serial):
        frame = b"HELLO\r"
        read_calls = [bytes([b]) for b in frame] + [b""]
        mock_serial.read = MagicMock(side_effect=read_calls * 5)

        adapter = SerialAdapter(
            "/dev/ttyUSB0",
            frame_mode="delimited",
            delimiter=0x0D,
            sensor_id="rs485_sensor",
        )
        events: list[SensorEvent] = []
        adapter.on_event(events.append)
        adapter.start()
        time.sleep(0.2)
        adapter.stop()

        assert len(events) >= 1
        assert events[0].payload == b"HELLO"


# ---------------------------------------------------------------------------
# Encoding metadata
# ---------------------------------------------------------------------------

class TestEncoding:
    def test_utf8_decoded_to_metadata(self, mock_serial):
        mock_serial.readline = _make_line_reader([b"25.3\n"])
        adapter = SerialAdapter(
            "/dev/ttyUSB0",
            frame_mode="line",
            sensor_id="temp",
            encoding="utf-8",
        )
        events: list[SensorEvent] = []
        adapter.on_event(events.append)
        adapter.start()
        time.sleep(0.15)
        adapter.stop()

        assert "text" in events[0].metadata
        assert events[0].metadata["text"] == "25.3"

    def test_decode_error_sets_flag(self, mock_serial):
        mock_serial.readline = _make_line_reader([b"\xff\xfe\n"])
        adapter = SerialAdapter(
            "/dev/ttyUSB0",
            frame_mode="line",
            sensor_id="bad_enc",
            encoding="utf-8",
        )
        events: list[SensorEvent] = []
        adapter.on_event(events.append)
        adapter.start()
        time.sleep(0.15)
        adapter.stop()

        if events:
            assert events[0].metadata.get("decode_error") is True


# ---------------------------------------------------------------------------
# Priority
# ---------------------------------------------------------------------------

class TestPriority:
    def test_critical_priority(self, mock_serial):
        mock_serial.readline = _make_line_reader([b"99\n"])
        adapter = SerialAdapter(
            "/dev/ttyUSB0",
            frame_mode="line",
            sensor_id="alarm",
            priority=Priority.CRITICAL,
        )
        events: list[SensorEvent] = []
        adapter.on_event(events.append)
        adapter.start()
        time.sleep(0.15)
        adapter.stop()
        assert events[0].priority is Priority.CRITICAL


# ---------------------------------------------------------------------------
# Callback isolation
# ---------------------------------------------------------------------------

class TestCallbackIsolation:
    def test_bad_callback_does_not_block_good_callback(self, mock_serial):
        mock_serial.readline = _make_line_reader([b"data\n"])
        adapter = SerialAdapter("/dev/ttyUSB0", frame_mode="line", sensor_id="s")
        good: list[SensorEvent] = []

        def bad(ev):
            raise RuntimeError("oops")

        adapter.on_event(bad)
        adapter.on_event(good.append)
        adapter.start()
        time.sleep(0.15)
        adapter.stop()
        assert len(good) >= 1


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

class TestLifecycle:
    def test_double_start_is_safe(self, mock_serial):
        mock_serial.readline = _make_line_reader([])
        adapter = SerialAdapter("/dev/ttyUSB0", frame_mode="line", sensor_id="s")
        adapter.start()
        adapter.start()  # second start should be no-op
        adapter.stop()

    def test_stop_without_start_is_safe(self, mock_serial):
        adapter = SerialAdapter("/dev/ttyUSB0", frame_mode="line", sensor_id="s")
        adapter.stop()  # must not raise

    def test_stop_closes_port(self, mock_serial):
        mock_serial.readline = _make_line_reader([])
        adapter = SerialAdapter("/dev/ttyUSB0", frame_mode="line", sensor_id="s")
        adapter.start()
        adapter.stop()
        mock_serial.close.assert_called_once()
