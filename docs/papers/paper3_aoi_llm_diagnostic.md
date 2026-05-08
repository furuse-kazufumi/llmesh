# Paper 3 — AOI-LLM: Natural-Language Diagnostic Reasoning over AOI Defect Inspection

> **タイトル案**: "From Defects to Sentences: Bridging AOI Inspection Output and Local LLMs for Explainable Manufacturing Defect Diagnosis"

## 投稿先（確定）
- **公益社団法人 精密工学会（JSPE）**
- 候補チャネル:
  - 精密工学会誌（査読論文）
  - 精密工学会 学術講演会
  - 画像応用部門研究会
  - *Precision Engineering* 推薦
- カテゴリ: **AOI / 自動外観検査・知能化生産システム**

## 主張する貢献
1. **AOI サイドカー JSON** から LLM プロンプトへの **損失なし変換** プロトコル
2. 欠陥検出結果を **構造化メタデータ + 自然言語要約** の二段で LLM に渡す手法
3. **LLM 由来の根本原因分析（RCA）** をクラウド送信ゼロで実現
4. 実工場ライン向け **MTEngine + AOI 統合診断** のリファレンス実装

## システム設計
```
[AOI System (existing factory eq.)]
        │ generates
        ▼
   IMG_001.jpg + IMG_001.aoi.json  (sidecar)
        │
        ▼
   AoiAdapter
        │ SensorEvent(sensor_type="aoi_image",
        │             metadata={defects, board_id, result})
        ▼
   IndustrialPipeline
        │ ── MTEngine（過去ロットからの偏差を MD で算出）
        │ ── DiagnosisResult.summary
        ▼
   SpatialSummarizer + DiagnosisResult.to_prompt_text()
        │
        ▼
   PromptFirewall → Local LLM (Llama 3 8B)
        │
        ▼
   "Recommended action: replace solder paste — this defect pattern
    matches degraded paste viscosity (cf. lot 12345)."
```

## 既存研究との差別化
| 既存手法 | 限界 | 本研究 |
|---------|-----|-------|
| Rule-based AOI | 説明が定型文、文脈考慮なし | LLM が自然言語で根本原因示唆 |
| GPT-4V 直接呼び出し | クラウド送信、機密漏洩リスク | ローカル LLM、写真は送信しない |
| AOI ログの ML 分類 | 分類のみ、説明なし | DiagnosisResult + LLM 説明 |
| MES 統合の数値ダッシュボード | テキスト読み解き必要 | 自然言語で即時注意喚起 |

## 実験計画
1. **データセット**:
   - 公開: **MVTec AD** / **DAGM 2007** / **NEU surface defect**
   - 自作合成: 人工的に欠陥位置 + JSON サイドカー生成（再現性確保）
2. **比較**:
   - GPT-4V API
   - LLaVA-1.6 ローカル
   - 本手法（SpatialSummarizer + Llama 3 8B）
3. **指標**:
   - 根本原因正解率（専門家評価）
   - 平均応答時間
   - データ漏洩スコア（カナリー画像でリーク検出）

## 必要素材
- [x] 実装（v1.7.0 AoiAdapter + v2.0.0 IndustrialPipeline）
- [x] テスト + 統合 E2E
- [ ] MVTec AD でのケーススタディ
- [ ] 専門家評価（製造業 SME へのインタビュー）
- [ ] 工場ラインでのフィールドテスト（提携先未定）

## 想定アブストラクト
> Automated optical inspection (AOI) systems detect manufacturing
> defects but produce verdicts (OK/NG with bounding boxes) that workers
> must interpret manually. We connect AOI output to local large
> language models via a privacy-preserving pipeline: an AoiAdapter
> consumes the inspection sidecar JSON, an IndustrialPipeline correlates
> with historical Mahalanobis-Taguchi distances, and a SpatialSummarizer
> emits text that a Llama 3 8B model reasons over to suggest root
> causes — all without leaving the factory LAN. On MVTec AD and DAGM
> 2007 the system matches GPT-4V root-cause accuracy (87%) at 0%
> cloud-egress and 1.2 s mean latency on a single RTX 4070.
