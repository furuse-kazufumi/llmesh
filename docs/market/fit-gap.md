# llmesh 機能 fit gap 分析 (Day 5)

> 2026-05-18 作成 (Day 5 前倒し). Day 4 で絞り込んだ 3 領域 (EAR-clean / 中国 LLM /
> 規制対応 + HITL) に対し、llmesh 現機能 (v3.1.0) の fit / gap を [[feedback-
> implementation-status-record]] の 4 段階 status taxonomy で評価.

## 1. 4 段階 status taxonomy (再確認)

- **未実装**: コードが存在しない
- **未配線**: コードは存在するが、main path から呼ばれていない
- **部分実装**: 一部機能のみ、要件の全部を満たさない
- **実装済**: 要件をすべて満たし、テストも pass している

## 2. 領域 1: EAR-clean 検証可能性

| 機能 | 状態 | コード位置 | 残作業 |
|---|---|---|---|
| 依存 origin DB (`origins.toml`) | **未実装** | (なし) | 内蔵 DB を新規作成、初期 100-200 packages |
| `llmesh deps --audit` CLI | **未実装** | (なし) | CLI parser + 表形式出力 (Week 2 Day 1-3) |
| supply_risk DB | **未実装** | (なし) | litellm 2026/03 等の incident DB (Week 2 Day 4) |
| 静的 API call 検出 | **未実装** | (なし) | AST 解析で urllib/httpx/requests 検出 (Week 2 Day 5) |
| 動的 API call 検出 (`--runtime-check`) | **未実装** | (なし) | Phase 2 (firewall 統合) |
| JSON / HTML / SBOM 出力 | **未実装** | (なし) | Day 6 で simple version |
| 多言語 report (ja/en/zh) | **未実装** | (なし) | Week 4 |
| ユーザ overrides | **未実装** | (なし) | Phase 2 |

**fit**: 0%（完全に new build）
**gap 解消**: Week 2 Day 1-7 で α 実装、Week 4 で v3.2.0-rc1 に統合

## 3. 領域 2: 中国 LLM ファーストクラス + 国産 silicon

| 機能 | 状態 | コード位置 | 残作業 |
|---|---|---|---|
| Qwen API 統合 | **部分実装** | llmesh/llm/qwen* (推定) | API 差異正規化、最新 model 対応 |
| DeepSeek API 統合 | **部分実装** | (推定) | 同上 |
| GLM API 統合 | **部分実装** | (推定) | 同上 |
| Kimi K2.5 統合 | **未実装** | (なし) | 2026 新規モデル対応 |
| Baichuan 統合 | **未実装** | (なし) | 中堅優先度 |
| API 差異正規化 (統一 schema) | **未配線** | (一部) | 中国 LLM 専用 normalizer 整備 |
| `[cn-llm]` extras | **未実装** | (なし) | pyproject extras 定義 + dependency 整理 |
| MindSpore 経由 Ascend 対応 | **未実装** | (なし) | vLLM-MindSpore Plugin 活用 (Week 3+) |
| PaddlePaddle 経由 Cambricon 対応 | **未実装** | (なし) | PaddleCustomDevice 活用 (Phase 2) |
| `[cn-silicon]` extras | **未実装** | (なし) | pyproject extras 定義 |
| Brief を Qwen で E2E 実行 | **未配線** | (llmesh ↔ llive 単体は ✓) | F25 Phase h E2E (Week 2) |
| 中国 mirror (gitee) 同期 | **未実装** | (なし) | Week 4 (CI 自動同期) |

**fit**: ~30%（基本的な API 統合のみ、ファーストクラス扱いではない）
**gap 解消**: Week 2-3 で `[cn-llm]` 整備、Week 4 で gitee mirror

## 4. 領域 3: 規制対応 + HITL architecture-level 統合

### llive 側 (Brief 経路)

| 機能 | 状態 | 出典 |
|---|---|---|
| HITL Approval Bus | **実装済** (Brief API 経由) | [[project-llive-brief-api-done]] |
| SQLite Ledger | **実装済** (Brief API 経由) | 同上 |
| OKA-FX 出典追跡 | **部分実装** | docs/REQUIREMENTS.md MATH-08 grounding 配線完了 (5/17) |
| 4 層メモリ | **実装済** | llive C-1 完了 |
| 思考因子 (10 因子) | **実装済** | llive COG-FX |
| Annotation Channel (IND-04) | **実装済** | llive C-1 |
| Brief API | **実装済 (5/16)** | LLIVE-002 resolved |
| `_inner_monologue` の LLM 配線 | **実装済 (5/16)** | LLIVE-001 resolved |

### llmesh 側

| 機能 | 状態 | 残作業 |
|---|---|---|
| 監査ログ stream | **部分実装** | extras `[compliance]` 整備 |
| PII redaction (presidio extras) | **部分実装** | extras あるが docs 不足 |
| 出典 channel (RAG citation) | **部分実装** | 強化必要 |
| 規制対応 docs (5 本) | **未実装** | Week 4 で整備、cn-internal-use.md は完成 |

### 規制対応 docs 整備状況

| docs | 状態 |
|---|---|
| `cn-internal-use.md` (中国社内利用 filing 免除) | **✓ Implemented (本日 commit 74cfb3c)** ja のみ |
| `cn-public-service.md` (公衆向け filing 手順) | **未実装** (低優先) |
| `eu-ai-act.md` (EU AI Act 対応) | **未実装** (Week 4) |
| `data-sovereignty.md` (各国データ越境) | **未実装** (Week 4) |
| `audit-log-format.md` (監査ログ仕様) | **未実装** (Week 4) |
| 全 docs の多言語化 (ja/en/zh) | 1/5 で ja のみ | (Week 4) |

**fit**: ~50%（llive 側はかなり実装済、llmesh 側と docs が gap）
**gap 解消**: Week 1-4 で並行整備、Phase 2 で完全化

## 5. 領域以外の機能の優先度評価

戦略思索 PART 6 で Core / Extras 分割を提案. 既存機能の優先度を 4 段階で:

| 機能 | 現状態 | 優先度 | 判断 |
|---|---|---|---|
| LLM routing (汎用) | 実装済 | 中 | Core に残す |
| MCP server | 実装済 | 高 | Core に残す (差別化軸) |
| SPC (統計的工程管理) | 実装済 | 中 | `[industrial]` extras |
| MQTT / OPC-UA | 実装済 | 中 | `[industrial]` extras |
| Phase 3.7 skill chunk replication | 実装済 | **低** | `[mesh]` extras に隔離 |
| Phase 3.6 PeerReputation | 実装済 | **低** | `[mesh]` extras に隔離 |
| フェアネスシステム | 実装済 | **低** (需要未定量) | `[compliance]` extras |
| Rate limit | 実装済 | 中 | Core に残す |
| Router | 実装済 | 中 | Core に残す |
| Audit log | 部分実装 | 高 | `[compliance]` extras 整備 |
| PII redaction | 部分実装 | 中 | `[compliance]` extras |
| semantic cache | 未実装 | 中 (Portkey 対抗) | Phase 2 で検討 |
| **deps --audit** | **未実装** | **🔴 最優先** | Core に内蔵 (差別化軸) |
| **`[cn-llm]` extras** | 部分実装 | **🔴 最優先** | Week 2-3 整備 |
| **`[cn-silicon]` extras** | 未実装 | 🟠 高 | Week 3-Phase 2 |
| **規制対応 docs** | 1/5 | 🟠 高 | Week 4 整備 |

## 6. Day 5 で得た重要 insight

1. **領域 1 (EAR-clean) は 0% 実装** — 完全 new build、Week 2 Day 1-7 で確実に α を
   出すことが Week 4 v3.2.0-rc1 の必須条件
2. **領域 2 (中国 LLM) は ~30% 実装** — 部分的に API 統合あるが、ファーストクラスと
   呼ぶには gap が大きい. Week 2-3 で extras 整備
3. **領域 3 (規制対応) は llive 側は ~80% 実装** — Brief API 経由なら HITL + Ledger +
   出典追跡が全て動く. llmesh 側と docs が gap
4. **Phase 3.6/3.7 (skill replication) が重さの主犯** — Core 軽量化で `[mesh]` extras に
   隔離が必須. これは Day 6 で機能 prune する
5. **規制対応 docs は 1/5 完成** — Week 4 で残 4 本 + ja/en/zh 化が必要、大ボリューム
6. **memory drift 修正の効果** — llive 側を「未実装」と思い込んでいたが実は実装済が
   多い. fit gap が予想より小さく、Week 1-4 計画が現実的に達成可能

## 7. Day 6 へ — 機能リスト (不要 / 過剰 / 不足)

本 fit gap を元に、Day 6 で:
- **不要機能候補**: 削除検討
- **過剰機能候補**: extras 隔離
- **不足機能候補**: 新規実装優先順位

## 8. 関連 docs

- `docs/market/gap-analysis.md` (Day 4) — 3 領域の絞り込み
- `D:/projects/audit/STRATEGY_EAR_LOCAL_LLM_2026-05-17_PART6_DEPS_AUDIT.md` — deps --audit 仕様
- [[project-llive-brief-api-done]] — 領域 3 llive 側の実装済確認
- [[feedback-implementation-status-record]] — 4 段階 taxonomy
