# LiteLLM が届かない 3 領域 — gap analysis (Day 4)

> 2026-05-18 作成 (Day 4 前倒し). Day 1-3 (業界レポート + 顧客ペルソナ + 競合 matrix)
> を統合し、LiteLLM (現状 OSS LLM gateway の事実上標準) が **構造的にカバーできない
> 3 領域** を絞り込む. これが llmesh の集中投資領域.

## 1. 絞り込み方法

| Input | 出典 |
|---|---|
| 顧客 pain point | `customer-personas.md` Day 2 |
| 競合機能ギャップ | `competitor-matrix.md` Day 3 |
| 市場 layer | `[[project-market-layers-l1-l5]]` |
| LiteLLM の弱点 | 業界レポート (2026/03 supply chain attack 等) |

LiteLLM 単体で行えること + LiteLLM が補完できる範囲を除外し、構造的に **不可能** な
領域に絞り込む.

## 2. 領域 1: EAR-clean 検証可能性 (依存 origin 可視化)

### 顧客 pain
- 規制対応大手 (L1) の調達担当者が「ライブラリ origin を自社で検証できるか」を最重要
  評価軸に
- LiteLLM 2026/03 supply chain attack (credential 盗難 malware) で米国製 OSS への
  信頼性が揺らいだ
- BIS が 2025/03 に Entity List 80 entities 追加、地政学的検証性が更に critical

### LiteLLM が構造的に不可能な理由
- LiteLLM は米国 BerriAI 社製品 — **自社が米国製であることが弱点**
- 「依存 origin manifest を出す」という機能は、自社製品の米国製性を可視化する自己
  矛盾になる
- 中立 OSS でないと信頼を取れない

### FullSense の解
- `llmesh deps --audit` 機能 (戦略思索 PART 6 で仕様 draft 完成)
- internal `origins.toml` DB + ユーザ overrides + supply_risk DB
- HTML report (調達担当者向け) + SBOM (CycloneDX/SPDX) export
- 多言語 (ja/en/zh) report

### 優先度: 🔴 最優先 (Week 2 Day 1-7 で α 実装)

## 3. 領域 2: 中国 LLM ファーストクラス + 国産 silicon

### 顧客 pain
- 中国 enterprise (L1) は Qwen / DeepSeek / GLM / Kimi を on-prem 運用が標準
- 2026/04 で中国 LLM が OpenRouter シェア 45% 超 (Day 1 確認)
- LiteLLM の中国 LLM 対応はベストエフォート (OpenAI compatible endpoint 経由)
- 国産 silicon (Ascend / Cambricon / Loongson) との first-class 統合無し

### LiteLLM が構造的に不可能な理由
- 米国製のため、中国 LLM ベンダとの直接協業が地政学的に困難
- 国産 silicon (Ascend / Cambricon) は中国国内サポートが主で、米国 OSS の優先度低
- MindSpore / PaddlePaddle 統合は中国エコシステム内で完結する性格

### FullSense の解
- `llmesh[cn-llm]` extras: Qwen / DeepSeek / GLM / Kimi / Baichuan 公式 API +
  独自機能 first-class
- `llmesh[cn-silicon]` extras: MindSpore / PaddlePaddle 経由で Ascend / Cambricon
  対応 (vLLM-MindSpore Plugin 活用)
- API 差異の正規化 (各社微妙に違う request/response schema を統一)

### 優先度: 🔴 最優先 (Week 2-3 で extras 整備、Week 4 で v3.2.0-rc1 リリース)

## 4. 領域 3: 規制対応 + HITL architecture-level 統合

### 顧客 pain
- 中国 AI 弁法 (社内利用は filing 免除、本日 cn-internal-use.md draft 完成)
- 改正サイバーセキュリティ法 2026/01 (AI 専用条項追加)
- 擬人化対話 AI 弁法 2026/07 施行予定
- EU AI Act / 日本金融庁 AI ディスカッションペーパー 2026/03
- HITL (Human-in-the-loop) Approval Bus が architecture-level で必要
- 監査ログ + 出典追跡が金融庁検査で必須

### LiteLLM が構造的に不可能な理由
- LiteLLM は LLM routing に特化した library、agent framework ではない
- HITL Approval Bus / 出典追跡 / 思考因子 / Annotation Channel 等は範囲外
- 規制対応 docs を米国製 OSS が出すと地政学的色を帯びる

### FullSense の解
- **llive 側**: HITL Approval Bus + SQLite Ledger + OKA-FX 出典追跡 + 4 層メモリ +
  Brief API (LLIVE-002 resolved 5/16)
- **llmesh 側**: `[compliance]` extras (PII redaction + audit log + 出典 channel)
- **規制対応 docs**: ja/en/zh で `docs/regulatory/` 配下 5 本整備 (Week 4)
- **cn-internal-use.md** は本日 commit 済

### 優先度: 🟠 高 (Week 1-4 で並行整備、Phase 2 で完全化)

## 5. 3 領域共通の戦略含意

### TRIZ 観点
全 3 領域とも、**LiteLLM の存在自体 (米国製) が解決の障壁** = 矛盾。LiteLLM が自分の
ライブラリを変えても解決しない、構造的問題。これは TRIZ 1. 分割 (米国製 OSS と
中立 OSS で機能を分離) で対応する.

### クロスドメイン参照
- **Kubernetes → k3s / minikube**: 重さで嫌われ軽量版が普及した先例
- **TensorFlow → JAX**: 機能盛りすぎで研究者が JAX へ
- **Apache → Nginx**: 設定の重さで世代交代
- LiteLLM → FullSense の構図は同じパターンの可能性

### Honest disclosure
- FullSense は個人 OSS で開発者単独、商用サポート無し
- LiteLLM は商用社 + 大コミュニティ
- **規模では負ける、構造的差別化で勝つ**

## 6. 3 領域以外 — 「LiteLLM で十分」な領域

逆に、以下は LiteLLM (+ 他競合) が既に十分カバー、FullSense が無理に参入しない:

| 領域 | LiteLLM/Portkey/Helicone でカバー済 |
|---|---|
| OpenAI / Anthropic / 主要 cloud LLM の OpenAI 互換 routing | ✓ (LiteLLM 100+ models) |
| Rate limit / quota / fallback | ✓ (Portkey 強い) |
| 基本的な observability (trace / span) | ✓ (Phoenix / Langfuse 強い) |
| coding 補完 (IDE 統合) | ✓ (Tabby / Cody / Continue / Copilot) |
| semantic cache (Portkey 2026/03 OSS 化) | △ (Portkey OSS に対応) |

→ これらの領域では FullSense は「劣化版」になる. **集中投資を 3 領域に絞る**.

## 7. Day 4 で得た重要 insight

1. **LiteLLM が構造的に届かない 3 領域**: EAR-clean / 中国 LLM ファーストクラス /
   規制対応 + HITL. これは LiteLLM が機能を追加しても解決しない (自社が米国製で
   あることが障壁) — **構造的差別化**
2. **3 領域全てで FullSense が単独実装可能** — 個人 OSS で中立、地政学的不偏、規制
   対応で速い
3. **3 領域以外は LiteLLM / 競合で十分** → FullSense は「LiteLLM の劣化版」に
   ならないよう、汎用 LLM routing は llmesh-core の最低限に絞る
4. **戦略集中の根拠が data 化** — 顧客ペルソナ (5 業界 × 5 規模) と競合 matrix
   (13 機能で唯一) から導出された、感覚ではなく定量的な絞り込み

## 8. 関連 docs

- `docs/market/customer-personas.md` (Day 2)
- `docs/market/competitor-matrix.md` (Day 3)
- `D:/projects/audit/STRATEGY_EAR_LOCAL_LLM_2026-05-17_PART6_DEPS_AUDIT.md` — 領域 1
  の機能仕様
- [[project-cn-ai-compliance-internal-use-exemption]] — 領域 3 の根拠
- `D:/projects/fullsense/docs/regulatory/cn-internal-use.md` (commit 74cfb3c) — 領域 3 docs
