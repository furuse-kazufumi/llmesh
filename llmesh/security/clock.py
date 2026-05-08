"""NTP clock-sync enforcement for replay-window accuracy.

Queries NTP servers at node start-up and raises ClockDriftError if the
local clock is too far off.  Skewing the clock breaks the nonce replay
window, so an inaccurate clock is treated as a fatal start-up error.

Environment variables:
  LLMESH_NTP_SERVERS       — comma-separated NTP servers
                             (default: pool.ntp.org,time.cloudflare.com)
  LLMESH_MAX_CLOCK_DRIFT_S — max tolerable drift in seconds (default: 10)
  LLMESH_NTP_TIMEOUT_S     — per-server query timeout in seconds (default: 5)

Dependencies: ntplib>=0.4  (pip install llmesh[mgmt])
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_DEFAULT_NTP_SERVERS = "pool.ntp.org,time.cloudflare.com"
_DEFAULT_MAX_DRIFT_S = 10.0
_DEFAULT_TIMEOUT_S = 5.0


class ClockDriftError(RuntimeError):
    """Raised when the local clock is too far off from NTP time."""

    def __init__(self, drift: float, max_drift: float) -> None:
        super().__init__(
            f"Local clock drift {drift:.2f}s exceeds threshold {max_drift:.2f}s — "
            "synchronise your system clock and retry."
        )
        self.drift = drift
        self.max_drift = max_drift


def check_clock_sync(
    *,
    servers: list[str] | None = None,
    max_drift_s: float | None = None,
    timeout_s: float | None = None,
) -> float:
    """Check NTP clock sync and return measured drift in seconds.

    Args:
        servers:      NTP server hostnames. Falls back to LLMESH_NTP_SERVERS.
        max_drift_s:  Maximum tolerable drift. Falls back to LLMESH_MAX_CLOCK_DRIFT_S.
        timeout_s:    Per-server query timeout. Falls back to LLMESH_NTP_TIMEOUT_S.

    Returns:
        Absolute drift in seconds (float).

    Raises:
        ClockDriftError: If drift exceeds max_drift_s.
        RuntimeError:    If all NTP servers are unreachable.
        ImportError:     If ntplib is not installed.
    """
    try:
        import ntplib  # noqa: PLC0415
    except ImportError as exc:
        raise ImportError("ntplib is required: pip install llmesh[mgmt]") from exc

    if servers is None:
        servers = os.environ.get("LLMESH_NTP_SERVERS", _DEFAULT_NTP_SERVERS).split(",")
    if max_drift_s is None:
        max_drift_s = float(
            os.environ.get("LLMESH_MAX_CLOCK_DRIFT_S", str(_DEFAULT_MAX_DRIFT_S))
        )
    if timeout_s is None:
        timeout_s = float(
            os.environ.get("LLMESH_NTP_TIMEOUT_S", str(_DEFAULT_TIMEOUT_S))
        )

    client = ntplib.NTPClient()
    last_exc: Exception | None = None

    for server in servers:
        server = server.strip()
        if not server:
            continue
        try:
            response = client.request(server, timeout=timeout_s)
            drift = abs(response.offset)
            logger.debug("NTP: server=%s offset=%.3fs", server, response.offset)
            if drift > max_drift_s:
                raise ClockDriftError(drift, max_drift_s)
            logger.info(
                "NTP: clock sync OK — drift=%.3fs (max %.1fs)", drift, max_drift_s
            )
            return drift
        except ClockDriftError:
            raise
        except Exception as exc:
            logger.warning("NTP: failed to query %s: %s", server, exc)
            last_exc = exc

    raise RuntimeError(
        f"NTP: all servers unreachable {servers}: {last_exc}"
    )
