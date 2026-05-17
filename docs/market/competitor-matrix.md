# 競合機能比較 matrix (Day 3)

> 2026-05-18 作成 (Day 3 前倒し). LLM Gateway / Observability / Agent Framework /
> AI コーディングアシスタントの 8 競合 + FullSense の機能比較.
> **Portkey 2026/03 Apache 2.0 OSS 化** を反映した最新版.

## 1. 比較対象 (9 製品)

| 製品 | カテゴリ | OSS | self-host | 拠点 | License |
|---|---|---|---|---|---|
| **LiteLLM** | LLM Gateway | ✓ | ✓ | 米国 | MIT |
| **Portkey** ⚠2026/03 | LLM Gateway | **✓ (gateway only)** | **✓** | 米国 | Apache 2.0 (gateway) / commercial (managed) |
| **Helicone** | Observability + Gateway | ✓ | ✓ | 米国 | Apache 2.0 |
| **Langfuse** | Observability | ✓ | ✓ | EU (DE) | MIT |
| **OpenRouter** | LLM Marketplace | ✗ | ✗ | 米国 | (closed SaaS) |
| **Tabby** | AI コーディングアシスタント | ✓ | ✓ | 米国 (TabbyML) | Apache 系 |
| **Continue.dev** | IDE 拡張 (BYO backend) | ✓ | ✓ (backend BYO) | 米国 | Apache 2.0 |
| **Cody (Sourcegraph)** | コードアシスタント + enterprise | ✓ / 商用 | ✓ (enterprise self-host) | 米国 | Apache 系 |
| **FullSense** (llmesh+llive+llove) | Agent framework + Hub + 観測 + IDE | ✓ | ✓ | 個人 (中立) | Apache 2.0 + Commercial dual |

## 2. 機能比較 matrix

### 2.1 Core LLM Gateway 機能

| 機能 | LiteLLM | Portkey | Helicone | Langfuse | OpenRouter | Tabby | Continue | Cody | **FullSense** |
|---|---|---|---|---|---|---|---|---|---|
| 100+ LLM 対応 | ✓ | ✓ | ✓ | n/a | ✓ (200+) | △ | ✓ | △ | △ (主要 + 中国 LLM ファースト) |
| 中国 LLM ファーストクラス | ✗ | ✗ | ✗ | ✗ | △ | ✗ | △ | ✗ | **✓** |
| OpenAI 互換 API | ✓ | ✓ | ✓ | n/a | ✓ | △ | ✓ | ✓ | **✓** |
| MCP プロトコル | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | **✓** |
| semantic cache | ✗ | **✓ (差別化)** | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | △ (Phase 2) |
| Rate limit / Quota | ✓ | ✓ | ✓ | △ | ✓ | △ | ✗ | ✓ | △ (Phase 2) |
| Fallback / Retry | ✓ | ✓ | ✓ | ✗ | ✓ | ✗ | ✗ | ✓ | △ |

### 2.2 Observability + Audit

| 機能 | LiteLLM | Portkey | Helicone | Langfuse | OpenRouter | Tabby | Continue | Cody | **FullSense** |
|---|---|---|---|---|---|---|---|---|---|
| Trace / Span 記録 | △ | ✓ | ✓ | **✓ (主役)** | ✗ | △ | ✗ | ✓ | ✓ (llive) |
| 監査ログ | △ | ✓ | ✓ | ✓ | ✗ | △ | ✗ | ✓ | **✓ (HITL Approval Bus)** |
| 出典追跡 | ✗ | ✗ | ✗ | △ | ✗ | ✗ | ✗ | △ | **✓ (OKA-FX)** |
| 思考因子可視化 | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | **✓ (10 因子、独自)** |
| Annotation Channel | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | **✓ (IND-04)** |
| PII redaction | △ | ✓ | △ | △ | ✗ | △ | ✗ | △ | △ (presidio extras) |

### 2.3 Agent Framework / 思考レイヤ

| 機能 | LiteLLM | Portkey | Helicone | Langfuse | OpenRouter | Tabby | Continue | Cody | **FullSense** |
|---|---|---|---|---|---|---|---|---|---|
| Agent loop | ✗ | ✗ | ✗ | ✗ | ✗ | △ | △ | △ | **✓ (llive 6 stage)** |
| 記憶層 (multi-layer) | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | △ | **✓ (4 層 L1-L4)** |
| HITL Approval Bus | ✗ | △ (guardrails) | ✗ | ✗ | ✗ | ✗ | ✗ | △ | **✓ (llive 主役)** |
| TRIZ / OKA-FX / VRB-FX | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | **✓ (独自)** |
| Brief API | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | **✓ (LLIVE-002 resolved)** |
| Grounding (RAD / 出典) | ✗ | ✗ | ✗ | △ | ✗ | △ | ✗ | △ | **✓ (~5 万件 RAG)** |

### 2.4 産業 / 規制対応

| 機能 | LiteLLM | Portkey | Helicone | Langfuse | OpenRouter | Tabby | Continue | Cody | **FullSense** |
|---|---|---|---|---|---|---|---|---|---|
| SPC (統計的工程管理) | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | **✓ (llmesh)** |
| MQTT / OPC-UA 直結 | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | **✓ (llmesh)** |
| 国産 silicon (Ascend/Cambricon) | ✗ | ✗ | ✗ | ✗ | ✗ | △ (Ollama 経由) | △ | ✗ | △ (Phase 2) |
| 中国 AI 弁法対応 docs | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | **✓ (cn-internal-use)** |
| EU AI Act 対応 docs | ✗ | △ | ✗ | △ | ✗ | ✗ | ✗ | △ | △ (Week 4 整備) |
| 日本金融庁 AI ディスカッションペーパー対応 | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | △ (Week 4 整備) |
| データ越境管理 (on-prem 完結) | △ (self-host で) | △ (self-host で) | △ (self-host で) | ✓ (EU 拠点 + self-host) | ✗ | ✓ (air-gapped) | △ | ✓ (Enterprise self-host) | **✓ (架構レベル)** |

### 2.5 配布 / Marketplace 非依存

| 機能 | LiteLLM | Portkey | Helicone | Langfuse | OpenRouter | Tabby | Continue | Cody | **FullSense** |
|---|---|---|---|---|---|---|---|---|---|
| VS Code Marketplace | ✗ | ✗ | ✗ | ✗ | ✗ | ✓ | ✓ | ✓ | △ (Week 4) |
| **VSIX 直接配布** | n/a | n/a | n/a | n/a | n/a | △ | △ | △ | **✓ (設計時から必須)** |
| **gitee mirror** | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | △ (Week 4 整備) |
| **依存 origin manifest** | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | **✓ (deps --audit)** |
| pip wheel オフライン install | ✓ | △ | ✓ | ✓ | ✗ | △ | ✗ | ✗ | **✓** |
| Docker image オフライン | ✓ | ✓ | ✓ | ✓ | ✗ | ✓ | ✗ | ✓ | **✓** |

### 2.6 IDE / UI 統合

| 機能 | LiteLLM | Portkey | Helicone | Langfuse | OpenRouter | Tabby | Continue | Cody | **FullSense** |
|---|---|---|---|---|---|---|---|---|---|
| TUI (terminal) | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | **✓ (llove)** |
| VS Code 拡張 | ✗ | ✗ | ✗ | ✗ | ✗ | ✓ | **✓** | **✓** | △ (Week 4 α) |
| JetBrains plugin | ✗ | ✗ | ✗ | ✗ | ✗ | ✓ | ✓ | ✓ | △ (Phase 2) |
| Neovim plugin | ✗ | ✗ | ✗ | ✗ | ✗ | ✓ | △ | ✓ | △ (Phase 2) |
| Web dashboard | ✓ | ✓ | ✓ | **✓** | ✓ | ✓ | ✗ | ✓ | ✗ (TUI のみ) |
| HTML export | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | **✓ (llove)** |

## 3. Layer 別総合 fit (戦略 PART 1 L1-L5)

### L1 (規制対応大手、東アジア中心)

| 製品 | fit | 理由 |
|---|---|---|
| **FullSense** | **★★★★★** | 中国 LLM ファースト + 規制 docs + Marketplace 非依存 + HITL + 監査 |
| Langfuse | ★★★ | EU 拠点で中立性高、observability 強、ただし中国 LLM / 規制 docs 弱 |
| Tabby | ★★★ | air-gapped 強、ただしコード補完中心、規制 docs / 中国 LLM 弱 |
| Portkey | ★★ | 2026/03 OSS 化で底上げ、ただし米国製で中国 LLM ファーストでない |
| LiteLLM | ★ | 2026/03 supply chain attack で信頼性疑問 |
| Helicone / Continue / Cody | ★ | 米国製、規制対応 docs 無し |
| OpenRouter | ✗ | SaaS、L1 で使用不可 |

### L2 (制裁対象国)

| 製品 | fit | 理由 |
|---|---|---|
| **FullSense** | **★★★★** | 中立 OSS、地政学的不偏 |
| Tabby | ★★ | OSS だが米国会社製で地政学的曖昧 |
| Langfuse | ★★ | EU 拠点 + OSS、中立性まあまあ |
| 他 (米国系) | ★ | 二次制裁リスクで使えない可能性 |

### L3 (中国系サプライチェーン)

L1 と同じ評価 (FullSense 強い)。

### L4 (中立志向国)

| 製品 | fit | 理由 |
|---|---|---|
| **FullSense** | **★★★★** | ベンダロックイン回避 + データ主権 |
| Langfuse | ★★★★ | EU 中立、observability で先行 |
| Tabby | ★★★★ | OSS air-gapped、コード補完で先行 |
| Portkey | ★★★ | 2026/03 OSS 化で底上げ |
| LiteLLM | ★★ | supply chain で揺れた |

### L5 (EU 規制対応層)

| 製品 | fit | 理由 |
|---|---|---|
| Langfuse | ★★★★★ | EU 拠点、GDPR 親和、observability 最強 |
| **FullSense** | **★★★★** | EU AI Act 対応 docs + on-prem 完結 |
| Tabby | ★★★ | air-gapped、ただし EU AI Act docs 弱 |
| 他 | ★★ | |

### L6 (北米 SEC 重視)

| 製品 | fit | 理由 |
|---|---|---|
| Tabby | ★★★★★ | 米国 SEC 市場で支配的 |
| Cody | ★★★★★ | 同上、enterprise 強 |
| Continue | ★★★★ | 同上、開発者層 |
| Portkey | ★★★★ | 2026/03 OSS 化で底上げ |
| Helicone / Langfuse | ★★★ | observability 領域 |
| LiteLLM | ★★ | supply chain 後 |
| **FullSense** | **★★** | 無理に取らない、棲み分け |

### L7 (一般 OSS 開発者)

Tabby / Continue.dev / LiteLLM が支配的、FullSense は採用ファネルとして共存。

## 4. 致命的な差別化マトリックス (FullSense しか持たない機能)

| 機能 | FullSense | 他 8 製品 |
|---|---|---|
| 中国 LLM ファーストクラス | **✓** | (どこも ✗) |
| MCP プロトコル準拠 | **✓** | (どこも ✗) |
| HITL Approval Bus (architecture level) | **✓** | (どこも ✗ または △) |
| 4 層メモリ + 10 思考因子 | **✓** | (どこも ✗) |
| TRIZ / OKA-FX / VRB-FX 思考フレーム | **✓** | (どこも ✗) |
| Brief API (LLIVE-002) | **✓** | (どこも ✗) |
| SPC + MQTT + OPC-UA 産業 IoT 直結 | **✓** | (どこも ✗) |
| 依存 origin manifest (`deps --audit`) | **✓ (Week 2)** | (どこも ✗) |
| 中国 AI 弁法対応 docs | **✓ (本日 commit)** | (どこも ✗) |
| TUI 主体 (Marketplace 不要) | **✓** | (どこも ✗) |
| HTML export (llove) | **✓** | (どこも ✗) |
| Annotation Channel (IND-04) | **✓** | (どこも ✗) |
| Apache 2.0 + Commercial dual | **✓** | (Cody が近いが商用必須) |

→ **13 機能で FullSense が唯一**. これは "**競合不在**" を構造的に確立できる
領域. L1-L3 で確実に勝てる根拠.

## 5. Portkey 2026/03 OSS 化への対応戦略

戦略思索 PART 2 章 4.1 で Portkey を "SaaS が main" と評価していた前提が破綻したので、
修正:

| 変更前評価 | 変更後評価 |
|---|---|
| L6 で強敵 | L6 で **更に強い競合**、北米 SEC で支配的に |
| L1-L5 では届かない | **L4-L5 でも一部競合**、Apache 2.0 化で OSS 派にも届く |
| semantic cache は managed のみ | **gateway 含めて OSS、Portkey 真似される可能性** |

### FullSense 側の対応

- **semantic cache を llmesh Phase 2 で実装検討** (Portkey の差別化機能、影響大)
- **依存 origin manifest (`deps --audit`) で差別化深掘り** — Portkey が真似しにくい
  (自社が米国製なので)
- **中国 LLM ファーストクラス + 規制 docs で L1-L3 集中** は変わらず
- **Apache 2.0 + Commercial dual のメリット強化** — Portkey は managed が separate
  license、FullSense は単一 OSS

## 6. Day 3 で得た重要 insight

1. **FullSense が単独で持つ機能は 13 個** — 競合 8 製品 すべて持っていない領域. L1-L3
   で構造的に勝てる根拠が定量化された
2. **L6 (北米 SEC) は Tabby + Cody + Portkey で完全支配**. 無理に取らない判断が確定
3. **Portkey 2026/03 OSS 化が L4-L5 で影響** — semantic cache が真似されると痛い、
   llmesh Phase 2 で対応検討
4. **Langfuse は EU 拠点で L5 で最強競合** — FullSense は L1-L3 集中で棲み分け、
   L5 では特化機能で limited 競合
5. **依存 origin manifest が最強差別化** — 競合 8 製品 全てが米国製、構造的に真似不能
6. **MCP プロトコル準拠は FullSense だけ** — Anthropic 標準なので追従に見えるが、競合
   8 製品 がまだ追従していないので先行優位
7. **HITL Approval Bus が architecture level で実装されているのは FullSense だけ** —
   規制対応企業の必須要件

## 7. Day 4-7 残作業

- **Day 4**: LiteLLM が届かない領域 3 箇所絞り込み (顧客ペルソナ + 競合 matrix から)
- **Day 5**: gap に対する llmesh 現機能 fit gap (4 段階 status taxonomy)
- **Day 6**: 不要 / 過剰 / 不足機能リスト
- **Day 7**: Roadmap 再構築 draft

## 8. 関連 memory / docs

- [[project-llmesh-critical-review]] — Portkey OSS 化で update された
- [[project-market-layers-l1-l5]] — Layer 別 fit 評価
- [[feedback-competitor-benchmark]] — 競合分析方針
- `D:/projects/audit/STRATEGY_EAR_LOCAL_LLM_2026-05-17_PART4_TABBY.md` — Tabby 詳細
- `docs/market/customer-personas.md` (Day 2) — 顧客プロファイル
- `docs/market/reports-2026-05.md` (Day 1) — 業界レポート

## 9. 改訂履歴

- 2026-05-18 — v1 作成 (Day 3 前倒し、約 30 分)
