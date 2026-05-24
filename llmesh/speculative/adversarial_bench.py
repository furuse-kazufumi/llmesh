"""敵対的ロバスト性 + 正確性 評価 PoC (Speculative Mesh).

速度だけでなく **mesh の安全性 (fail-closed)** と **正確性** を評価する。

脅威モデル: Ed25519 署名は **manifest の origin 真正性**を守る (改ざん/誤配は拒否) が、
**結果の正しさは保証しない** — 投機を実行する peer は origin 署名済 manifest を正しく
echo しつつ、**poisoned result** を返せる (Byzantine peer)。

本 PoC が測るもの:

1. **malformed/forged 拒否** (実 `SpeculativeMeshCoordinator`): 壊れた署名・他人の鍵での
   署名は fail-closed で拒否されるか (signature_rejections)。
2. **poisoned result 受容率**: Byzantine peer 比率 ``f`` のとき、**結果検証なし**では汚染が
   そのまま通る。**安価な結果検証** (predictive ゲート / cross-check, 捕捉率 ``v``) を入れると
   汚染受容がどれだけ下がるか。
3. **正確性 (correctness)**: honest peer のみなら 100%、Byzantine 混在で低下、検証で回復。

    py -3.11 -m llmesh.speculative.adversarial_bench
"""
from __future__ import annotations

import dataclasses
import random
from dataclasses import dataclass

from llmesh.identity.node_id import NodeIdentity
from llmesh.speculative.coordinator import IdleNode, SpeculativeMeshCoordinator
from llmesh.speculative.manifest import SpeculativeManifest


# ---------------------------------------------------------------------------
# 1. malformed / forged manifest の fail-closed 拒否 (実 coordinator)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RejectionResult:
    forged_signature_rejected: bool
    malformed_hex_rejected: bool
    tampered_branch_rejected: bool
    honest_accepted: bool


def evaluate_rejection() -> RejectionResult:
    """改ざん/偽造 manifest が fail-closed で拒否され、正規は通ることを実機で確認."""
    origin = NodeIdentity.generate()
    attacker = NodeIdentity.generate()
    coord = SpeculativeMeshCoordinator(origin, require_lan=True)
    idle = [IdleNode("peer:x", pending_tasks=0, vram_free_mb=8000.0)]

    # 正規
    m1 = SpeculativeManifest.new(origin_node_id=origin.node_id, branch={"i": 1})
    s1 = coord.dispatch(m1, idle)
    assert s1 is not None
    honest_ok = coord.submit_result(s1, {"r": 1}, cost_ms=10.0)

    # 偽造: attacker の pubkey に差し替え
    m2 = SpeculativeManifest.new(origin_node_id=origin.node_id, branch={"i": 2})
    s2 = coord.dispatch(m2, idle)
    assert s2 is not None
    forged = dataclasses.replace(s2, origin_pub_hex=attacker.public_key_hex)
    forged_rejected = not coord.submit_result(forged, {"r": "evil"}, cost_ms=10.0)

    # malformed hex
    m3 = SpeculativeManifest.new(origin_node_id=origin.node_id, branch={"i": 3})
    s3 = coord.dispatch(m3, idle)
    assert s3 is not None
    malformed = dataclasses.replace(s3, signature_hex="zzzz")
    malformed_rejected = not coord.submit_result(malformed, {"r": "evil"}, cost_ms=10.0)

    # tampered branch (署名後に payload 改変)
    m4 = SpeculativeManifest.new(origin_node_id=origin.node_id, branch={"i": 4})
    s4 = coord.dispatch(m4, idle)
    assert s4 is not None
    tampered_m = dataclasses.replace(s4.manifest, branch={"i": "evil"})
    tampered = dataclasses.replace(s4, manifest=tampered_m)
    tampered_rejected = not coord.submit_result(tampered, {"r": "evil"}, cost_ms=10.0)

    return RejectionResult(forged_rejected, malformed_rejected, tampered_rejected, honest_ok)


# ---------------------------------------------------------------------------
# 2/3. poisoned result の受容率 + 正確性 (Byzantine peer 比率 × 結果検証)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PoisonResult:
    byzantine_fraction: float
    verify_catch_rate: float
    poison_accept_no_verify: float   # 結果検証なしの汚染受容率
    poison_accept_with_verify: float  # 結果検証ありの汚染受容率
    correctness_no_verify: float      # 検証なしの正答率
    correctness_with_verify: float    # 検証ありの正答率


def evaluate_poison(
    *, byzantine_fraction: float, verify_catch_rate: float = 0.9,
    n: int = 4000, seed: int = 0,
) -> PoisonResult:
    """Byzantine peer が署名済 manifest に poisoned result を返す。結果検証の効果を測る."""
    rng = random.Random(seed)
    poisoned = 0
    accept_no_verify = 0
    accept_with_verify = 0
    for _ in range(n):
        is_byzantine = rng.random() < byzantine_fraction
        if not is_byzantine:
            continue  # honest peer は正しい結果 → 汚染なし
        poisoned += 1
        # 署名は通る (origin 署名を echo) → 結果検証なしでは受容
        accept_no_verify += 1
        # 結果検証 (cross-check / gate): catch_rate で捕捉、すり抜けたら受容
        if rng.random() >= verify_catch_rate:
            accept_with_verify += 1

    poison_no = accept_no_verify / n
    poison_with = accept_with_verify / n
    return PoisonResult(
        byzantine_fraction=byzantine_fraction,
        verify_catch_rate=verify_catch_rate,
        poison_accept_no_verify=poison_no,
        poison_accept_with_verify=poison_with,
        correctness_no_verify=1.0 - poison_no,
        correctness_with_verify=1.0 - poison_with,
    )


BYZANTINE_FRACTIONS = (0.0, 0.1, 0.3, 0.5)


def poison_sweep(*, verify_catch_rate: float = 0.9, seed: int = 0) -> list[PoisonResult]:
    return [
        evaluate_poison(byzantine_fraction=f, verify_catch_rate=verify_catch_rate, seed=seed)
        for f in BYZANTINE_FRACTIONS
    ]


def _ensure_utf8_stdout() -> None:
    import sys

    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:
        pass


def main() -> None:
    _ensure_utf8_stdout()
    print("# 敵対的ロバスト性 + 正確性 — Speculative Mesh\n")
    rej = evaluate_rejection()
    print("## 1. fail-closed 拒否 (実 coordinator)")
    print(f"- forged signature 拒否: {rej.forged_signature_rejected}")
    print(f"- malformed hex 拒否: {rej.malformed_hex_rejected}")
    print(f"- tampered branch 拒否: {rej.tampered_branch_rejected}")
    print(f"- 正規 result 受容: {rej.honest_accepted}")
    print("\n## 2/3. poisoned result 受容率 + 正確性 (結果検証 catch_rate=0.9)\n")
    print("| Byzantine率 | 汚染受容(検証なし) | 汚染受容(検証あり) | 正答率(検証なし→あり) |")
    print("|---|---|---|---|")
    for r in poison_sweep():
        print(
            f"| {r.byzantine_fraction:.0%} | {r.poison_accept_no_verify:.1%} | "
            f"{r.poison_accept_with_verify:.1%} | "
            f"{r.correctness_no_verify:.0%}→{r.correctness_with_verify:.0%} |"
        )
    print("\n→ 署名は manifest 真正性を守るが結果の正しさは別。Byzantine には**結果検証**が必須。")


if __name__ == "__main__":
    main()
