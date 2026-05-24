"""組み合わせ PoC (llmesh) — Speculative Mesh x KV-cache 差分 (Combo-B).

単体 PoC を合成: **KV-cache 差分共有** ([[project_idea_kv_cache_memory_translator]]) で
peer の prefix が暖機されると、投機実行の `exec_ms` が縮む。これは Speculative Mesh
([[project_idea_speculative_mesh_execution]]) に二重に効く:

1. **実効 hit_rate 上昇**: exec が lead time 内に終わる確率が上がる (間に合う投機が増える)。
2. **wasted_compute 減**: miss 時に peer が捨てる計算 (`exec_ms`) が小さくなる。

つまり KV 差分共有は「投機が間に合いやすくなり、外しても安い」方向に働く。locality
(差分率) が高いほど暖機効果が大きい。

    py -3.11 -m llmesh.speculative.combo_bench
"""
from __future__ import annotations

from dataclasses import dataclass

from .bench import LatencyModel


@dataclass(frozen=True)
class ComboBResult:
    diff_ratio: float
    exec_cold_ms: float
    exec_warm_ms: float
    hit_rate_cold: float
    hit_rate_warm: float
    speedup_cold: float
    speedup_warm: float
    wasted_ratio_warm_vs_cold: float  # warm の miss あたり無駄計算 / cold (小さいほど良い)


def combo_b(
    *,
    diff_ratio: float,
    predictor_accuracy: float = 0.8,
    lead_time_ms: float = 30.0,
    base_exec_ms: float = 40.0,
    prefill_fraction: float = 0.7,
    lat: LatencyModel | None = None,
) -> ComboBResult:
    """KV 差分暖機 (cold vs warm) が speculative の hit_rate / speedup に効く量を測る."""
    lat = lat or LatencyModel(local_ms=40.0, net_one_way_ms=0.5, miss_penalty_ms=0.0)

    exec_cold = base_exec_ms
    # KV 差分共有: prefix の prefill 部 (prefill_fraction) のうち、共有できる部分
    # (1 - diff_ratio) を暖機でスキップ。locality 高 (差分小) ほど exec が縮む。
    exec_warm = base_exec_ms * (1.0 - prefill_fraction * (1.0 - diff_ratio))

    # lead time 内に終わる割合 (間に合えば hit 候補)。exec > lead なら部分的にしか間に合わない。
    ready_cold = min(1.0, lead_time_ms / exec_cold)
    ready_warm = min(1.0, lead_time_ms / exec_warm)
    hit_cold = predictor_accuracy * ready_cold
    hit_warm = predictor_accuracy * ready_warm

    return ComboBResult(
        diff_ratio=diff_ratio,
        exec_cold_ms=exec_cold,
        exec_warm_ms=exec_warm,
        hit_rate_cold=hit_cold,
        hit_rate_warm=hit_warm,
        speedup_cold=lat.analytical_speedup(hit_cold),
        speedup_warm=lat.analytical_speedup(hit_warm),
        wasted_ratio_warm_vs_cold=exec_warm / exec_cold,
    )


DIFF_RATIOS = (0.05, 0.2, 0.5, 0.9)


def sweep(**kw) -> list[ComboBResult]:
    return [combo_b(diff_ratio=r, **kw) for r in DIFF_RATIOS]


def _ensure_utf8_stdout() -> None:
    import sys

    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:
        pass


def main() -> None:
    _ensure_utf8_stdout()
    print("# Combo-B: Speculative Mesh x KV-cache 差分 (暖機で投機を間に合わせる)\n")
    print("base_exec=40ms / lead_time=30ms / predictor_acc=0.8\n")
    print("| 差分率 | exec cold→warm | hit_rate cold→warm | speedup cold→warm | miss 無駄 (warm/cold) |")
    print("|---|---|---|---|---|")
    for r in sweep():
        print(
            f"| {r.diff_ratio:.2f} | {r.exec_cold_ms:.0f}→{r.exec_warm_ms:.1f}ms | "
            f"{r.hit_rate_cold:.2f}→{r.hit_rate_warm:.2f} | "
            f"{r.speedup_cold:.2f}x→{r.speedup_warm:.2f}x | {r.wasted_ratio_warm_vs_cold:.2f} |"
        )


if __name__ == "__main__":
    main()
