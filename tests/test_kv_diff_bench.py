"""KV-cache mesh 差分共有 PoC: 差分転送 vs ローカル prefill の損益検証."""
from __future__ import annotations

from llmesh.speculative.kv_diff_bench import (
    DIFF_RATIOS,
    SCENARIOS,
    KvDiffModel,
    evaluate,
    sweep,
)


def test_transfer_grows_with_diff_ratio():
    m = SCENARIOS["LAN long-context"]
    assert m.transfer_ms(0.05) < m.transfer_ms(0.5) < m.transfer_ms(0.9)


def test_lan_wins_all_localities():
    m = SCENARIOS["LAN long-context"]
    for r in DIFF_RATIOS:
        assert m.speedup(r) > 1.0
    # 高 locality (小差分) ほど大きな speedup
    assert m.speedup(0.05) > m.speedup(0.9)


def test_wan_loses():
    m = SCENARIOS["WAN long-context"]
    for r in DIFF_RATIOS:
        assert m.speedup(r) < 1.0


def test_breakeven_lan_always_wins_wan_near_zero():
    assert SCENARIOS["LAN long-context"].breakeven_diff_ratio() == 1.0
    assert SCENARIOS["WAN long-context"].breakeven_diff_ratio() < 0.05


def test_evaluate_verdict():
    win = evaluate(SCENARIOS["LAN long-context"], 0.1, scenario="x")
    lose = evaluate(SCENARIOS["WAN long-context"], 0.1, scenario="y")
    assert win.verdict == "win" and win.speedup > 1.0
    assert lose.verdict == "lose" and lose.speedup < 1.0


def test_short_context_still_wins_on_lan():
    m = SCENARIOS["LAN short-context"]
    assert m.speedup(0.2) > 1.0


def test_sweep_shape():
    rows = sweep()
    assert len(rows) == len(SCENARIOS) * len(DIFF_RATIOS)


def test_zero_bandwidth_safety():
    # 極端に低帯域でも例外を出さず lose になる
    m = KvDiffModel(net_bw_mbps=0.1, prefill_cost_ms=300)
    assert m.speedup(0.5) < 1.0
