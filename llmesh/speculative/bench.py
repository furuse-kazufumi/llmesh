"""Quantitative break-even PoC for Speculative Mesh Execution.

「使えそう」では本格導入しない — まず **baseline (local-only) vs speculative** を定量比較し、
効果がありそうな領域を実数で特定する PoC (FullSense 規約: 要件→PoC→フィジビリティ)。

本 PoC は **実 `SpeculativeMeshCoordinator` を駆動** (dispatch→submit_result→pull の
lifecycle を本物で回す) しつつ、その上に **レイテンシモデル**を重ねて end-to-end の
所要時間を baseline と比較する。ハードに依存しない**アルゴリズム寄り**の評価
(実 transport / 実 LLM executor は未配線なので、レイテンシは model パラメータで与える —
この前提は honest disclosure として明記する)。

## 損益モデル

1 分岐あたり:

- **baseline (local-only)**: `baseline_branch_ms = local_ms + swap_ms`
  (大型モデルは VRAM swap で memory-bound → `swap_ms` 増)
- **speculative**:
  - hit (予測的中 & 期限内 ready): `pull_ms + sign_ms` だけ (mesh から回収)
  - miss (予測外し / 間に合わず): `sign_ms + miss_penalty_ms + baseline_branch_ms`
    (= 投機を待った時間 `miss_penalty_ms` + 結局ローカル計算)。peer の `exec_ms` は無駄。

期待所要 = `h·pull + (1−h)·(baseline + miss_penalty)`。
**break-even hit_rate** `h* = miss_penalty / ((baseline − pull) + miss_penalty)`
(`pull ≥ baseline` の WAN では原則勝てない)。fast-fallback (`miss_penalty≈0`) なら
`h*≈0` = miss が latency-neutral なので**ほぼ常に勝つ** (ただし miss の `exec_ms` は
環境負荷として残る)。

    py -3.11 -m llmesh.speculative.bench
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from .coordinator import IdleNode, SpeculativeMeshCoordinator
from .manifest import SpeculativeManifest


@dataclass(frozen=True)
class LatencyModel:
    """1 分岐あたりのレイテンシ仮定 (ms)。実測前の PoC パラメータ。"""

    local_ms: float = 40.0          # ローカル計算 (compute-bound 部)
    swap_ms: float = 0.0            # memory-bound 時の VRAM swap 追加分
    net_one_way_ms: float = 0.5     # mesh 片道 (LAN≈0.5, WAN≈50)
    exec_ms: float = 35.0           # peer での投機実行時間 (環境負荷の単位)
    sign_ms: float = 0.05           # Ed25519 sign 1 発 (~50μs)
    miss_penalty_ms: float = 0.0    # miss 時に fallback 前に待つ時間 (timeout 等)

    @property
    def baseline_branch_ms(self) -> float:
        return self.local_ms + self.swap_ms

    @property
    def pull_ms(self) -> float:
        return 2.0 * self.net_one_way_ms  # 往復で結果回収

    def expected_spec_ms(self, hit_rate: float) -> float:
        h = float(hit_rate)
        hit_cost = self.pull_ms + self.sign_ms
        miss_cost = self.sign_ms + self.miss_penalty_ms + self.baseline_branch_ms
        return h * hit_cost + (1.0 - h) * miss_cost

    def analytical_speedup(self, hit_rate: float) -> float:
        return self.baseline_branch_ms / self.expected_spec_ms(hit_rate)

    def breakeven_hit_rate(self) -> float:
        """speculative が baseline を上回り始める hit_rate (sign 無視の近似)。

        `pull >= baseline` (WAN 等) なら勝てない → 1.0 を返す。
        """
        a = self.baseline_branch_ms - self.pull_ms
        if a <= 0.0:
            return 1.0
        denom = a + self.miss_penalty_ms
        h = self.miss_penalty_ms / denom
        return max(0.0, min(1.0, h))


@dataclass(frozen=True)
class SimResult:
    scenario: str
    hit_rate_target: float
    hit_rate_measured: float
    baseline_ms: float
    speculative_ms: float
    speedup: float
    analytical_speedup: float
    wasted_compute_ms: float
    used_compute_ms: float
    n_branches: int


def simulate(
    model: LatencyModel,
    *,
    hit_rate: float,
    n_branches: int = 2000,
    seed: int = 0,
    scenario: str = "",
) -> SimResult:
    """実 coordinator を駆動して baseline vs speculative を end-to-end 比較。"""
    from llmesh.identity.node_id import NodeIdentity

    ident = NodeIdentity.generate()
    coord = SpeculativeMeshCoordinator(ident, require_lan=True)
    idle = IdleNode("peer:executor", pending_tasks=0, cpu_load=0.1, vram_free_mb=8000.0, is_lan=True)
    rng = random.Random(seed)

    baseline_total = 0.0
    spec_total = 0.0
    for i in range(n_branches):
        manifest = SpeculativeManifest.new(
            origin_node_id=ident.node_id, branch={"branch": i}, created_at_ms=i
        )
        signed = coord.dispatch(manifest, [idle])
        assert signed is not None  # idle peer は常に居る前提の PoC
        baseline_total += model.baseline_branch_ms

        if rng.random() < hit_rate:
            # hit: peer が期限内に正しい結果を提出 → origin は pull で回収
            coord.submit_result(signed, {"result": i}, cost_ms=model.exec_ms)
            ok, _ = coord.pull(signed.manifest_hash)
            assert ok
            spec_total += model.pull_ms + model.sign_ms
        else:
            # miss: origin が到達した時点で未 ready → ローカル計算へ fallback
            ok, _ = coord.pull(signed.manifest_hash)
            assert not ok
            # peer の投機実行は遅れて完了 = 無駄 (環境負荷)
            coord.submit_result(signed, {"result": i}, cost_ms=model.exec_ms)
            spec_total += model.sign_ms + model.miss_penalty_ms + model.baseline_branch_ms

    disc = coord.disclosure()
    speedup = baseline_total / spec_total if spec_total > 0 else float("inf")
    return SimResult(
        scenario=scenario,
        hit_rate_target=hit_rate,
        hit_rate_measured=float(disc["hit_rate"]) if disc["hit_rate"] is not None else 0.0,
        baseline_ms=baseline_total,
        speculative_ms=spec_total,
        speedup=speedup,
        analytical_speedup=model.analytical_speedup(hit_rate),
        wasted_compute_ms=float(disc["wasted_compute_ms"]),
        used_compute_ms=float(disc["used_compute_ms"]),
        n_branches=n_branches,
    )


# PoC シナリオ: アルゴリズム寄りで損益分岐を見る代表点。
SCENARIOS: dict[str, LatencyModel] = {
    # LAN・即時 fallback: miss が latency-neutral → ほぼ常に勝つ (energy 浪費のみ)
    "LAN fast-fallback": LatencyModel(local_ms=40, net_one_way_ms=0.5, miss_penalty_ms=0.0),
    # LAN だが投機を待ってから fallback (timeout 20ms): break-even が上がる
    "LAN slow-fallback(20ms)": LatencyModel(local_ms=40, net_one_way_ms=0.5, miss_penalty_ms=20.0),
    # 大型モデル (swap-bound, baseline 240ms): pull が相対的に極小 → 投機が映える
    "big-model swap-bound": LatencyModel(local_ms=40, swap_ms=200, net_one_way_ms=0.5, miss_penalty_ms=0.0),
    # WAN: pull(100ms) > baseline(40ms) → 原則負ける (honest)
    "WAN": LatencyModel(local_ms=40, net_one_way_ms=50.0, miss_penalty_ms=0.0),
}

HIT_RATES = (0.3, 0.5, 0.7, 0.9)


def sweep(*, n_branches: int = 2000, seed: int = 0) -> list[SimResult]:
    rows: list[SimResult] = []
    for name, model in SCENARIOS.items():
        for h in HIT_RATES:
            rows.append(
                simulate(model, hit_rate=h, n_branches=n_branches, seed=seed, scenario=name)
            )
    return rows


def format_markdown(rows: list[SimResult]) -> str:
    lines = [
        "| scenario | hit_rate | speedup | baseline_ms | spec_ms | wasted_compute_ms | verdict |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        verdict = "win" if r.speedup > 1.0 else ("neutral" if abs(r.speedup - 1.0) < 1e-9 else "lose")
        lines.append(
            f"| {r.scenario} | {r.hit_rate_target:.1f} | {r.speedup:.2f}x | "
            f"{r.baseline_ms:.0f} | {r.speculative_ms:.0f} | {r.wasted_compute_ms:.0f} | {verdict} |"
        )
    return "\n".join(lines)


def _ensure_utf8_stdout() -> None:
    """Windows cp932 console で em-dash / 日本語を出力するための UTF-8 reconfigure."""
    import sys

    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:
        pass


def main() -> None:
    _ensure_utf8_stdout()
    rows = sweep()
    print("# Speculative Mesh Execution — 定量比較 PoC (simulation)\n")
    for name, model in SCENARIOS.items():
        print(
            f"- {name}: baseline={model.baseline_branch_ms:.0f}ms / pull={model.pull_ms:.1f}ms / "
            f"break-even hit_rate={model.breakeven_hit_rate():.2f}"
        )
    print()
    print(format_markdown(rows))


if __name__ == "__main__":
    main()
