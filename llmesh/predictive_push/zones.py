"""SPC zone classification — the trigger for speculative pre-generation.

Predictive-coding push reacts not to the alarm itself but to the **warning zone**
that precedes it: the band where the process is still "in control" but trending
toward a violation. Entering the warning zone is the signal to *speculatively*
generate the explanation, so it is ready the instant the alarm confirms.

- Shewhart charts (Xbar-R): the warning zone is the 2σ–3σ band. With control
  limits at 3σ from the centre line, the 2σ warning limit sits ``warn_frac`` of
  the way out (default 2/3).
- CUSUM: the warning zone is when the cumulative statistic reaches ``warn_frac``
  of the decision interval ``h`` (default 0.5) but has not yet crossed it.
"""
from __future__ import annotations

from enum import Enum

from ..industrial.spc_engine import SPCResult


class Zone(str, Enum):
    """Where an observation sits relative to the control limits."""

    NOMINAL = "nominal"   # comfortably in control
    WARNING = "warning"   # in control but trending out → speculate
    ALARM = "alarm"       # control-limit violation → confirm


def classify_shewhart_zone(result: SPCResult, center: float, warn_frac: float = 2 / 3) -> Zone:
    """Classify a Shewhart (Xbar-R) observation into a zone.

    ``center`` is the centre line (e.g. ``XbarRChart.x_bar_bar``). The warning
    limits sit at ``center ± warn_frac × (UCL − center)``.
    """
    if not result.in_control:
        return Zone.ALARM
    warn_upper = center + warn_frac * (result.ucl - center)
    warn_lower = center - warn_frac * (center - result.lcl)
    if result.value >= warn_upper or result.value <= warn_lower:
        return Zone.WARNING
    return Zone.NOMINAL


def classify_cusum_zone(result: SPCResult, h: float, warn_frac: float = 0.5) -> Zone:
    """Classify a CUSUM observation into a zone using ``h`` (decision interval)."""
    if not result.in_control:
        return Zone.ALARM
    s_plus = float(result.extra.get("s_plus", 0.0)) if result.extra else 0.0
    s_minus = float(result.extra.get("s_minus", 0.0)) if result.extra else 0.0
    statistic = max(s_plus, s_minus)
    if h > 0 and statistic >= warn_frac * h:
        return Zone.WARNING
    return Zone.NOMINAL
