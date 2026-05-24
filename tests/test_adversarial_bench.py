"""敵対的ロバスト性 + 正確性 PoC の検証."""
from __future__ import annotations

from llmesh.speculative.adversarial_bench import (
    evaluate_poison,
    evaluate_rejection,
    poison_sweep,
)


def test_fail_closed_rejection():
    r = evaluate_rejection()
    assert r.forged_signature_rejected
    assert r.malformed_hex_rejected
    assert r.tampered_branch_rejected
    assert r.honest_accepted


def test_no_byzantine_no_poison():
    r = evaluate_poison(byzantine_fraction=0.0, seed=0)
    assert r.poison_accept_no_verify == 0.0
    assert r.correctness_no_verify == 1.0


def test_poison_accept_tracks_byzantine_without_verify():
    r = evaluate_poison(byzantine_fraction=0.5, seed=0)
    # 結果検証なしでは Byzantine 比率ぶん汚染が通る
    assert 0.45 < r.poison_accept_no_verify < 0.55
    assert r.correctness_no_verify < 0.6


def test_result_verification_recovers_correctness():
    r = evaluate_poison(byzantine_fraction=0.5, verify_catch_rate=0.9, seed=0)
    # 結果検証で汚染受容が大幅減 → 正答率回復
    assert r.poison_accept_with_verify < r.poison_accept_no_verify
    assert r.correctness_with_verify > 0.9


def test_poison_monotonic_in_byzantine_fraction():
    rows = poison_sweep(seed=0)
    poison = [r.poison_accept_no_verify for r in rows]  # 0, 0.1, 0.3, 0.5
    assert poison[0] < poison[1] < poison[2] < poison[3]
