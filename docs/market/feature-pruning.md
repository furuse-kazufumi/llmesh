# llmesh 機能 prune リスト — 不要 / 過剰 / 不足 (Day 6)

> 2026-05-18 作成 (Day 6 前倒し). Day 5 fit gap 分析を元に、機能を 3 分類:
> - **不要 / 削除候補**: 戦略上意義が薄い、廃止検討
> - **過剰 / extras 隔離候補**: Core から切り離し optional 化
> - **不足 / 新規実装候補**: 戦略的に必須

## 1. 不要機能 — 削除候補 (4 件)

戦略思索で「需要未定量」と判定された機能、または「他社が圧倒的に強い」領域:

### F-DEL-1: フェアネスシステム
- **理由**: memory [[project-llmesh-fairness]] 83 件全 PASS だが、需要が定量化
  されていない. L1-L5 市場で「fairness」を主要要件として挙げる顧客はゼロ
- **削除/隔離**: `[compliance]` extras に隔離、メイン path から外す. 完全削除は
  しない (実装済の sunk cost を活かす)
- **判断**: 削除はせず extras 隔離、Phase 2 で需要再評価

### F-DEL-2: 一部の Audit log 重複機能
- **理由**: llive 側 Approval Bus + SQLite Ledger と機能重複の箇所がある
- **削除/隔離**: llive 側に統一、llmesh は llive の Ledger を呼ぶだけにする
- **判断**: Phase 2 で精査して duplication 解消

### F-DEL-3: 古い MCP 旧バージョン対応コード
- **理由**: MCP 1.23.0+ が standard、旧バージョン互換コードは保守コスト
- **削除/隔離**: 最新 MCP 仕様のみサポート、旧バージョンは v3.0 で deprecate
- **判断**: v3.2.0 で deprecation warning、v3.3.0 で削除

### F-DEL-4: 内製 cache (Phase 3.0 以前)
- **理由**: Portkey 2026/03 OSS 化で semantic cache が無料化、内製は劣化版に
- **削除/隔離**: Phase 2 で Portkey OSS gateway を組み込む or 内製を改良
- **判断**: Phase 2 で戦略再評価

## 2. 過剰機能 — Core から extras に隔離 (6 件)

PART 6 章 5.2 で提案した Core / Extras 分割の具体化:

### F-EXT-1: P2P mesh + skill chunk replication (Phase 3.6/3.7)
- **隔離先**: `llmesh[mesh]` extras
- **理由**: 普通の用途には完全に過剰、依存が重い、研究的色合いが強い
- **影響**: Core size 大幅減、起動時間短縮 (推定 30-40%)

### F-EXT-2: PeerReputation
- **隔離先**: `llmesh[mesh]` extras
- **理由**: P2P mesh とセット、独立して使う場面ほぼなし

### F-EXT-3: SPC (統計的工程管理)
- **隔離先**: `llmesh[industrial]` extras
- **理由**: 産業 IoT 向け、汎用 LLM hub には不要

### F-EXT-4: MQTT / OPC-UA
- **隔離先**: `llmesh[industrial]` extras
- **理由**: F-EXT-3 と同じ、産業 IoT 向け

### F-EXT-5: PII redaction (presidio 等)
- **隔離先**: `llmesh[compliance]` extras
- **理由**: 規制対応企業向け、汎用には不要 (依存 presidio が重い)

### F-EXT-6: フェアネスシステム
- **隔離先**: `llmesh[compliance]` extras
- **理由**: F-DEL-1 で削除しない判断 → extras 隔離

### Core に残す機能 (差別化軸 + 基本機能)
- LLM routing (汎用)
- MCP server
- Rate limit
- Router
- Audit log (基本)
- 監視メトリクス (基本)
- **`deps --audit`** ← 差別化軸として Core 内蔵

## 3. 不足機能 — 新規実装 (10 件、優先度順)

### 🔴 最優先 (Week 2-4)

#### F-NEW-1: `llmesh deps --audit`
- 仕様: 戦略思索 PART 6 完成
- 工数: Week 2 Day 1-7 (7 日)
- 影響: L1 市場入口の決定打

#### F-NEW-2: `[cn-llm]` extras (Qwen/DeepSeek/GLM/Kimi/Baichuan)
- 仕様: API 差異正規化 + 統一 schema + 公式機能 first-class
- 工数: Week 2-3 (10 日)
- 影響: 中国市場参入の必須条件

#### F-NEW-3: 中国 mirror (gitee) 自動同期
- 仕様: GitHub → gitee sync CI
- 工数: Week 4 (1-2 日)
- 影響: 中国市場アクセシビリティ

#### F-NEW-4: 規制対応 docs 残 4 本
- 仕様: cn-public-service / eu-ai-act / data-sovereignty / audit-log-format
- 工数: Week 4 (3-4 日)
- 影響: L1 + L5 市場のセールスポイント

### 🟠 高優先 (Month 2)

#### F-NEW-5: 多言語 docs (ja/en/zh)
- 仕様: 主要 docs を 3 言語化
- 工数: Phase 2 (2 週間)
- 影響: 各市場 layer での認知度

#### F-NEW-6: `[cn-silicon]` extras (Ascend / Cambricon)
- 仕様: MindSpore + PaddlePaddle 経由
- 工数: Phase 2 (2-3 週、ただし実機テスト未確保)
- 影響: 中国 enterprise 製造業向け

#### F-NEW-7: HTML レポート出力 (`deps --audit --html`)
- 仕様: 調達担当者向け visual report
- 工数: Phase 2 (1 週)
- 影響: 企業意思決定者への訴求

### 🟡 中優先 (Month 3-4)

#### F-NEW-8: SBOM export (CycloneDX / SPDX)
- 仕様: 業界標準フォーマット
- 工数: Phase 3 (1 週)
- 影響: enterprise SBOM 管理ツール統合

#### F-NEW-9: semantic cache (Portkey 対抗)
- 仕様: embedding-based cache
- 工数: Phase 3 (2 週)
- 影響: コスト削減 + Portkey との同等化

#### F-NEW-10: F25 Phase h E2E 完成
- 仕様: llove ↔ llmesh ↔ llive MCP 経由完全動作
- 工数: Week 2-3 (5 日、llove 側との並行)
- 影響: 統合 demo + 戦略思索 Insight 4 整合

## 4. Day 6 で得た重要 insight

1. **Core / Extras 分割で起動時間 / 配布サイズが推定 30-40% 改善** — Phase 3.6/3.7
   の skill replication + 産業 IoT を extras 化することで Core は LiteLLM と勝負
   できる軽さに
2. **削除候補は 4 件のみ、隔離 (extras 化) で対応**が大半 — sunk cost を活かしながら
   Core 軽量化を実現
3. **新規実装 10 件のうち、Week 2-4 で最優先 4 件 (deps --audit + cn-llm + gitee +
   規制 docs)** — これらが Week 4 v3.2.0-rc1 リリースの core
4. **Month 2 で多言語 + 国産 silicon 着手** — Phase 2 で市場アクセシビリティを完成
5. **semantic cache は Phase 3 (Month 3-4)** — Portkey 2026/03 OSS 化への対応、
   ただし急がない (FullSense の差別化軸とは別)

## 5. Day 7 (Roadmap 再構築) へ

本リストを元に Day 7 で Roadmap v3.2.0 → v4.0.0 を書く.

## 6. 関連 docs

- `docs/market/fit-gap.md` (Day 5) — fit gap 評価
- `docs/market/gap-analysis.md` (Day 4) — 3 領域絞り込み
- `D:/projects/audit/STRATEGY_EAR_LOCAL_LLM_2026-05-17_PART6_DEPS_AUDIT.md` — F-NEW-1 仕様
- [[project-llmesh-critical-review]] — Core 軽量化方針
