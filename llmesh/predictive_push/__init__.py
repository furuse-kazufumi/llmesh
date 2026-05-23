"""Predictive-coding push for industrial alarm explanation.

Generate the explanation *speculatively* when an SPC chart enters its warning
zone, then on confirmation push only the **typed diff** (the prediction error)
against the speculation — negative latency, minimal payload. Composes the existing
``llmesh.industrial`` SPC + explainer with the ``llmesh.llrepr`` typed-diff
primitive.

    from llmesh.predictive_push import PredictivePush
    from llmesh.industrial.spc_engine import CUSUMChart

    pp = PredictivePush(CUSUMChart(target=2.0, k=0.5, h=5.0), sensor_id="S1")
    for v in stream:
        result = pp.observe(v)
    print(pp.metrics)
"""
from __future__ import annotations

from .coordinator import ObserveResult, PredictiveMetrics, PredictivePush
from .report_repr import incident_to_llrepr
from .transport import InMemorySink, PushFrame, PushSink
from .zones import Zone, classify_cusum_zone, classify_shewhart_zone

__all__ = [
    "PredictivePush",
    "PredictiveMetrics",
    "ObserveResult",
    "Zone",
    "classify_cusum_zone",
    "classify_shewhart_zone",
    "PushSink",
    "InMemorySink",
    "PushFrame",
    "incident_to_llrepr",
]
