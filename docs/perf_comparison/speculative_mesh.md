# Speculative Mesh Execution — perf comparison (honest disclosure)

> 「思考リレー」: メイン推論中に予測した分岐を idle peer へ署名付きで投機投入し、
> 到達時に mesh から回収する。**得かどうかは hit rate と往復レイテンシ次第**であり、
> 主張する前に必ず内訳を疑う ([[feedback_benchmark_honest_disclosure]])。

実装: `llmesh/speculative/` (`SpeculativeMeshCoordinator` / `SpeculativeManifest`)。
本ファイルは **計測の方法論と記録様式**を定義する。実測値は run ごとに追記する
(空欄を埋める。**数字を捏造しない**)。

## 前提と損益分岐 (なぜ honest disclosure が必須か)

投機実行が **net positive になる条件**:

```
mesh 往復レイテンシ (dispatch + 実行 + pull) < ローカル VRAM swap レイテンシ
   かつ
hit rate が十分高い (外れた投機の compute は丸ごと無駄)
```

- **LAN** (μs–ms): 往復が swap より速い見込み → 勝てる。
- **WAN** (数十–数百 ms): swap (NVMe ms 級) に負ける見込み → **基本投入しない**
  (`require_lan=True` が既定。WAN 投入は `wan_dispatches` で別途計上)。
- Ed25519 sign ≈ 50μs/発 のオーバーヘッドは分岐数に比例。
- 投機外れ率が高いと mesh 全体の電力消費が増える → **環境負荷**として開示する。

## 記録様式 (`coordinator.disclosure()` の dict をそのまま時系列に積む)

| field | 意味 | 解釈 |
|---|---|---|
| `dispatched` | 投機投入した manifest 数 | 分母の一部 |
| `hits` | ready な結果を pull できた回数 | **唯一の「得」** |
| `misses` | pull 時に結果未達 → ローカル計算した回数 | 投機が遅すぎ/未投入 |
| `hit_rate` | `hits / (hits + misses)` | 損益分岐の主指標 |
| `wasted` | 未使用のまま破棄した投機数 | 分岐予測の空振り |
| `wasted_compute_ms` | 未使用に費やした executor 時間 | **環境負荷の主指標** |
| `used_compute_ms` | hit を生んだ executor 時間 | 有効活用分 |
| `wan_dispatches` | WAN peer への投入数 | 負け前提の投機 |
| `signature_rejections` | Ed25519 検証失敗で拒否した結果数 | mesh 改ざん/誤配 |
| `no_idle_node` | idle peer 不在で投入見送り | mesh 余剰の欠如 |

### 損益の読み方

- `hit_rate` が低い / `wasted_compute_ms` が `used_compute_ms` を大きく上回る
  → **投機は割に合っていない**。speedup を主張してはいけない。
- `wan_dispatches > 0` の run で速くなって見えても、LAN/WAN を分離するまで信用しない。

## 計測手順

1. `SpeculativeMeshCoordinator(origin_identity, require_lan=True)` を生成。
2. ベースライン (投機なし = 全 pull が miss 相当) のローカル実行時間を先に測る。
3. 投機ありで同一ワークロードを実行し、`disclosure()` を 1 run = 1 行で追記。
4. LAN と WAN を**必ず分けて**記録 (混在は `feedback_llive_measurement_purity` に反する)。

## 定量比較 PoC (simulation, 2026-05-24)

`py -3.11 -m llmesh.speculative.bench`。実 `SpeculativeMeshCoordinator` を駆動
(dispatch→submit_result→pull の lifecycle を本物で回す) しつつ、レイテンシは
`LatencyModel` パラメータで与える **アルゴリズム寄りシミュレーション**
(`tests/test_speculative_mesh_bench.py` で解析式と一致を検証, 11 ケース)。

> ⚠️ **これは simulation であり実測ハードウェアではない。** 実 transport / 実 LLM
> executor 未配線のため、レイテンシは仮定値。break-even の**構造**を見るためのもので、
> 絶対値は実配線後に上書きする。

**break-even hit_rate** (`h* = miss_penalty / ((baseline − pull) + miss_penalty)`):

| scenario | baseline_ms | pull_ms | break-even hit_rate |
|---|---|---|---|
| LAN fast-fallback | 40 | 1.0 | **0.00** (miss が latency-neutral) |
| LAN slow-fallback (20ms timeout) | 40 | 1.0 | **0.34** |
| big-model swap-bound | 240 | 1.0 | **0.00** |
| WAN | 40 | 100.0 | **1.00** (pull > baseline → 原則勝てない) |

**speedup (n=2000 分岐, seed=0)**:

| scenario | hit_rate | speedup | wasted_compute_ms | verdict |
|---|---|---|---|---|
| LAN fast-fallback | 0.3 / 0.5 / 0.7 / 0.9 | 1.39x / 1.94x / 3.10x / 7.77x | 49875→7350 | **win** |
| LAN slow-fallback(20ms) | 0.3 / 0.5 / 0.7 / 0.9 | 0.93x / 1.31x / 2.11x / 5.52x | 49875→7350 | 0.3 で **lose**, 以降 win |
| big-model swap-bound | 0.3 / 0.5 / 0.7 / 0.9 | 1.40x / 1.99x / 3.26x / **9.18x** | 49875→7350 | **win** |
| WAN | 0.3 / 0.5 / 0.7 / 0.9 | 0.70x / 0.57x / 0.49x / 0.43x | 49875→7350 | **lose** |

**読み取り (効果判定)**:

- ✅ **効果がありそうな領域**: **LAN** かつ (**fast-fallback** または **hit_rate > break-even**)。
  特に **大型 swap-bound モデル**で最大 9x (baseline≫pull のため)。
- miss は fast-fallback なら latency-neutral (= 投機の上振れはタダ) だが、**timeout 待ち
  (miss_penalty) が入ると break-even が上がり、低 hit_rate で負ける**。→ 実装は
  **fast-fallback (即時ローカル切替) が必須**。
- ❌ **WAN は全域で負け** (pull > baseline)。`require_lan=True` 既定の妥当性を定量裏付け。
- ⚠️ **環境負荷**: 低 hit_rate ほど `wasted_compute_ms` 大 (0.3 で 49,875ms = peer の空振り
  実行)。latency が neutral でも**電力は浪費**する。hit_rate を上げる予測器精度が ROI を決める。

## 実測ログ (実ハードウェア — 配線後)

| date | env (LAN/WAN) | workload | dispatched | hit_rate | wasted_compute_ms | baseline_ms | speculative_ms | verdict |
|---|---|---|---|---|---|---|---|---|
| _TBD_ | _TBD_ | _TBD_ | – | – | – | – | – | _未計測_ |

> simulation 段階。実 transport / 実 LLM executor 配線後に、上の simulation 値を
> 実測で上書きする (ベースライン → 投機ありの順、LAN/WAN 分離)。

## 既知の未配線 (honest disclosure)

- 分岐予測器 (Phase 1) は推論エンジン側 (llive MetaMutation 拡張) で別途実装。
  本 PoC は ready-made manifest を消費する。
- 実 mesh transport (`dispatch_fn`) と実 executor は stub。`InMemory` 相当で
  lifecycle のみ検証済 (`tests/test_speculative_mesh.py`, 23 ケース)。
- KV cache 差分共有 ([[project_idea_kv_cache_memory_translator]]) との結合は未着手。
