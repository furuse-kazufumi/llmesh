# llmesh 想定顧客プロファイル (Day 2)

> 2026-05-18 作成 (Day 2 前倒し). 中国信通院 (CAICT) + 日本 IPA + Gartner 等の
> 業界レポートから、5 業界 × 5 規模 = 25 セルの想定顧客プロファイルを draft.
> 戦略思索 [[project-market-layers-l1-l5]] の L1-L5 を業界レベルに分解.

## 1. 市場規模の data 裏付け (Day 1 補強 + Day 2 追加)

| 地域 / 軸 | 規模 | Source |
|---|---|---|
| 中国 AI 核心産業 (2025) | **1.2 兆元 ~$165B**, CAGR 24% | CAICT |
| Enterprise LLM 全球 (2026) | $6-8B | Fortune / GMInsights |
| アジア太平洋 LLM (2030) | **$94B**, CAGR 95% | 戦略 PART 1 |
| 日本 AI 市場 (2026) | $94B | renue.co.jp |
| 日本金融 LLM (2030) | **1500 億円** | renue.co.jp |
| Hybrid 配備 CAGR | 26.7% | Day 1 確認 |

## 2. 5 業界の選定根拠

| Layer (戦略 PART 1) | 業界 | 選定理由 |
|---|---|---|
| L1 (規制対応大手) | **金融 / 銀行 (BFSI)** | 規制最厳格、データ越境禁止、AI ガイドライン整備中 |
| L1 (規制対応大手) | **製造業 (自動車 / 半導体 / 通信機器)** | 産業 IoT 直結、SPC, 生産現場、Trade Secret |
| L1 / L2 | **政府 / 行政 (中央 / 地方)** | データ主権、地政学的に on-prem 必須 |
| L1 | **エネルギー / 公益 (電力 / 通信 / 重要インフラ)** | SCADA, OPC-UA, 重要インフラ防御 |
| L4 / L5 | **医療 / ヘルスケア** | PII / 患者データ、規制対応、ドメイン特化モデル需要 |

(教育 / 法務 / 建設 / 小売 / メディア等は将来 phase 拡張)

## 3. 5 規模の定義

| 規模 | 従業員 | 年商目安 | 意思決定速度 | 予算規模 (AI/年) |
|---|---|---|---|---|
| 巨大企業 (E) | 10,000+ | $1B+ / 1000 億円+ | 6-12 か月 | $1M-100M |
| 中堅大 (L) | 1,000-10,000 | $100M-$1B | 3-6 か月 | $100K-1M |
| 中堅 (M) | 100-1,000 | $10M-$100M | 1-3 か月 | $10K-100K |
| 中小 (S) | 10-100 | $1M-$10M | 2-4 週 | <$10K |
| スタートアップ / 部門 (X) | <10 | <$1M | 数日-数週 | <$1K |

## 4. 25 セルの顧客プロファイル (主要 fit 評価)

| 業界 \ 規模 | E | L | M | S | X |
|---|---|---|---|---|---|
| **金融** | ★★★★★ | ★★★★★ | ★★★★ | ★★ | ★ |
| **製造業** | ★★★★★ | ★★★★★ | ★★★★ | ★★★ | ★★ |
| **政府** | ★★★★★ | ★★★★ | ★★★ | ★★ | ★ |
| **エネルギー / 公益** | ★★★★★ | ★★★★ | ★★★ | ★★ | ★ |
| **医療** | ★★★★ | ★★★★ | ★★★ | ★★ | ★ |

(★ 1-5、★ = fit 低、★★★★★ = 最高 fit)

→ **主戦場は左上 (E/L × 金融/製造/政府/エネルギー)**. 大企業の規制対応セクター
集中. M (中堅) は採用ファネル後半で広がる. S/X は L7 採用ファネル.

---

## 5. 詳細ペルソナ (戦略上重要なセル 6 件のみ詳述)

### 5.1 金融 × 巨大 (E) — 規制金融機関の AI 統括部門

- **役職**: CIO / Chief AI Officer / 規制対応統括
- **Pain Point**:
  - 米国系 cloud LLM (Claude/OpenAI) を社内ポリシーで使えない
  - 既存 LiteLLM だが 2026/03 supply chain attack で再評価中
  - 中国 LLM (Qwen / DeepSeek / GLM) を on-prem で使う必要性が出てきた
  - 監査ログ / 出典追跡が金融庁検査で必須
- **現状の対処**: 自社 wrapper + 個別契約、ベンダロックイン不安
- **FullSense fit**:
  - llmesh-core + cn-llm extras → Qwen/DeepSeek/GLM ファーストクラス
  - llmesh deps --audit → 米国製依存ゼロ証明、調達ポリシー対応
  - llive + Approval Bus → 監査ログ + 出典追跡 (金融庁検査対応)
  - llove HTML export → 監査レポート出力
- **採用障壁**: 個人開発者の継続性 (3 年契約への不安) / 商用サポート不在
- **想定購買力**: 年 $100K-1M (パイロット → 本格契約)
- **対応戦略**: パイロット無料 → enterprise contract で sustainability 確保 (12 か月計画)

### 5.2 製造業 × 巨大 (E) — 自動車 / 半導体 / 通信機器の AI 推進室

- **役職**: スマートファクトリ責任者 / Production AI Lead
- **Pain Point**:
  - 生産現場の SCADA / PLC / OPC-UA データを LLM で異常検知したいが、
    on-prem で安全に運用する LLM hub が無い
  - LiteLLM は産業 IoT 対応無、Tabby は production line 観測対応無
  - 中国系企業は地政学的に米国 SaaS が使えない、国産 silicon (Ascend / Cambricon)
    との統合が必須
- **現状の対処**: 内製 + Qwen 直接、Edge AI 別系統
- **FullSense fit**:
  - llmesh-industrial extras → SPC + MQTT + OPC-UA + LLM
  - llmesh-cn-silicon extras → Ascend / Cambricon ファーストクラス
  - llove SPC pane → リアルタイム可視化
  - llive HITL Approval → 重要操作の人間判断
- **採用障壁**: 国産 silicon 実機テスト未済 (FullSense 側のリスク)、製造現場の
  legacy SCADA 統合の工数
- **想定購買力**: 年 $200K-2M (large factory)
- **対応戦略**: 1 工場で PoC → 全社展開、SiliconFlow 経由でテスト先行

### 5.3 政府 / 行政 × 巨大 (E) — 中央政府 / 自治体の DX 推進室

- **役職**: GovTech 統括 / セキュリティ統括官
- **Pain Point**:
  - 国民データの越境禁止、米国 SaaS は地政学的に不可
  - 中国 AI 弁法 / 改正サイバーセキュリティ法 / GDPR / 各国独自規制への対応
  - 中国信通院の「政務 / 央国企 / 金融」向け私有化部署需要そのもの
- **現状の対処**: 国営クラウド (阿里云 / 华为云 等) + 内製
- **FullSense fit**:
  - llmesh + llive + llove on-prem 完結
  - cn-internal-use.md (本日 commit 済) → 規制対応 docs
  - llmesh deps --audit → 政府調達基準対応
  - llive HITL Approval → 人間判断必須業務
- **採用障壁**: 政府調達手続 (随契禁止 / 競争入札)、個人 OSS 採用の難しさ
- **想定購買力**: 年 $500K-10M (中央) / $50K-500K (自治体)
- **対応戦略**: 政府系 SI (NTT / 富士通 / 国営大手 等) との連携経由で間接採用

### 5.4 エネルギー / 公益 × 巨大 (E) — 電力 / 通信 / 重要インフラの OT 部門

- **役職**: OT セキュリティ責任者 / 制御系 AI Lead
- **Pain Point**:
  - SCADA / 重要インフラの制御系で AI を導入したいが、外部接続絶対不可
  - air-gapped 環境で動く LLM スタックが必須
  - 重要インフラサイバーセキュリティ規制 (NIS2 / Critical Infrastructure 法)
- **現状の対処**: 内製、または専用 vendor (Schneider / Siemens 等の closed system)
- **FullSense fit**:
  - llmesh on-prem 完結 + air-gapped
  - llmesh-industrial extras → SCADA / OPC-UA 直接
  - llive 出典追跡 + 監査ログ → 規制対応
- **採用障壁**: OT 環境特有の要件 (deterministic latency / safety integrity)、
  vendor のロックイン
- **想定購買力**: 年 $300K-3M
- **対応戦略**: OT 向け system integrator と連携、IEC 62443 (OT 制御系セキュリティ)
  対応を Phase 2 で実装

### 5.5 金融 × 中堅大 (L) — 地方銀行 / フィンテック中堅

- **役職**: AI 推進室長 / 情報システム部長
- **Pain Point**:
  - 都銀のような潤沢な AI 予算は無いが、規制対応は必須
  - LiteLLM + 内製は人手不足、商用 SaaS は規制で使えない
  - 日本市場で「自社内で完結する LLM スタック」を探している
- **現状の対処**: 限定的な PoC、本格運用には至っていない
- **FullSense fit**:
  - llmesh-core + ja docs → 軽量 + 日本語対応
  - llove HTML export → 経営報告書出力
  - 規制対応 docs (日本金融庁 AI ディスカッションペーパー 2026/03 対応)
- **採用障壁**: 中堅銀行は商用サポート期待 (個人 OSS は嫌われる)、SI 経由が必須
- **想定購買力**: 年 $50K-300K
- **対応戦略**: 日本の地銀向け SI (NTT データ / NEC / Hitachi 等) との連携、
  日本市場向け sales 路線は Week 4 以降 docs 整備後

### 5.6 製造業 × 中堅 (M) — 中堅製造業の AI 推進

- **役職**: 製造部長 / DX 推進
- **Pain Point**:
  - 大手のように予算が無いが、AI 導入の競争圧力
  - Ollama / vLLM + 内製で頑張っているが、SPC / 産業 IoT との統合は手作業
- **現状の対処**: 限定的、生産改善の特定工程のみ
- **FullSense fit**:
  - llmesh-core (軽量) + llmesh-industrial extras
  - OSS なので無料、内製チームが拡張可能
- **採用障壁**: 自社で技術理解できる人材の確保、商用サポート不在
- **想定購買力**: 年 $5K-50K
- **対応戦略**: 採用ファネル後半、L7 経由で自然流入、コミュニティ活用

---

## 6. 採用障壁の共通パターン (戦略含意)

全 25 セルを通して、共通する障壁:

| 障壁 | 対応戦略 |
|---|---|
| 個人開発者の継続性不安 | 1) git log の活発さ可視化 / 2) コミュニティ参加 / 3) 12 か月後に enterprise contract で sustainability 担保 |
| 商用サポート不在 | パートナー SI 連携で間接サポート提供、または Phase 3 (12 か月+) で商用化検討 |
| 政府調達 / 大企業調達手続 | SI 経由が標準、直接契約は無理、調達基準 (deps --audit) で SI を支援 |
| 国産 silicon 実機未確認 | SiliconFlow 経由テスト + パートナーシップ、Phase 2-3 で実機環境確保 |
| 日本市場での認知不足 | 日本 SI / 公開記事 / コミュニティ参加、ただし投稿記事は articles_pause 中 |

## 7. Day 3-7 で進める残作業

- **Day 3**: 競合機能比較 matrix (Portkey OSS 化 2026/03 反映、要 update)
- **Day 4**: 「LiteLLM が届かない領域」を 3 箇所絞り込み (顧客ペルソナ起点で)
- **Day 5**: 各 gap に対する llmesh 現機能 fit gap (4 段階 status taxonomy 適用)
- **Day 6**: 不要 / 過剰 / 不足機能リスト
- **Day 7**: Roadmap 再構築 draft (Core / Extras 分割 + 機能優先順位)

## 8. Day 2 で得た重要 insight

1. **中国 AI 核心産業 1.2 兆元 (~$165B, CAGR 24%)** が CAICT 公式数値。FullSense の
   中国市場戦略の根拠 data が更に固まった
2. **CAICT は「政務 / 央国企 / 金融」を「データセキュリティ要求高 + 個性化需要」
   と明示し、私有化部署 (on-prem) + customization を最適と判定**。これは FullSense
   の販売 messaging で直接引用できる
3. **日本金融 LLM 市場 2030 年 1500 億円 / 既に on-prem OSS LLM 採用が進む** → 日本
   L1 市場の現実性が data で裏付け
4. **「汎用 LLM × ドメイン知識」が主戦場** (日本 IPA / CAICT 両者) → FullSense の
   個性化 (TRIZ / OKA-FX / 思考因子) が差別化軸として機能する
5. **採用障壁の最大は「個人開発者の継続性 + 商用サポート不在」** → 12 か月後の
   enterprise contract 1 件獲得 (戦略思索 PART 3 章 12) が sustainability の鍵
6. **左上 (E/L × 規制セクター) が主戦場、右下 (S/X) は採用ファネル** という棲み分けが
   明確に. messaging / docs / 営業戦略をこの構造に合わせる

## 9. Sources

- [中国信通院 (CAICT) 2026 人工知能産業発展研究報告](https://www.caict.ac.cn/kxyj/qwfb/bps/202602/P020260202487301304903.pdf)
- [BetterYeah 2026 企业级智能体平台对比评测 - 私有化部署选型指南](https://www.betteryeah.com/blog/enterprise-ai-agent-platform-comparison-private-deployment-2026)
- [新华深读 2026 年中国 AI 发展趋势前瞻](https://www.news.cn/20260128/3b2f11906fd74ca397fef9996c805a60/c.html)
- [renue.co.jp AI 業界の現状完全ガイド 2026 日本 94 億ドル市場](https://renue.co.jp/posts/ai-industry-state-2026-world-2-5t-japan-94b-llm-3-giants-5-trends)
- [日本金融庁 AI ディスカッションペーパー 2026 年 3 月](https://www.fsa.go.jp/news/r7/sonota/20260303/aidp_version1.1.pdf)
- [はてなベース 2026 年オンプレミス生成 AI 完全ガイド](https://hatenabase.jp/blog/on-premise-generative-ai-guide-202/)
- [PwC Japan 行政の生成 AI 調達・利活用ガイドライン解説](https://www.pwc.com/jp/ja/knowledge/column/ai-governance/procurement-and-utilization.html)
- [METI AI 事業者ガイドライン](https://www.meti.go.jp/shingikai/mono_info_service/ai_shakai_jisso/index.html)

## 関連 memory / docs

- [[project-market-layers-l1-l5]] — L1-L5 layer 別戦略
- [[project-fullsense-ear-origin]] — 起源、CN 規制市場前提
- [[project-cn-ai-compliance-internal-use-exemption]] — 規制対応 docs
- [[project-llmesh-critical-review]] — 競合分析 (Portkey OSS 化要 update)
- [[project-30day-action-plan-2026-05]] — Day 3-7 タスク

## 改訂履歴

- 2026-05-18 — v1 作成 (Day 2 前倒し、約 30 分)
