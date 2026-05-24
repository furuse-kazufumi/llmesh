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

## 実測ログ

| date | env (LAN/WAN) | workload | dispatched | hit_rate | wasted_compute_ms | baseline_ms | speculative_ms | verdict |
|---|---|---|---|---|---|---|---|---|
| _TBD_ | _TBD_ | _TBD_ | – | – | – | – | – | _未計測_ |

> まだ実測なし。PoC 着地段階 (mesh transport / 実 LLM executor 未配線)。
> 配線後にベースライン → 投機ありの順で測り、上表を埋める。

## 既知の未配線 (honest disclosure)

- 分岐予測器 (Phase 1) は推論エンジン側 (llive MetaMutation 拡張) で別途実装。
  本 PoC は ready-made manifest を消費する。
- 実 mesh transport (`dispatch_fn`) と実 executor は stub。`InMemory` 相当で
  lifecycle のみ検証済 (`tests/test_speculative_mesh.py`, 23 ケース)。
- KV cache 差分共有 ([[project_idea_kv_cache_memory_translator]]) との結合は未着手。
