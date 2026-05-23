"""Runnable demo: predictive-coding push over a CUSUM chart.

    py -3.11 -m llmesh.predictive_push.demo

Three episodes show the full state machine:
  A) drift into the warning zone -> speculate -> alarm -> push a *typed diff*
  B) warning that recedes to nominal -> speculation discarded, nothing pushed
  C) cold alarm (no prior warning) -> push the *full* document

No LLM is wired in (template explainer), so it runs air-gapped and deterministically.
"""
from __future__ import annotations

import datetime as _dt
import json
import sys

from ..industrial.spc_engine import CUSUMChart
from .coordinator import PredictivePush
from .transport import InMemorySink


def _ensure_utf8_stdout() -> None:
    # Windows cp932 consoles choke on the arrows/box glyphs below.
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass


def _fixed_clock():
    return _dt.datetime(2026, 5, 24, 0, 0, 0, tzinfo=_dt.timezone.utc)


def _counter_ids():
    n = {"i": 0}

    def factory() -> str:
        n["i"] += 1
        return f"inc-{n['i']:04d}"

    return factory


def _new_pp(sink: InMemorySink) -> PredictivePush:
    return PredictivePush(
        CUSUMChart(target=2.0, k=0.5, h=5.0),
        sink=sink,
        sensor_id="S1",
        warn_frac=0.5,
        clock=_fixed_clock,
        incident_id_factory=_counter_ids(),
    )


def _run(label: str, values: list[float], sink: InMemorySink) -> PredictivePush:
    print(f"\n=== {label} ===")
    pp = _new_pp(sink)
    for v in values:
        r = pp.observe(v)
        tag = ""
        if r.speculated:
            tag = "  (speculated explanation ahead of alarm)"
        elif r.frame is not None:
            tag = f"  -> PUSH {r.frame.kind} (prediction_error={r.frame.prediction_error})"
        print(f"  value={v:>5}  zone={r.zone.value:<7}{tag}")
    m = pp.metrics
    print(f"  metrics: made={m.speculations_made} used={m.speculations_used} "
          f"wasted={m.speculations_wasted} diff_pushes={m.diff_pushes} full_pushes={m.full_pushes} "
          f"total_pred_err={m.total_prediction_error}")
    return pp


def main() -> None:
    _ensure_utf8_stdout()

    sink_a = InMemorySink()
    pp_a = _run("Episode A — drift -> warning -> alarm (diff push)",
                [2.0, 2.0, 3.0, 3.5, 3.5, 4.0, 4.0, 4.5], sink_a)

    sink_b = InMemorySink()
    _run("Episode B — warning recedes to nominal (speculation discarded)",
         [2.0, 3.5, 3.5, 3.5, 1.0, 1.0], sink_b)

    sink_c = InMemorySink()
    _run("Episode C — cold alarm, no warning (full push)",
         [9.0], sink_c)

    # The payoff: in Episode A the confirmed alarm travelled as a small diff
    # because the explanation was already generated at warning time.
    print("\n=== Episode A pushed frame (the prediction error on the wire) ===")
    diff_frame = next((f for f in sink_a.frames if f.is_diff), None)
    for f in sink_a.frames:
        print(f"  kind={f.kind}  incident={f.incident_id}  prediction_error={f.prediction_error}")
        if f.is_diff:
            print("  diff ops:")
            print("    " + json.dumps(f.ops, ensure_ascii=False, indent=2).replace("\n", "\n    "))

    print("\n=== Takeaway ===")
    print("  Primary win = NEGATIVE LATENCY: the explanation was generated at WARNING time,")
    print("  so when the alarm confirmed, only the prediction error (the diff) had to be computed —")
    print(f"  here {diff_frame.prediction_error if diff_frame else 0} op(s), not a fresh generation.")
    print("  Honest payload note: for a tiny document a 1-op diff (JSON pointer + value) is NOT")
    print("  smaller than the full doc. The payload saving scales with representation size —")
    print("  the diff stays ~constant (the error) while a full re-send grows with the document.")


if __name__ == "__main__":
    main()
