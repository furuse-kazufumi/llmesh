"""Render an industrial ``IncidentReport`` as an llrepr Document.

The explanation must become a *typed representation* so the predictive-coding
push can diff a speculative report against the confirmed one and send only the
difference. The shape is kept **stable** across speculative and confirmed reports
(same node skeleton, varying leaf text) so the diff is a handful of clean
``replace`` ops — i.e. the prediction error, nothing more.

The ``incident_id`` is intentionally a stable leaf: the coordinator reuses one id
for the speculation and its confirmation, so id churn never inflates the diff.
"""
from __future__ import annotations

from ..industrial.explainer import IncidentReport
from ..llrepr import Container, Document, Heading, Text


def incident_to_llrepr(report: IncidentReport) -> Document:
    """Build a stable-shaped llrepr document from an incident report."""
    return Document.of(
        Heading(level=2, children=[Text(text=f"Incident {report.incident_id}")]),
        Container(tag="block", children=[Text(text=report.severity)]),
        Container(tag="block", children=[Text(text=report.cause)]),
        Container(tag="block", children=[Text(text=report.suggestion)]),
    )
