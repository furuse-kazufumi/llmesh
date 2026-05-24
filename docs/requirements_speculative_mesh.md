# Speculative Mesh Execution — 要件定義 (本格導入に向けて)

> PoC (`llmesh/speculative/`, commit `2a05f64` + bench `b1d95e6`) で **定量比較** を実施し、
> **LAN かつ fast-fallback で効果あり** (大型 swap-bound モデルで最大 9.18x speedup,
> simulation) と判定。本書はその PoC 由来の **本格導入要件** をまとめる。
> 規約: 要件 → PoC → フィジビリティ → 詳細設計 ([[feedback_poc_feasibility_first]])。

## 1. PoC が示した導入条件 (定量根拠)

`docs/perf_comparison/speculative_mesh.md` の simulation より:

| 条件 | 根拠 | 要件への含意 |
|---|---|---|
| **LAN 限定** | WAN は全 hit_rate で lose (0.43–0.70x, pull>baseline) | `require_lan=True` を既定強制 |
| **fast-fallback 必須** | miss_penalty=20ms で break-even が 0→0.34 に上昇、低 hit_rate で lose | pull は**非ブロッキング**、miss は**即ローカル切替** |
| **大型/swap-bound で ROI 最大** | baseline 240ms ≫ pull 1ms → 最大 9.18x | 投機対象は重い分岐を優先 |
| **hit_rate が ROI を左右** | 0.3→1.39x, 0.9→7.77x。低 hit_rate は wasted_compute 大 | 予測器精度 + 環境負荷の honest 追跡 |

## 2. 要件

| ID | 要件 | 優先 | PoC 由来 |
|---|---|---|---|
| **SPEC-MESH-01** ✅ | 分岐予測器 (Phase 1): 最小 predictor 実装済 (`llive/src/llive/evolution/branch_predictor.py`: `FrequencyPredictor`=baseline / `MarkovPredictor`=order-1, top-K を opaque `branch` dict で生成 → `SpeculativeManifest.new(branch=...)`)。hit_rate 単体測定済 (合成系列, k=1): 構造ありで markov 0.87 (noisy.85) / 0.999 (cyclic) vs baseline 0.23–0.25、構造なしは baseline 同等 (sanity 通過)。実運用値は ChangeOp 実ログ待ち | 高 | 測定 doc: `llive/docs/perf_comparison/branch_predictor_hit_rate_2026_05_24.md` |
| **SPEC-MESH-02** ✅ (機構配線) | 実 mesh transport: `dispatch_fn` を llmesh discovery (`NodeRegistry`) で解決 → `MeshTransport` で署名 manifest を idle peer へ送る。`make_mesh_dispatch_fn(registry, transport)` (endpoint 解決 + best-effort・**例外を origin へ伝播しない**) + `HttpMeshTransport` (stdlib urllib, **背景スレッドプールで非ブロッキング**送信, 失敗は `send_errors` 計上のみ) を実装。残=FastAPI `/speculative/dispatch` route を node app に登録 (deployment glue) + 実マルチホスト run | 高 | PoC は dispatch_fn=record only |
| **SPEC-MESH-03** ✅ (機構配線) | peer 側 executor: `SpeculativeExecutor(identity, run_fn, allowed_origins=)` が受信 manifest を Ed25519 検証 (fail-closed・未検証は実行しない) → 実行 (例外は `exec_errors` 計上で握り潰し peer を落とさない) → 結果を `SignedResult` (= `(manifest_hash, result, cost_ms)` への peer 署名 = provenance) で署名して返す。origin 側 `ingest_result()` が結果署名検証 + manifest 束縛 + 任意の executor 同定を経て `submit_result` へ。残=実 run_fn (実 LLM/推論) 配線 + OS スケジューラ優先度連動 | 高 | PoC は submit_result を手動駆動 |
| **SPEC-MESH-04** ✅ | **fast-fallback (非ブロッキング pull)**: `coordinator.pull_or_compute(hash, local_fn)` = hit なら返す、miss なら**即** `local_fn()`。timeout 待ち / inflight join を一切しない。送信側も `HttpMeshTransport` が背景送信で origin をブロックしない。**最初から組込** (後付け不可要件を満たした) | **最高** | break-even を 0 付近に保つ唯一の条件 |
| **SPEC-MESH-05** | LAN 限定ゲート既定: `require_lan=True`。WAN 投入は明示 opt-in + `wan_dispatches` で別計上 | 高 | WAN は構造的に lose |
| **SPEC-MESH-06** | 投機対象の選別: コストの高い (swap-bound / 長 context) 分岐を優先投入。軽い分岐は投機しない | 中 | ROI は baseline/pull 比に比例 |
| **SPEC-MESH-07** | honest disclosure 実測: 配線後に baseline → 投機ありを実測し simulation 値を上書き。LAN/WAN 分離。`disclosure()` を時系列で `docs/perf_comparison/` に追記 | 高 | simulation は構造把握のみ |
| **SPEC-MESH-08** | 環境負荷ガード: `wasted_compute_ms` が閾値超過 (低 hit_rate 継続) で投機を自動抑制。予測器精度の floor を設ける | 中 | 低 hit_rate は電力浪費 |
| **SPEC-MESH-09** | Approval Bus 非迂回: 投機結果を確定タスクに昇格する際は通常の approval を通す。speculative=true は独立 verdict | 高 | fail-closed / 既存 PoC で署名検証は実装済 |
| **SPEC-MESH-10** | KV-cache 差分共有との結合余地: [[project_idea_kv_cache_memory_translator]] と署名スキーム共通化 (将来) | 低 | 別アイデア #2 と結合 |
| **SPEC-MESH-11** | **結果検証 (Byzantine 対策)**: Ed25519 署名は manifest 真正性のみ保証し**結果の正しさは保証しない**。投機結果を採用する前に安価な cross-check / 予測検証ゲート で poisoned result を捕捉する | **高** | 敵対的 PoC: 結果検証なしだと Byzantine 50% で正答率 51% に低下、検証(catch 0.9)で 95% 回復 |

## 3. 成功基準

- LAN 実機で **swap-bound 分岐** に対し hit_rate ≥ 0.5 / fast-fallback で **end-to-end speedup ≥ 1.5x** を実測。
- WAN では投機を発火させない (gate が効く)。
- `wasted_compute_ms` を監視し、hit_rate floor を下回ったら自動抑制が発火。
- すべての mesh 結果が Ed25519 検証を通過 (signature_rejections = 改ざん検出のみ)。
- Byzantine peer 混在下でも **結果検証で正答率 ≥ 95% を維持** (署名のみでは不足, SPEC-MESH-11)。

## 4. フィジビリティ上の主リスク (honest disclosure)

- **予測器精度 (SPEC-MESH-01)** が全体 ROI を支配。精度が低いと latency neutral でも電力浪費。
  → まず予測器単体の hit_rate を measure してから transport 配線へ。
- **fast-fallback の実装難度**: 非ブロッキング pull + 投機の遅延結果の安全な破棄。
- simulation の絶対値は仮定 — 実 transport の net 往復・executor 起動コストが pull_ms を
  押し上げると break-even が動く。実測必須。

## 5. 着手順 (PoC→本格導入)

1. ✅ SPEC-MESH-01 予測器の hit_rate 単体測定 (llive 側、最小 predictor) — **完了 (2026-05-24)**。
   構造のある系列で markov が baseline を大きく超える (cyclic 0.999 vs 0.25) ことを実証。
   構造なし系列では baseline 同等 = 過剰主張なし。**次の前提 = 実 ChangeOp 系列のログ化**で合成値を実測上書き。
2. 効果確認後 SPEC-MESH-02/03 transport+executor 配線 → SPEC-MESH-07 実測で simulation 上書き。
3. SPEC-MESH-04 fast-fallback を最初から組み込む (後付け不可の最高優先)。
4. SPEC-MESH-05/08 ゲート + 環境負荷ガード。

## Sources / 関連

- PoC: `llmesh/speculative/` (commit `2a05f64`) + `bench.py` (`b1d95e6`)
- 定量結果: `docs/perf_comparison/speculative_mesh.md`
- 上流アイデア: memory `project_idea_speculative_mesh_execution` / FullSense `research/gemini_brainstorm_impl_2026_05_24.md`
- 基盤: [[project_llmesh_p2p_winny]] (P2P mesh) / `llmesh/auth/signer.py` (Ed25519)
