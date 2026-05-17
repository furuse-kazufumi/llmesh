# llmesh Roadmap 再構築 v3.2.0 → v4.0.0 draft (Day 7)

> 2026-05-18 作成 (Day 7 前倒し). Week 1 需要定量化スプリント (Day 1-6) の総合.
> Core 軽量化 + 真の差別化 extras 集中投資 + L1-L3 市場対応を 6 か月で達成する
> ロードマップ.

## 1. 戦略原則

1. **Core を LiteLLM と勝負できる軽さに**: Phase 3.6/3.7 + 産業 IoT + フェアネスを
   extras 隔離、Core は LLM routing + MCP + `deps --audit` に絞る
2. **3 領域 (EAR-clean / 中国 LLM / 規制対応) に集中投資**: Day 4 gap analysis で
   絞り込んだ領域、LiteLLM が構造的に届かない
3. **L1-L3 市場を最優先**: 中国系大手 + 制裁対象国 + 中国系チェーン. L6-L7 は無理に
   取らない (Tabby / Cody / Continue / Portkey に道を譲る)
4. **Marketplace 非依存配布**: VSIX 直接 + gitee mirror + Docker + pip wheel が main
5. **個人開発者の優位活用**: 速さ + OSS 透明性 + honest disclosure を武器

## 2. ロードマップ概観

| Release | 期間 | Theme | 主要機能 | KPI |
|---|---|---|---|---|
| v3.2.0 | Week 4 (6/14) | **Core 軽量化 α + EAR-clean** | deps --audit + cn-llm extras + cn-internal-use docs + gitee mirror | rc1 公開、内部 testing |
| v3.3.0 | Month 2 (7/14) | **中国市場対応完成** | cn-llm 完全化 + 多言語 docs + Engine 抽出統合 + 規制 docs en/zh | rc1 → stable |
| v3.4.0 | Month 3 (8/14) | **国産 silicon + 規制 docs 完全化** | cn-silicon extras + semantic cache + EU AI Act docs + HTML report | Beta release |
| v3.5.0 | Month 4-5 | **enterprise 機能整備** | SBOM export + LDAP/SSO 統合 + Portkey 互換 + アンプリファイア | β → stable |
| **v4.0.0** | Month 6 (11/18) | **メジャー: Core 完全軽量化 + L1-L3 本格採用** | L1-L3 で paying user 1 件、商用契約検討 | Production GA |

## 3. v3.2.0 (Week 4 = 2026-06-14)

### Theme: "EAR-clean foundation"

### 機能 (must)
- ✅ `llmesh deps --audit` α (CLI + JSON / 表形式出力)
  - internal `origins.toml` 初期 100 packages
  - supply_risk DB 初期 (litellm 2026/03 等)
  - 静的 API call 検出 (AST 解析)
- ✅ `llmesh[cn-llm]` extras α (Qwen / DeepSeek / GLM / Kimi 統合テスト)
- ✅ 中国 mirror (gitee) 同期 + Docker registry mirror
- ✅ `docs/regulatory/cn-internal-use.md` (本日 commit 済) + 残 4 本 ja draft
- ✅ Core / Extras 分割設計実装 (pyproject extras 定義)

### 機能 (should)
- HTML レポート (簡易版)
- Engine 抽出設計書 (llove と並行)

### 機能 (won't, Phase 2 以降)
- 多言語 (en/zh) docs 完全化
- 国産 silicon 実機テスト
- semantic cache

### 撤退条件
- Week 2 Day 7 で `deps --audit` が自身を audit して 100 deps 解析できない →
  α 機能を JSON 出力のみに縮小
- Week 4 で v3.2.0-rc1 リリース不能 → 1 か月延期、品質確保継続

### KPI
- v3.2.0-rc1 PyPI publish (pre-release)
- gitee mirror 同期完了
- 内部 testing 通過
- docs ja 完成 (cn-internal-use + 1-2 本)

## 4. v3.3.0 (Month 2 = 2026-07-14)

### Theme: "中国市場対応完成"

### 機能 (must)
- ✅ `[cn-llm]` 完全化 (Baichuan + Yi + その他主要中国 LLM)
- ✅ API 差異正規化の完全化 (統一 schema、validation)
- ✅ 多言語 docs (ja/en/zh) 主要 5 本完成
  - cn-internal-use.md (ja → en + zh)
  - eu-ai-act.md (ja + en + zh)
  - data-sovereignty.md (ja + en + zh)
  - audit-log-format.md (ja + en + zh)
  - deps --audit 説明 docs (ja + en + zh)
- ✅ Engine 抽出 (llove) 統合 — F25 Phase h E2E 完成、llmesh ↔ llove MCP 経由
- ✅ HTML レポート完成版

### 機能 (should)
- 月次 audit script (社内 marketplace 配備で活用)
- 監査ログ format 統一

### KPI
- v3.3.0 stable リリース
- 中国 GitHub mirror (gitee) の Star 50+ / Issue 5+
- 業界レポート (1 本) 寄稿
- 日本 IPA / 中国信通院系の引用 1 件

## 5. v3.4.0 (Month 3 = 2026-08-14)

### Theme: "国産 silicon + 規制 docs 完全化"

### 機能 (must)
- ✅ `[cn-silicon]` extras (MindSpore + PaddlePaddle 経由 Ascend 動作確認)
- ✅ semantic cache (Portkey 対抗、Phase 3 で実装)
- ✅ EU AI Act 対応 docs 完全化
- ✅ 日本金融庁 AI ディスカッションペーパー対応 docs
- ✅ SBOM (CycloneDX / SPDX) export

### 機能 (should)
- Cambricon 実機テスト (SiliconFlow 経由でも OK)
- `--runtime-check` (動的 API call 検出)

### KPI
- v3.4.0 β リリース
- 国産 silicon 動作確認 1 種 (Ascend 最低)
- 業界レポート 2 本目寄稿
- 中国 OSS イベント (PyCon China / 云栖 等) 寄稿

## 6. v3.5.0 (Month 4-5 = 2026-09-14 ~ 10-14)

### Theme: "enterprise 機能整備"

### 機能 (must)
- ✅ LDAP / SSO 統合 (Tabby v0.24 同等)
- ✅ admin dashboard (簡易版、TUI 経由)
- ✅ Portkey 互換性 (semantic cache 経由で gateway 互換)
- ✅ usage analytics (社内 marketplace 配備用)

### KPI
- v3.5.0 stable
- enterprise PoC 1 件 (L1 市場)
- 中国 OSS / 日本 OSS イベント発表 2 本

## 7. v4.0.0 (Month 6 = 2026-11-18) - **Major Release**

### Theme: "L1-L3 で本格採用 + Core 完全軽量化"

### 機能 (must)
- ✅ Core 完全軽量化 (Phase 3.6/3.7 skill replication を `[mesh]` extras に完全隔離)
- ✅ `[mesh]` / `[industrial]` / `[compliance]` / `[cn-llm]` / `[cn-silicon]` の
  5 extras が安定動作
- ✅ 全 docs 多言語 (ja/en/zh) 完成
- ✅ 規制対応 docs 5 本 + ガイダンス完成
- ✅ Tabby / Continue.dev 統合 demo (integration story)
- ✅ Production GA

### KPI (戦略思索 PART 3 章 12.3 と整合)
- L1 市場で paying user / enterprise contract **1 件以上**
- 阿里云 / 华为云 / 腾讯云 のいずれかでマーケットプレース掲載
- GitHub Star 500+ / gitee Star 100+
- 中国 OSS イベント発表 1 本以上
- 北米 OSS イベント (PyCon / Linux Foundation) 発表 1 本以上

### 撤退条件 (戦略思索 PART 3 章 12.2)
- 6 か月で Core 切り出しが完了せず重さが解消されない → LiteLLM に道を譲り、llive
  内蔵 hub として吸収
- 12 か月で L1-L3 市場の採用ゼロ → 産業 IoT 一本に絞り SCADA / PLC 市場専門化

## 8. リスクと対応

| Risk ID | 内容 | 対応 |
|---|---|---|
| R-1 | Tabby / Portkey が真似てくる | 速さで先行、4 層メモリ + 思考因子で深掘り |
| R-2 | 中国規制急変 | 月次で update 監視、docs に disclaimer |
| R-3 | 国産 silicon 実機未確保 | SiliconFlow 経由テスト、partner 探し |
| R-4 | 個人開発者継続性 | git log の活発さ可視化、12 か月で enterprise contract |
| R-5 | 中国市場参入の文化的障壁 | gitee mirror + 中文 docs で自然流入、無理な営業はしない |
| R-6 | supply chain attack | `deps --audit` を自分にも適用、署名検証導入 |
| R-7 | 地政学的二次制裁 | 中立 OSS スタンス、特定国向け marketing しない |

## 9. 中間判定基準

各リリース末で確認:

| Release | 判定 KPI |
|---|---|
| v3.2.0 | rc1 publish, gitee 同期動作確認, deps --audit α 動作 |
| v3.3.0 | stable, 中国市場での認知開始 (gitee Star 50+) |
| v3.4.0 | β, 国産 silicon 動作確認 1 種 |
| v3.5.0 | enterprise PoC 1 件 |
| v4.0.0 | paying user / contract 1 件 |

不達なら次 release を 1 か月延期 + 戦略再評価.

## 10. Day 7 + Week 1 全体で得た重要 insight

1. **6 か月で v3.2.0 → v4.0.0 の現実的なロードマップが定量化された**. 当初の漠然と
   した「Week 1-4 計画 + 6 か月成功条件」が具体的なリリース計画になった
2. **3 領域集中 (EAR-clean / 中国 LLM / 規制対応) が全リリースに通底**. v4.0.0 ですら
   この 3 領域の深掘りで完結する
3. **Core / Extras 分割で起動時間 / 配布サイズが 30-40% 改善見込み** — これだけで
   LiteLLM 競合と勝負できる軽さ
4. **L1-L3 市場の paying user 1 件 = 12 か月後の中間判定** が戦略の sustainability
   閾値. これに到達すれば商用化検討、未達なら個人 OSS として継続
5. **Week 1 需要定量化スプリント (Day 1-7) が予想以上に深い** — 業界レポート + 顧客
   ペルソナ + 競合 matrix + gap + fit + 機能リスト + Roadmap で 1 週間分の戦略
   document が出来た. Week 2-4 の実装は data 裏付け済

## 11. Week 1 完了報告

| Day | 成果物 | 状態 |
|---|---|---|
| Day 0 | llove gap analysis | ✓ `D:/projects/llove/docs/audits/dogfooding-day0-gap.md` |
| Day 0 | llive bug 8 件 status | ✓ `D:/projects/llive/docs/bugs/bug-8-status.md` |
| Day 1 | 業界レポート | ✓ `docs/market/reports-2026-05.md` |
| Day 2 | 顧客ペルソナ | ✓ `docs/market/customer-personas.md` |
| Day 3 | 競合 matrix | ✓ `docs/market/competitor-matrix.md` |
| Day 4 | gap analysis (3 領域) | ✓ `docs/market/gap-analysis.md` |
| Day 5 | fit gap | ✓ `docs/market/fit-gap.md` |
| Day 6 | 機能リスト (不要/過剰/不足) | ✓ `docs/market/feature-pruning.md` |
| Day 7 | Roadmap v3.2 → v4.0 draft | ✓ `docs/market/roadmap-v4-draft.md` (本書) |

→ **Week 1 needs スプリント全完了**. Week 2 (Engine 抽出 + Core 軽量化 + cn-llm
extras + deps --audit α) に確信を持って着手できる data 基盤が整った.

## 12. 関連 docs

- 全 Day 1-6 docs (本 directory)
- 戦略思索 PART 1-6 + RETROSPECTIVE (`D:/projects/audit/STRATEGY_*`)
- 関連 memory 全件 (MEMORY.md 参照)
