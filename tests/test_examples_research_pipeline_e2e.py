"""Smoke test for examples/research_pipeline_e2e.py (Phase 19).

The demo script doubles as our most fragile integration test — it
imports from `core`, `research` and `vla` and exercises every D1-D7
public API. Running it under pytest with a redirected OUT_DIR keeps
us honest: if any phase's public signature drifts, this test catches
it before users hit a broken demo.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import examples.research_pipeline_e2e as demo


def test_e2e_demo_completes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out_dir = tmp_path / "research_e2e_demo"
    trace_path = out_dir / "trace.jsonl"
    paper_dir = out_dir / "paper"
    monkeypatch.setattr(demo, "OUT_DIR", out_dir)
    monkeypatch.setattr(demo, "TRACE_PATH", trace_path)
    monkeypatch.setattr(demo, "PAPER_DIR", paper_dir)

    demo.main()

    # Trace JSONL is non-empty and starts with run.start.
    assert trace_path.exists()
    lines = [
        json.loads(line)
        for line in trace_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert lines[0]["kind"] == "run.start"
    assert lines[-1]["kind"] == "run.end"

    # Paper bundle was rendered.
    assert (paper_dir / "paper_bundle.md").exists()
    assert (paper_dir / "runs.csv").exists()
    assert (paper_dir / "metrics.csv").exists()
    assert (paper_dir / "timing.svg").exists()


def test_e2e_demo_emits_cost_metrics(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out_dir = tmp_path / "research_e2e_demo"
    trace_path = out_dir / "trace.jsonl"
    monkeypatch.setattr(demo, "OUT_DIR", out_dir)
    monkeypatch.setattr(demo, "TRACE_PATH", trace_path)
    monkeypatch.setattr(demo, "PAPER_DIR", out_dir / "paper")

    demo.main()

    # At least one prompt entry should carry a cost_usd metric (D1 contract).
    lines = [
        json.loads(line)
        for line in trace_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    costed = [l for l in lines if l.get("metrics", {}).get("cost_usd", 0) > 0]
    assert costed, "D1 cost-aware trace should have at least one costed entry"


def test_e2e_demo_logs_attribution_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out_dir = tmp_path / "research_e2e_demo"
    trace_path = out_dir / "trace.jsonl"
    monkeypatch.setattr(demo, "OUT_DIR", out_dir)
    monkeypatch.setattr(demo, "TRACE_PATH", trace_path)
    monkeypatch.setattr(demo, "PAPER_DIR", out_dir / "paper")

    demo.main()

    lines = [
        json.loads(line)
        for line in trace_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    with_attr = [
        l for l in lines if l.get("extra", {}).get("attribution")
    ]
    assert with_attr, "at least one step should carry attribution links"
