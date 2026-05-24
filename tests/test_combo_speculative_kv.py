"""Combo-B: Speculative Mesh x KV-cache 差分の synergy 検証."""
from __future__ import annotations

from llmesh.speculative.combo_bench import combo_b, sweep


def test_warm_reduces_exec():
    r = combo_b(diff_ratio=0.05)
    assert r.exec_warm_ms < r.exec_cold_ms
    assert r.wasted_ratio_warm_vs_cold < 1.0


def test_high_locality_raises_hit_and_speedup():
    r = combo_b(diff_ratio=0.05)
    assert r.hit_rate_warm > r.hit_rate_cold
    assert r.speedup_warm > r.speedup_cold
    assert r.wasted_ratio_warm_vs_cold < 0.5  # miss の無駄も半減以下


def test_low_locality_marginal_benefit():
    r = combo_b(diff_ratio=0.9)
    # 差分が大きい (locality 低) と暖機効果は小さい
    assert r.wasted_ratio_warm_vs_cold > 0.8
    assert r.speedup_warm >= r.speedup_cold  # 悪化はしない


def test_exec_monotonic_in_diff_ratio():
    rows = sweep()
    execs = [r.exec_warm_ms for r in rows]  # diff 0.05,0.2,0.5,0.9
    assert execs[0] < execs[1] < execs[2] < execs[3]


def test_warm_never_worse_than_cold():
    for r in sweep():
        assert r.speedup_warm >= r.speedup_cold
        assert r.hit_rate_warm >= r.hit_rate_cold
