"""IndustrialMetrics — lightweight Prometheus-compatible metrics (v3 preview).

A pure-stdlib metrics collector designed for the Industrial Phase
adapters.  Counters and gauges can be exported in Prometheus text format
(``text/plain; version=0.0.4``) so they can be scraped by any Prometheus-
compatible monitoring stack — no `prometheus_client` dependency required.

Usage::

    from llmesh.industrial.metrics import IndustrialMetrics

    metrics = IndustrialMetrics()
    metrics.increment("modbus_events_total", labels={"device": "smt01"})
    metrics.set_gauge("modbus_connected", 1, labels={"device": "smt01"})

    text = metrics.render()      # Prometheus text format
    print(text)
    # # HELP modbus_events_total auto-generated
    # # TYPE modbus_events_total counter
    # modbus_events_total{device="smt01"} 1

    # Optional HTTP scrape endpoint
    await metrics.serve_http("0.0.0.0", 9000)

Security invariants
-------------------
- No shell=True, eval, exec, pickle, subprocess.
- Label values are escaped (Prometheus exposition rules).
- Metric names are validated against `[a-zA-Z_:][a-zA-Z0-9_:]*`.
- HTTP endpoint is read-only (GET /metrics only); other paths return 404.
"""
from __future__ import annotations

import asyncio
import logging
import re
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

# Prometheus metric-name allowed character set (data model spec § Metric names).
_METRIC_NAME_RE = re.compile(r"^[a-zA-Z_:][a-zA-Z_0-9:]*$")

# Prometheus label-name allowed character set.
_LABEL_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z_0-9]*$")

# Maximum number of distinct (name, label-set) series we keep in memory.
# Defends against label-cardinality explosion.
_MAX_SERIES = 100_000

# Per-series timeseries name buffer cap (used by histograms when added later).
_MAX_OBSERVATIONS_PER_SERIES = 1024

# HTTP response served by /metrics on a successful scrape.
_PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


def _validate_metric_name(name: str) -> None:
    if not _METRIC_NAME_RE.match(name):
        raise ValueError(
            f"invalid metric name {name!r}; must match {_METRIC_NAME_RE.pattern}"
        )


def _validate_labels(labels: Mapping[str, str] | None) -> None:
    if not labels:
        return
    for k in labels:
        if not _LABEL_NAME_RE.match(k):
            raise ValueError(
                f"invalid label name {k!r}; must match {_LABEL_NAME_RE.pattern}"
            )


def _escape_label_value(v: str) -> str:
    """Escape ``\\``, ``"``, and newlines per Prometheus exposition rules."""
    return v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _series_key(name: str, labels: Mapping[str, str] | None) -> tuple[str, tuple[tuple[str, str], ...]]:
    if not labels:
        return (name, ())
    return (name, tuple(sorted((k, str(v)) for k, v in labels.items())))


# ---------------------------------------------------------------------------
# Metric storage
# ---------------------------------------------------------------------------

@dataclass
class _Series:
    """One time-series — counter cumulative or gauge instantaneous value."""

    name: str
    labels: tuple[tuple[str, str], ...]
    kind: str        # "counter" | "gauge"
    value: float = 0.0
    help_text: str = "auto-generated"


# ---------------------------------------------------------------------------
# IndustrialMetrics — public API
# ---------------------------------------------------------------------------

class IndustrialMetrics:
    """Thread-safe Prometheus-compatible counter / gauge registry."""

    def __init__(self) -> None:
        self._series: dict[tuple, _Series] = {}
        self._lock = threading.RLock()
        self._http_server: Any = None
        self._http_task: asyncio.Task | None = None  # type: ignore[type-arg]

    # ------------------------------------------------------------------
    # Counter
    # ------------------------------------------------------------------

    def increment(
        self,
        name: str,
        amount: float = 1.0,
        *,
        labels: Mapping[str, str] | None = None,
        help_text: str | None = None,
    ) -> None:
        """Add *amount* to the counter (name, labels)."""
        _validate_metric_name(name)
        _validate_labels(labels)
        if amount < 0:
            raise ValueError("counter increment must be non-negative")
        key = _series_key(name, labels)
        with self._lock:
            self._capacity_check()
            s = self._series.get(key)
            if s is None:
                s = _Series(name=name, labels=key[1], kind="counter",
                            help_text=help_text or "auto-generated")
                self._series[key] = s
            elif s.kind != "counter":
                raise ValueError(
                    f"metric {name!r} already registered as {s.kind!r}"
                )
            s.value += amount

    # ------------------------------------------------------------------
    # Gauge
    # ------------------------------------------------------------------

    def set_gauge(
        self,
        name: str,
        value: float,
        *,
        labels: Mapping[str, str] | None = None,
        help_text: str | None = None,
    ) -> None:
        """Set the gauge (name, labels) to *value*."""
        _validate_metric_name(name)
        _validate_labels(labels)
        key = _series_key(name, labels)
        with self._lock:
            self._capacity_check()
            s = self._series.get(key)
            if s is None:
                s = _Series(name=name, labels=key[1], kind="gauge",
                            help_text=help_text or "auto-generated")
                self._series[key] = s
            elif s.kind != "gauge":
                raise ValueError(
                    f"metric {name!r} already registered as {s.kind!r}"
                )
            s.value = float(value)

    # ------------------------------------------------------------------
    # Read access
    # ------------------------------------------------------------------

    def get(self, name: str, labels: Mapping[str, str] | None = None) -> float | None:
        with self._lock:
            s = self._series.get(_series_key(name, labels))
            return s.value if s is not None else None

    def reset(self) -> None:
        with self._lock:
            self._series.clear()

    # ------------------------------------------------------------------
    # Prometheus text rendering
    # ------------------------------------------------------------------

    def render(self) -> str:
        """Render all series in Prometheus text exposition format (v0.0.4)."""
        with self._lock:
            # Group series by metric name to emit HELP/TYPE only once.
            by_name: dict[str, list[_Series]] = {}
            for s in self._series.values():
                by_name.setdefault(s.name, []).append(s)

            lines: list[str] = []
            for name in sorted(by_name):
                series_list = by_name[name]
                first = series_list[0]
                lines.append(f"# HELP {name} {first.help_text}")
                lines.append(f"# TYPE {name} {first.kind}")
                for s in series_list:
                    lines.append(self._format_line(s))
            return "\n".join(lines) + ("\n" if lines else "")

    def _format_line(self, s: _Series) -> str:
        if not s.labels:
            return f"{s.name} {self._format_value(s.value)}"
        labels = ",".join(
            f'{k}="{_escape_label_value(v)}"' for k, v in s.labels
        )
        return f"{s.name}{{{labels}}} {self._format_value(s.value)}"

    @staticmethod
    def _format_value(v: float) -> str:
        # Prometheus prefers integer-valued floats without a decimal point
        if v != v:           # NaN
            return "NaN"
        if v == float("inf"):
            return "+Inf"
        if v == float("-inf"):
            return "-Inf"
        if v.is_integer():
            return str(int(v))
        return repr(v)

    # ------------------------------------------------------------------
    # Capacity guard
    # ------------------------------------------------------------------

    def _capacity_check(self) -> None:
        if len(self._series) >= _MAX_SERIES:
            raise RuntimeError(
                f"IndustrialMetrics: series cardinality limit "
                f"({_MAX_SERIES}) reached — refusing new series to prevent "
                f"memory exhaustion. Reduce label cardinality."
            )

    # ------------------------------------------------------------------
    # Optional HTTP scrape endpoint
    # ------------------------------------------------------------------

    async def serve_http(self, host: str = "127.0.0.1", port: int = 9000) -> None:
        """Start a minimal asyncio HTTP server exposing /metrics."""
        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                request_line = await asyncio.wait_for(reader.readline(), timeout=2.0)
                if not request_line:
                    return
                # Drain the rest of the headers (no body expected for GET)
                while True:
                    line = await asyncio.wait_for(reader.readline(), timeout=2.0)
                    if line == b"\r\n" or not line:
                        break
                parts = request_line.split()
                if len(parts) >= 2 and parts[0] == b"GET" and parts[1].startswith(b"/metrics"):
                    body = self.render().encode("utf-8")
                    response = (
                        b"HTTP/1.1 200 OK\r\n"
                        b"Content-Type: " + _PROMETHEUS_CONTENT_TYPE.encode() + b"\r\n"
                        b"Content-Length: " + str(len(body)).encode() + b"\r\n"
                        b"Connection: close\r\n\r\n" + body
                    )
                else:
                    response = (
                        b"HTTP/1.1 404 Not Found\r\n"
                        b"Content-Length: 0\r\n"
                        b"Connection: close\r\n\r\n"
                    )
                writer.write(response)
                await writer.drain()
            except asyncio.TimeoutError:
                pass
            except Exception as exc:
                logger.debug("IndustrialMetrics HTTP handler error: %s", exc)
            finally:
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
                    pass

        self._http_server = await asyncio.start_server(handle, host=host, port=port)
        logger.info("IndustrialMetrics HTTP server listening on %s:%d/metrics", host, port)

    async def stop_http(self) -> None:
        if self._http_server is not None:
            self._http_server.close()
            await self._http_server.wait_closed()
            self._http_server = None
