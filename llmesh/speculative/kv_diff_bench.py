"""KV-cache mesh 差分共有 定量比較 PoC (Gemini ブレスト #2).

[[project_idea_kv_cache_memory_translator]]: mesh 間で **KV cache 差分** を共有し、
「同じ memory 状態」を全 node が持てるようにする。本 PoC は「prefix を**ローカル再計算
(prefill)**する」vs「peer から **KV 差分を取得**する」のコストを定量比較し、損益分岐を
**差分率 (locality)** と **帯域 (LAN/WAN)** で見る。Ed25519 署名スキームは
`llmesh.speculative` (思考リレー) と共通化できる前提。

モデル (アルゴリズム寄り simulation):

* `prefill_cost_ms`: 長 context の prefix を**ローカルで再計算**するコスト (baseline)。
* KV 差分取得: `2·net_one_way + diff_mb/bw + apply + sign`。
  `diff_mb = kv_size_mb · diff_ratio` (locality 高 = 差分小 = 共有が得)。
* **勝つ条件**: 差分転送 < prefill。小さい diff_ratio + 高帯域 (LAN) + 高い prefill コスト
  (長 context) で大きく勝つ。WAN / 低 locality では負ける。

    py -3.11 -m llmesh.speculative.kv_diff_bench
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class KvDiffModel:
    kv_size_mb: float = 200.0          # full KV cache (長 context)
    prefill_cost_ms: float = 300.0     # prefix をローカル再計算 (baseline)
    net_one_way_ms: float = 0.5        # mesh 片道 (LAN≈0.5, WAN≈50)
    net_bw_mbps: float = 1250.0        # 実効帯域 MB/s (LAN 10Gbps≈1250, WAN 100Mbps≈12.5)
    apply_cost_per_mb_ms: float = 0.1  # 受信差分を適用するコスト
    sign_ms: float = 0.05              # Ed25519 sign/verify

    def transfer_ms(self, diff_ratio: float) -> float:
        diff_mb = self.kv_size_mb * float(diff_ratio)
        net = 2.0 * self.net_one_way_ms
        xfer = diff_mb / self.net_bw_mbps * 1000.0
        apply = diff_mb * self.apply_cost_per_mb_ms
        return net + xfer + apply + self.sign_ms

    def speedup(self, diff_ratio: float) -> float:
        return self.prefill_cost_ms / self.transfer_ms(diff_ratio)

    def breakeven_diff_ratio(self) -> float:
        """transfer_ms(r) == prefill_cost_ms となる diff_ratio (二分探索)。"""
        lo, hi = 0.0, 1.0
        if self.transfer_ms(0.0) >= self.prefill_cost_ms:
            return 0.0  # 差分ゼロでも転送が prefill 以上 (WAN 等) → 勝てない
        if self.transfer_ms(1.0) <= self.prefill_cost_ms:
            return 1.0  # full 転送でも prefill 以下 → 常に勝つ
        for _ in range(60):
            mid = (lo + hi) / 2.0
            if self.transfer_ms(mid) < self.prefill_cost_ms:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2.0


@dataclass(frozen=True)
class KvDiffResult:
    scenario: str
    diff_ratio: float
    transfer_ms: float
    prefill_ms: float
    speedup: float
    verdict: str


SCENARIOS: dict[str, KvDiffModel] = {
    "LAN long-context": KvDiffModel(kv_size_mb=200, prefill_cost_ms=300, net_one_way_ms=0.5, net_bw_mbps=1250.0),
    "LAN short-context": KvDiffModel(kv_size_mb=20, prefill_cost_ms=30, net_one_way_ms=0.5, net_bw_mbps=1250.0),
    "WAN long-context": KvDiffModel(kv_size_mb=200, prefill_cost_ms=300, net_one_way_ms=50.0, net_bw_mbps=12.5),
}
DIFF_RATIOS = (0.05, 0.2, 0.5, 0.9)


def evaluate(model: KvDiffModel, diff_ratio: float, *, scenario: str = "") -> KvDiffResult:
    sp = model.speedup(diff_ratio)
    return KvDiffResult(
        scenario=scenario,
        diff_ratio=diff_ratio,
        transfer_ms=model.transfer_ms(diff_ratio),
        prefill_ms=model.prefill_cost_ms,
        speedup=sp,
        verdict="win" if sp > 1.0 else "lose",
    )


def sweep() -> list[KvDiffResult]:
    rows: list[KvDiffResult] = []
    for name, model in SCENARIOS.items():
        for r in DIFF_RATIOS:
            rows.append(evaluate(model, r, scenario=name))
    return rows


def _ensure_utf8_stdout() -> None:
    import sys

    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:
        pass


def main() -> None:
    _ensure_utf8_stdout()
    print("# KV-cache mesh 差分共有 — 定量比較 PoC (vs ローカル prefill)\n")
    for name, model in SCENARIOS.items():
        print(f"- {name}: prefill={model.prefill_cost_ms:.0f}ms / break-even diff_ratio={model.breakeven_diff_ratio():.2f}")
    print()
    print("| scenario | diff_ratio | transfer_ms | prefill_ms | speedup | verdict |")
    print("|---|---|---|---|---|---|")
    for r in sweep():
        print(
            f"| {r.scenario} | {r.diff_ratio:.2f} | {r.transfer_ms:.1f} | "
            f"{r.prefill_ms:.0f} | {r.speedup:.2f}x | {r.verdict} |"
        )


if __name__ == "__main__":
    main()
