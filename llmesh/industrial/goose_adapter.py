"""GOOSEAdapter — v3-N7 IEC 61850 GOOSE (Generic Object Oriented Substation Event).

GOOSE is a layer-2 multicast protocol used in electrical substations
to publish protection / control events from intelligent electronic
devices (IEDs) to subscribers within sub-millisecond latency.

Status
------
**v2.14 skeleton.** GOOSE delivery on Ethernet requires raw socket
privileges (``CAP_NET_RAW`` on Linux), which is normally provided by a
native helper such as ``libiec61850`` or ``scapy``. Those are
intentionally **optional** here — the adapter accepts an injected
"transport" object exposing ``recv()`` so we can unit-test the parser /
SensorEvent normalisation independently from any kernel privilege.

Frame layout (subset)
---------------------
This module parses the *application-layer* PDU after the Ethernet +
GOOSE header. The relevant ASN.1 PDU fields used by the bridge:

- ``goCBRef``  — control-block reference, e.g. ``"IED1/LLN0$GO$gcb01"``
- ``datSet``   — referenced dataset name
- ``stNum``    — state counter (incremented on each event)
- ``sqNum``    — sequence number within a state
- ``dataset``  — sequence of typed values

We accept these fields as a Python dict (``GoosePDU``) so any decoder
(``libiec61850``, ``scapy.contrib.goose``, hand-rolled BER) can feed us.

Security invariants
-------------------
- ``allow_iedids`` whitelists which IED control blocks may emit events.
- ``MAX_DATASET_VALUES`` guards against unbounded payloads.
- All values are normalised into a single ``SensorEvent`` per dataset
  member, so downstream consumers do not parse ASN.1.
"""
from __future__ import annotations

import logging
import struct
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from .sensor_event import Priority, SensorEvent

logger = logging.getLogger(__name__)


MAX_DATASET_VALUES = 256


@dataclass(frozen=True)
class GoosePDU:
    """Subset of a GOOSE application PDU we care about."""

    go_cb_ref: str
    dat_set: str
    st_num: int
    sq_num: int
    dataset: tuple[Any, ...] = field(default_factory=tuple)


def _encode_value(value) -> bytes:
    """Encode a dataset member into ``SensorEvent.payload`` bytes."""
    if isinstance(value, bool):
        return b"\x01" if value else b"\x00"
    if isinstance(value, int):
        return struct.pack("<q", int(value))
    if isinstance(value, float):
        return struct.pack("<d", float(value))
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    return str(value).encode("utf-8")


def pdu_to_events(
    pdu: GoosePDU,
    *,
    device_id: str = "",
    timestamp_ns: int | None = None,
) -> list[SensorEvent]:
    """Fan a GOOSE PDU's dataset out into per-member ``SensorEvent``s.

    Each dataset member becomes one event with sensor_id
    ``"goose:<goCBRef>:<index>"``. The full PDU coordinates are
    preserved in metadata so downstream tools can reassemble the
    original message if needed.
    """
    if len(pdu.dataset) > MAX_DATASET_VALUES:
        raise ValueError(
            f"dataset has {len(pdu.dataset)} values; max is {MAX_DATASET_VALUES}"
        )
    events: list[SensorEvent] = []
    for idx, value in enumerate(pdu.dataset):
        events.append(SensorEvent.create(
            sensor_id=f"goose:{pdu.go_cb_ref}:{idx}",
            protocol="iec61850_goose",
            payload=_encode_value(value),
            priority=Priority.HIGH,   # GOOSE is for time-critical events
            device_id=device_id,
            sensor_type="goose_value",
            metadata={
                "go_cb_ref": pdu.go_cb_ref,
                "dat_set": pdu.dat_set,
                "st_num": int(pdu.st_num),
                "sq_num": int(pdu.sq_num),
                "index": idx,
            },
            timestamp_ns=timestamp_ns,
        ))
    return events


class GooseTransport:
    """Protocol-style hook for a real ``libiec61850`` / ``scapy`` driver.

    Implementations expose ``recv() -> GoosePDU | None`` (return None
    when no PDU is currently buffered).
    """

    def recv(self) -> GoosePDU | None:
        raise NotImplementedError


class GOOSEAdapter:
    """GOOSE subscriber (skeleton).

    Parameters
    ----------
    transport:
        A :class:`GooseTransport`-like object. Pass an injected fake in
        unit tests; production deployments wire in libiec61850 or
        scapy-based receivers.
    allow_iedids:
        Whitelist of accepted ``goCBRef`` strings (e.g. ``["IED1/LLN0$GO$gcb01"]``).
        ``None`` accepts any IED — strongly discouraged in production.
    device_id:
        Forwarded to every emitted ``SensorEvent``.

    Thread safety
    -------------
    **Not thread-safe.** ``step`` / ``drain`` mutate the per-``goCBRef``
    ``_last_st_num`` counter without synchronisation, so concurrent
    invocations from multiple threads can let a replayed PDU through.
    Run one ``GOOSEAdapter`` per worker, or wrap calls in a
    :class:`threading.Lock` on the caller side.
    """

    def __init__(
        self,
        transport: GooseTransport | None = None,
        *,
        allow_iedids: Iterable[str] | None = None,
        device_id: str = "",
    ) -> None:
        self._transport = transport
        self._allow = set(allow_iedids) if allow_iedids is not None else None
        self._device_id = device_id
        self._callbacks: list[Callable[[SensorEvent], None]] = []
        self._last_st_num: dict[str, int] = {}

    @property
    def device_id(self) -> str:
        return self._device_id

    def on_event(self, callback: Callable[[SensorEvent], None]) -> None:
        self._callbacks.append(callback)

    # ------------------------------------------------------------------
    # Reception
    # ------------------------------------------------------------------

    def step(self) -> list[SensorEvent]:
        """Pull one PDU from the transport (if any) and normalise.

        Skipped without raising when:

        - transport is not wired in,
        - the source ``goCBRef`` is not in ``allow_iedids``,
        - the ``stNum`` has gone backwards (replay protection).
        """
        if self._transport is None:
            return []
        pdu = self._transport.recv()
        if pdu is None:
            return []
        if not self._allowed(pdu.go_cb_ref):
            return []
        if not self._fresh(pdu):
            return []
        events = pdu_to_events(pdu, device_id=self._device_id)
        for ev in events:
            for cb in self._callbacks:
                try:
                    cb(ev)
                except Exception as exc:
                    logger.warning(
                        "GOOSEAdapter callback error: %s", exc, exc_info=True,
                    )
        return events

    def drain(self, max_steps: int = 1024) -> list[SensorEvent]:
        """Pull PDUs until the transport returns ``None`` or the cap is reached."""
        if max_steps <= 0:
            raise ValueError("max_steps must be positive")
        out: list[SensorEvent] = []
        for _ in range(max_steps):
            batch = self.step()
            if not batch:
                break
            out.extend(batch)
        return out

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _allowed(self, ref: str) -> bool:
        if self._allow is None:
            return True
        return ref in self._allow

    def _fresh(self, pdu: GoosePDU) -> bool:
        last = self._last_st_num.get(pdu.go_cb_ref)
        if last is not None and pdu.st_num < last:
            return False
        self._last_st_num[pdu.go_cb_ref] = max(last or 0, int(pdu.st_num))
        return True
