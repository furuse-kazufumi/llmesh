"""Tests for the predictive-coding push coordinator."""
from __future__ import annotations

import datetime as _dt

from llmesh.industrial.spc_engine import CUSUMChart, SPCResult
from llmesh.llrepr import apply_patch
from llmesh.predictive_push import (
    InMemorySink,
    PredictivePush,
    Zone,
    classify_cusum_zone,
    classify_shewhart_zone,
    incident_to_llrepr,
)


def _fixed_clock():
    return _dt.datetime(2026, 5, 24, tzinfo=_dt.timezone.utc)


def _counter_ids():
    n = {"i": 0}

    def factory() -> str:
        n["i"] += 1
        return f"inc-{n['i']:04d}"

    return factory


def _pp(sink: InMemorySink) -> PredictivePush:
    return PredictivePush(
        CUSUMChart(target=2.0, k=0.5, h=5.0),
        sink=sink,
        sensor_id="S1",
        warn_frac=0.5,
        clock=_fixed_clock,
        incident_id_factory=_counter_ids(),
    )


# ---------------------------------------------------------------------------
# Zone classification
# ---------------------------------------------------------------------------

def test_classify_cusum_zone():
    alarm = SPCResult(in_control=False, value=8.0, ucl=5.0, lcl=0.0, extra={"s_plus": 6.0})
    warn = SPCResult(in_control=True, value=3.5, ucl=5.0, lcl=0.0, extra={"s_plus": 3.0})
    nom = SPCResult(in_control=True, value=2.1, ucl=5.0, lcl=0.0, extra={"s_plus": 1.0})
    assert classify_cusum_zone(alarm, h=5.0) is Zone.ALARM
    assert classify_cusum_zone(warn, h=5.0, warn_frac=0.5) is Zone.WARNING
    assert classify_cusum_zone(nom, h=5.0, warn_frac=0.5) is Zone.NOMINAL


def test_classify_shewhart_zone():
    base = dict(lcl=0.0, ucl=12.0, extra={})
    alarm = SPCResult(in_control=False, value=13.0, **base)
    warn = SPCResult(in_control=True, value=10.0, **base)
    nom = SPCResult(in_control=True, value=6.0, **base)
    assert classify_shewhart_zone(alarm, center=6.0) is Zone.ALARM
    assert classify_shewhart_zone(warn, center=6.0) is Zone.WARNING   # >2/3 of the way to UCL
    assert classify_shewhart_zone(nom, center=6.0) is Zone.NOMINAL


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------

def test_warning_triggers_single_speculation_no_push():
    sink = InMemorySink()
    pp = _pp(sink)
    pp.observe_many([2.0, 2.0, 3.0, 3.5, 3.5])  # drifts into warning
    assert pp.has_speculation is True
    assert pp.metrics.speculations_made == 1
    assert sink.frames == []  # nothing pushed yet (negative latency: pre-generated)


def test_warning_then_alarm_pushes_diff_and_reuses_incident_id():
    sink = InMemorySink()
    pp = _pp(sink)
    results = pp.observe_many([2.0, 2.0, 3.0, 3.5, 3.5, 4.0, 4.0])
    spec = next(r for r in results if r.speculated)
    alarm = next(r for r in results if r.zone is Zone.ALARM)
    assert alarm.frame is not None and alarm.frame.is_diff
    assert pp.metrics.diff_pushes == 1 and pp.metrics.speculations_used == 1
    # Same incident id for the speculation and its confirmation.
    assert alarm.frame.incident_id == spec.report.incident_id


def test_diff_push_round_trips_against_speculation():
    sink = InMemorySink()
    pp = _pp(sink)
    results = pp.observe_many([2.0, 2.0, 3.0, 3.5, 3.5, 4.0, 4.0])
    spec = next(r for r in results if r.speculated)
    alarm = next(r for r in results if r.zone is Zone.ALARM)
    # A consumer holding the speculative doc + the pushed diff reconstructs the confirmed doc.
    spec_doc = incident_to_llrepr(spec.report)
    actual_doc = incident_to_llrepr(alarm.report)
    assert apply_patch(spec_doc, alarm.frame.ops).to_dict() == actual_doc.to_dict()


def test_warning_recedes_to_nominal_discards_speculation():
    sink = InMemorySink()
    pp = _pp(sink)
    pp.observe_many([2.0, 3.5, 3.5, 3.5, 1.0, 1.0])  # into warning, then back down
    assert pp.has_speculation is False
    assert pp.metrics.speculations_made == 1
    assert pp.metrics.speculations_wasted == 1
    assert sink.frames == []  # never alarmed → nothing pushed


def test_cold_alarm_pushes_full_document():
    sink = InMemorySink()
    pp = _pp(sink)
    result = pp.observe(9.0)  # jumps straight past the decision interval
    assert result.zone is Zone.ALARM
    assert result.frame is not None and not result.frame.is_diff
    assert result.frame.document is not None
    assert pp.metrics.full_pushes == 1 and pp.metrics.diff_pushes == 0
