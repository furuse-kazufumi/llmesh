"""Tests for the Speculative Mesh quantitative break-even PoC.

実 coordinator 駆動シミュレーションが解析式と一致し、損益分岐 (LAN win / WAN lose /
fallback penalty) が想定どおり出ることを検証。
"""
from __future__ import annotations

from llmesh.speculative.bench import (
    HIT_RATES,
    SCENARIOS,
    LatencyModel,
    format_markdown,
    simulate,
    sweep,
)


def test_baseline_branch_and_pull():
    m = LatencyModel(local_ms=40, swap_ms=200, net_one_way_ms=0.5)
    assert m.baseline_branch_ms == 240.0
    assert m.pull_ms == 1.0


def test_breakeven_fast_fallback_is_zero():
    # miss_penalty=0 → miss は latency-neutral → break-even ≈ 0
    m = LatencyModel(local_ms=40, net_one_way_ms=0.5, miss_penalty_ms=0.0)
    assert m.breakeven_hit_rate() == 0.0


def test_breakeven_with_penalty_between_zero_and_one():
    m = LatencyModel(local_ms=40, net_one_way_ms=0.5, miss_penalty_ms=20.0)
    # a = 40 - 1 = 39; h* = 20 / (39 + 20) = 0.339
    h = m.breakeven_hit_rate()
    assert 0.0 < h < 1.0
    assert abs(h - 20.0 / 59.0) < 1e-9


def test_wan_never_wins_breakeven_one():
    # pull(100) > baseline(40) → 勝てない
    m = LatencyModel(local_ms=40, net_one_way_ms=50.0)
    assert m.pull_ms == 100.0
    assert m.breakeven_hit_rate() == 1.0


def test_simulation_matches_analytical():
    # 大 n + seed 固定で、測定 speedup が解析式に十分近い
    m = LatencyModel(local_ms=40, net_one_way_ms=0.5, miss_penalty_ms=0.0)
    r = simulate(m, hit_rate=0.7, n_branches=4000, seed=1)
    assert abs(r.speedup - r.analytical_speedup) / r.analytical_speedup < 0.05
    # 測定 hit_rate も target に近い
    assert abs(r.hit_rate_measured - 0.7) < 0.05


def test_lan_fast_fallback_wins_even_low_hit_rate():
    m = SCENARIOS["LAN fast-fallback"]
    r = simulate(m, hit_rate=0.3, n_branches=3000, seed=2)
    # fast-fallback では miss が neutral なので低 hit_rate でも win
    assert r.speedup > 1.0


def test_big_model_swap_bound_large_speedup():
    m = SCENARIOS["big-model swap-bound"]
    r = simulate(m, hit_rate=0.9, n_branches=2000, seed=3)
    # baseline 240ms / pull 1ms → 高 hit_rate で大きな speedup
    assert r.speedup > 3.0


def test_wan_loses():
    m = SCENARIOS["WAN"]
    r = simulate(m, hit_rate=0.7, n_branches=2000, seed=4)
    # pull(100) > baseline(40): hit でも遅い → 全体で負ける
    assert r.speedup < 1.0


def test_slow_fallback_below_breakeven_loses():
    m = SCENARIOS["LAN slow-fallback(20ms)"]
    be = m.breakeven_hit_rate()  # ~0.339
    low = simulate(m, hit_rate=0.1, n_branches=3000, seed=5)
    high = simulate(m, hit_rate=0.9, n_branches=3000, seed=6)
    assert low.hit_rate_target < be < high.hit_rate_target
    assert low.speedup < 1.0   # break-even 未満は負ける
    assert high.speedup > 1.0  # 超えれば勝つ


def test_wasted_compute_tracks_misses():
    m = LatencyModel(local_ms=40, net_one_way_ms=0.5, exec_ms=35.0)
    r = simulate(m, hit_rate=0.5, n_branches=1000, seed=7)
    # miss 数 ≈ 500、各 exec_ms=35 が wasted
    expected_misses = (1.0 - r.hit_rate_measured) * r.n_branches
    assert abs(r.wasted_compute_ms - expected_misses * 35.0) < 35.0 * 5  # 数件の誤差許容


def test_sweep_and_format():
    rows = sweep(n_branches=500, seed=0)
    assert len(rows) == len(SCENARIOS) * len(HIT_RATES)
    md = format_markdown(rows)
    assert "scenario" in md and "speedup" in md
    assert md.count("\n") >= len(rows)
