# llmesh 業界レポート収集 — 2026-05 (需要定量化スプリント Day 1)

> 2026-05-18 作成. 戦略思索 PART 2 章 5.5 + PART 6 の続き. 需要定量化スプリント
> Day 1 として業界レポート / Gartner 予測 / 競合 OSS pricing を収集.

## 1. Enterprise LLM 市場規模 (2026 時点)

複数ソースで規模感を triangulate:

| Source | 2026 規模 | 2030+ 予測 | CAGR |
|---|---|---|---|
| GMInsights | $8.19B | — | — |
| Fortune Business Insights | $5.91B | $48.25B (2034) | **30.0%** |
| SNS Insider | — | $55.60B (2032) | — |
| Straits Research | — | — | 30%+ 推定 |

→ **2026 規模 ~$6-8B、2030-34 で $40-55B**. CAGR 30% は非常に高成長.

## 2. デプロイメント形態の trend (重要)

- **Cloud**: 2025 revenue share **41.74%** (依然最大、ただし減少傾向)
- **Hybrid**: 最速 CAGR **26.7%** — driver は **データプライバシ / 規制対応 / data residency**
- **On-prem**: 数値未明だが、Hybrid + on-prem 合計で cloud に拮抗
- 中心セクター: **BFSI (金融) / healthcare / government** = まさに L1-L5 fit
- 「データプライバシ / 規制対応で hybrid に移行」は FullSense 戦略の追い風

## 3. Gartner 予測 (2026 公表分)

直接的に FullSense 戦略に効く Gartner 予測:

### G1: 2027 — 35% の国が region-specific AI platform に lock-in
- regional LLM が global model を上回る (education / 法務 / 公共サービス)
- **FullSense の「中国 LLM ファーストクラス + 地域 mirror 配布」戦略と完全整合**
- 5 国に 1 国が地域特化 → L1-L5 市場の根拠データ

### G2: 2027 — small / task-specific AI models が general-purpose の 3 倍利用
- 小型特化モデル時代の到来 → on-prem inference の現実性が上昇
- Local LLM hub (llmesh-core) の需要を裏付け

### G3: 2028 — Explainable AI が LLM observability 投資の 50% を driver
- llive の「思考因子 / 出典追跡 / Annotation」可視化機能と整合
- 観測 / 説明可能性は L1-L5 で必須要件化

### G4: 2030 — LLM 推論コストが 90% drop
- on-prem LLM の TCO が大幅低下 → cloud LLM とのコスト差が更に縮小
- L1-L5 への on-prem 移行を後押し

## 4. アジア太平洋 LLM 市場

| Period | 規模 |
|---|---|
| 2023 | $416.56M |
| 2030 | **$94B** (推定) |

→ **CAGR ~95%+**、グローバル CAGR 30% を大きく上回る. 中国 + 日本 + 韓国が driver.

→ FullSense の L1-L3 (規制対応大手・中国系チェーン) 想定 TAM (戦略思索 PART 2
$50-70B) の根拠. 単独中国市場だけでも 2026 で $5-10B 規模が見えてくる.

## 5. 競合 LLM Gateway / Observability の pricing 整理

### 5.1 LiteLLM
- 完全 OSS、self-host で **無料**
- 本番運用インフラ目安: $100-400/月 (DB + replication + HA)
- 2026/03 supply chain attack (v1.82.7/1.82.8 で credential 盗難 malware) で
  **信頼性に致命傷**

### 5.2 Portkey ⚠ (本日新情報)
- **2026/03 に gateway を Apache 2.0 で OSS 化** ← 前回戦略思索の評価が変わる
- Managed: Free tier / $49/月 (production) / enterprise custom
- per-log: 500K req ~$36/月、1M req ~$81/月、2M req ~$171/月
- **semantic cache が最強差別化機能** (exact-match cache を超える hit rate)

### 5.3 Helicone
- Self-host **無料**、observability は managed 有料

### 5.4 Langfuse
- **MIT license**、self-host **無料**
- LangChain / LlamaIndex / OpenAI / Anthropic / Vercel AI SDK / LiteLLM /
  Flowise / Langflow 等 native 統合
- EU 拠点 (ドイツ Berlin)、GDPR 親和性

### 5.5 OpenRouter
- 完全 SaaS、200+ models、pay-per-token

### 5.6 Tabby (前回 PART 4 で deep dive)
- OSS、GitHub Stars 33,000+、Rust 92%
- self-host、air-gapped、enterprise (LDAP/SSO/GitLab MR)
- **コード補完中心**、FullSense とは用途部分重なり

## 6. 競合 OSS 化のインパクト分析

| 競合 | OSS 状態 | self-host コスト | FullSense への影響 |
|---|---|---|---|
| LiteLLM | OSS (MIT) | 無料 + インフラ | 信頼性疑問で間接優位 |
| **Portkey** | **2026/03 Apache 2.0 化** | **無料 + managed $49/月+** | **想定外、戦略再評価必要** |
| Helicone | self-host 無料 | 無料 | 直接影響薄 |
| Langfuse | OSS (MIT) | 無料 | L4-L5 で正面競合 |
| Tabby | OSS | 無料 | コード補完特化、棲み分け可 |

### Portkey OSS 化への対応戦略 (重要、修正点)

- **戦略思索 PART 2 章 4.1 で「Portkey: SaaS が main、self-host は二級」と書いたのは
  2026/03 以前の評価**. 現状は Apache 2.0 OSS gateway として LiteLLM と直接競合する
  存在に変わった
- **semantic cache 機能は llmesh が真似すべき**. ただし llmesh の core 差別化軸
  (EAR-clean / 中国 LLM ファースト / 産業 IoT / 規制対応) は Portkey が短期に追従
  できない領域
- L6 (北米 SEC) では **LiteLLM 撤退 + Portkey 上昇** が予測される. FullSense は
  L6 を狙わず L1-L5 集中で OK
- 競合 matrix を Day 2 で完成させる際、Portkey の最新評価を反映する必要あり

## 7. 中国系 LLM の OpenRouter シェア (再確認、前回 PART 1 で取得)

| 期間 | 中国 LLM シェア |
|---|---|
| 2025/02 (DeepSeek 前) | 2% |
| 2025 末 | 30% (グローバル使用率) |
| 2026/04 | **45% 超** (OpenRouter) |

→ 半年で 15% 増、年間ペースで cloud LLM 市場の中国系シェアが過半数に到達する勢い.
**llmesh が中国 LLM ファーストクラスにするタイミングとして遅すぎない** (ぎりぎり間に合う).

## 8. Day 1 で得た重要 insight

1. **Enterprise LLM 市場規模 2026 $6-8B、CAGR 30%** → 戦略思索 TAM 推定 ($50-70B) は
   控えめでむしろ過小評価の可能性
2. **Hybrid CAGR 26.7%** がデータプライバシ / 規制対応 driver で最速成長 → FullSense
   の追い風が data 裏付け
3. **Gartner G1: 2027 — 35% region-specific AI lock-in** が FullSense 地域戦略の
   根拠データ. これは Qiita / LinkedIn 記事執筆時の引用に使える
4. **Gartner G3: 2028 — Explainable AI が observability 50% driver** が llive 思考因子
   可視化機能の根拠データ
5. **アジア太平洋 LLM 市場 $416M (2023) → $94B (2030)** = CAGR 95% 級. 中国市場
   集中戦略が data で裏付け
6. **Portkey 2026/03 Apache 2.0 化が想定外** → 競合 matrix 再評価必要、ただし FullSense
   の core 差別化軸 (EAR-clean / 規制対応 / 中国 LLM / 産業 IoT) は短期競合不可
7. **中国系 LLM が OpenRouter で 45% 超** → llmesh `[cn-llm]` extras 優先度は最高

## 9. Day 2-7 (5/19-5/24) で進める残作業

- **Day 2**: 中国信通院 / IPA レポートで日本・中国 enterprise AI 市場詳細
- **Day 3**: 想定顧客プロファイル (5 業界 × 5 規模) を `docs/market/customer-personas.md`
- **Day 4**: 競合機能比較 matrix を `docs/market/competitor-matrix.md` (Portkey 修正版)
- **Day 5**: LiteLLM では届かない領域を 3 箇所に絞り込む (`docs/market/gap-analysis.md`)
- **Day 6**: 各 gap に対する llmesh 現機能との fit gap (`docs/market/fit-gap.md`)
- **Day 7**: 不要 / 過剰 / 不足機能リスト + Roadmap 再構築 draft

## 10. Sources

- [Enterprise LLM Market (Fortune Business Insights)](https://www.fortunebusinessinsights.com/enterprise-llm-market-114178)
- [Enterprise LLM Market Growth (Straits Research)](https://straitsresearch.com/report/enterprise-llm-market)
- [Enterprise LLM Market (GMInsights)](https://www.gminsights.com/industry-analysis/enterprise-llm-market)
- [Enterprise LLM Market (The Business Research Company)](https://www.thebusinessresearchcompany.com/report/enterprise-large-language-models-llm-market-report)
- [Enterprise LLM Market $55.60B by 2032 (SNS Insider)](https://www.globenewswire.com/news-release/2026/01/05/3212891/0/en/Enterprise-LLM-Market-to-Reach-USD-55-60-Billion-by-2032-Owing-to-Rising-AI-Adoption-and-Intelligent-Automation-Research-by-SNS-Insider.html)
- [Gartner: 35% region-specific AI lock-in by 2027](https://www.gartner.com/en/newsroom/press-releases/2026-01-29-gartner-predicts-35-percent-of-countries-will-be-locked-into-region-specific-ai-platforms-by-2027)
- [Gartner: small task-specific AI models 3x by 2027](https://www.gartner.com/en/newsroom/press-releases/2025-04-09-gartner-predicts-by-2027-organizations-will-use-small-task-specific-ai-models-three-times-more-than-general-purpose-large-language-models)
- [Gartner: Explainable AI drives 50% LLM observability investment](https://www.gartner.com/en/newsroom/press-releases/2026-03-30-gartner-predicts-by-2028-explainable-ai-will-drive-llm-observability-investments-to-50-percent-for-secure-genai-deployment)
- [Gartner: LLM inference cost -90% by 2030](https://www.hpcwire.com/aiwire/2026/03/25/gartner-forecasts-90-drop-in-llm-inference-costs-by-2030/)
- [LiteLLM vs Portkey vs OpenRouter 2026 (PocketLantern)](https://pocketlantern.dev/briefs/llm-gateway-litellm-vs-portkey-vs-openrouter-pricing-and-routing-2026)
- [Top 5 LLM Gateways 2026 (DEV Community)](https://dev.to/varshithvhegde/top-5-llm-gateways-in-2026-a-deep-dive-comparison-for-production-teams-34d2)
- [Best LiteLLM Alternatives 2026 (Eden AI)](https://www.edenai.co/post/best-alternatives-to-litellm)

## 関連 memory / docs

- [[project-llmesh-critical-review]] — Portkey OSS 化で評価修正の根拠
- [[project-market-layers-l1-l5]] — TAM 数値の裏付け強化
- [[project-fullsense-ear-origin]] — Gartner G1 が地域特化戦略を後押し
- [[project-30day-action-plan-2026-05]] — Day 2-7 タイムライン
- `D:/projects/audit/STRATEGY_EAR_LOCAL_LLM_2026-05-17_PART2.md` 章 5.5 — 本作業の根拠

## 改訂履歴

- 2026-05-18 — v1 作成 (Week 1 Day 1、約 30 分)
