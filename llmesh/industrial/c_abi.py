"""c_abi — decode SensorEvent v1 wire format from RTOS / embedded clients.

Counterpart to ``c_bindings/llmesh_event.h``.  Allows the LLMesh
gateway running on Linux/Windows/macOS to receive events produced by
TRON / Zephyr / FreeRTOS / VxWorks / QNX / NuttX / Mbed OS / AUTOSAR
etc. via TCP / UDP / Serial.

Wire format (header is 48 bytes, packed, little-endian)::

    uint32 magic          = 0x4C4D4553 ("LMES")
    uint16 version        = 1
    uint16 protocol_id
    uint64 timestamp_ns
    uint32 sensor_id_len
    uint32 device_id_len
    uint32 sensor_type_len
    uint32 unit_len
    uint32 payload_len
    uint8  priority
    uint8  reserved[7]
    sensor_id_bytes        (utf-8)
    device_id_bytes        (utf-8)
    sensor_type_bytes      (utf-8)
    unit_bytes             (utf-8)
    payload_bytes          (raw)

Security invariants
-------------------
- Strict length caps mirror the C header (defends against malformed
  packets attempting buffer over-reads).
- All multi-byte integers parsed via ``struct`` little-endian.
- Magic + version checked before any allocation.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

from llmesh.industrial.sensor_event import Priority, SensorEvent

# ---------------------------------------------------------------------------
# Module constants — must match c_bindings/llmesh_event.h
# ---------------------------------------------------------------------------

_MAGIC = 0x4C4D4553                # "LMES"
_VERSION = 1
_HEADER_SIZE = 44                  # 4+2+2+8+4*5+1+7 = 44 (packed)
_HEADER_FMT = "<IHHQIIIIIB7s"      # 44 bytes total, packed

# Max field sizes (bytes) — match the C header.
_MAX_SENSOR_ID = 128
_MAX_DEVICE_ID = 128
_MAX_SENSOR_TYPE = 64
_MAX_UNIT = 16
_MAX_PAYLOAD = 65_536

# Protocol ID → name (must match C enum llmesh_protocol_t).
_PROTOCOL_BY_ID: dict[int, str] = {
    0: "unknown",   1: "modbus",   2: "opcua",    3: "mqtt",
    4: "ethercat",  5: "can",      6: "bacnet",   7: "aoi",
    8: "depth",     9: "dvs",     10: "serial",  11: "hart",
    12: "dnp3",    13: "tron",    14: "zephyr",
}

_PRIORITY_BY_ID: dict[int, Priority] = {
    0: Priority.NORMAL,
    1: Priority.HIGH,
    2: Priority.CRITICAL,
}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class CABIError(ValueError):
    """Raised when a packed event fails validation."""


# ---------------------------------------------------------------------------
# Decoder
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CEventHeader:
    protocol_id: int
    protocol: str
    timestamp_ns: int
    sensor_id_len: int
    device_id_len: int
    sensor_type_len: int
    unit_len: int
    payload_len: int
    priority: Priority


def parse_header(data: bytes) -> CEventHeader:
    """Validate and parse the 48-byte header.  Raises CABIError on failure."""
    if len(data) < _HEADER_SIZE:
        raise CABIError(
            f"buffer too short for header: {len(data)} < {_HEADER_SIZE}"
        )
    (magic, version, proto_id, ts_ns,
     sid_len, did_len, st_len, unit_len, pl_len,
     priority_raw, _reserved) = struct.unpack_from(_HEADER_FMT, data, 0)

    if magic != _MAGIC:
        raise CABIError(f"bad magic: 0x{magic:08X}")
    if version != _VERSION:
        raise CABIError(f"unsupported version: {version}")

    if sid_len > _MAX_SENSOR_ID:
        raise CABIError(f"sensor_id_len too large: {sid_len}")
    if did_len > _MAX_DEVICE_ID:
        raise CABIError(f"device_id_len too large: {did_len}")
    if st_len > _MAX_SENSOR_TYPE:
        raise CABIError(f"sensor_type_len too large: {st_len}")
    if unit_len > _MAX_UNIT:
        raise CABIError(f"unit_len too large: {unit_len}")
    if pl_len > _MAX_PAYLOAD:
        raise CABIError(f"payload_len too large: {pl_len}")

    priority = _PRIORITY_BY_ID.get(priority_raw, Priority.NORMAL)
    protocol = _PROTOCOL_BY_ID.get(proto_id, "unknown")

    return CEventHeader(
        protocol_id=proto_id,
        protocol=protocol,
        timestamp_ns=ts_ns,
        sensor_id_len=sid_len,
        device_id_len=did_len,
        sensor_type_len=st_len,
        unit_len=unit_len,
        payload_len=pl_len,
        priority=priority,
    )


def decode(data: bytes) -> SensorEvent:
    """Decode a complete C ABI packed event into a SensorEvent."""
    h = parse_header(data)
    expected = (_HEADER_SIZE + h.sensor_id_len + h.device_id_len
                + h.sensor_type_len + h.unit_len + h.payload_len)
    if len(data) < expected:
        raise CABIError(
            f"truncated body: have {len(data)} bytes, expected {expected}"
        )

    off = _HEADER_SIZE
    sensor_id = data[off:off + h.sensor_id_len].decode("utf-8", "replace")
    off += h.sensor_id_len
    device_id = data[off:off + h.device_id_len].decode("utf-8", "replace")
    off += h.device_id_len
    sensor_type = data[off:off + h.sensor_type_len].decode("utf-8", "replace")
    off += h.sensor_type_len
    unit = data[off:off + h.unit_len].decode("utf-8", "replace")
    off += h.unit_len
    payload = bytes(data[off:off + h.payload_len])

    return SensorEvent(
        sensor_id=sensor_id,
        protocol=h.protocol,
        timestamp_ns=h.timestamp_ns,
        payload=payload,
        priority=h.priority,
        device_id=device_id,
        sensor_type=sensor_type,
        unit=unit,
        metadata={"c_abi_version": _VERSION},
    )


# ---------------------------------------------------------------------------
# Encoder (mirror of C side, useful for tests / debugging)
# ---------------------------------------------------------------------------

def encode(event: SensorEvent) -> bytes:
    """Serialize a SensorEvent into the C ABI v1 wire format."""
    sid = event.sensor_id.encode("utf-8")
    did = event.device_id.encode("utf-8")
    st = event.sensor_type.encode("utf-8")
    unit = event.unit.encode("utf-8")
    payload = bytes(event.payload)

    if len(sid) > _MAX_SENSOR_ID:
        raise CABIError(f"sensor_id too long: {len(sid)}")
    if len(did) > _MAX_DEVICE_ID:
        raise CABIError(f"device_id too long: {len(did)}")
    if len(st) > _MAX_SENSOR_TYPE:
        raise CABIError(f"sensor_type too long: {len(st)}")
    if len(unit) > _MAX_UNIT:
        raise CABIError(f"unit too long: {len(unit)}")
    if len(payload) > _MAX_PAYLOAD:
        raise CABIError(f"payload too long: {len(payload)}")

    proto_id = next(
        (k for k, v in _PROTOCOL_BY_ID.items() if v == event.protocol),
        0,
    )
    priority_id = next(
        (k for k, v in _PRIORITY_BY_ID.items() if v is event.priority),
        0,
    )

    header = struct.pack(
        _HEADER_FMT,
        _MAGIC, _VERSION, proto_id, event.timestamp_ns,
        len(sid), len(did), len(st), len(unit), len(payload),
        priority_id, b"\x00" * 7,
    )
    return header + sid + did + st + unit + payload
