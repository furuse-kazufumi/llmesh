# Paper 2 — ImageFirewall: Multi-Stage Privacy Filtering for Industrial Vision Inputs

> **タイトル案**: "ImageFirewall: A Multi-Stage Privacy Pipeline for Industrial Vision Inputs to Local LLMs"

## 投稿先（確定）
- **公益社団法人 精密工学会（JSPE）**
- 候補チャネル:
  - 精密工学会誌（査読論文）
  - 精密工学会 学術講演会
  - *Precision Engineering*（国際分科会推薦）
- カテゴリ: **画像計測・画像応用（セキュリティ・プライバシー横断）**

## 主張する貢献
1. **L0–L4 4 段階分類** に基づく画像入力のフェイルクローズ・ファイアウォール
2. **EXIF/IPTC/XMP 完全除去** + **顔/ナンバープレート自動マスク**（D-4 連動）
3. **OCR を経由した PII 検出**（画面キャプチャ内のメールアドレス・電話番号等）
4. ローカル LLM への入力直前で BLOCK / SUMMARIZE / PASS の 3 通り処理を強制

## 既存研究との差別化
| 既存手法 | 限界 | 本研究 |
|---------|-----|-------|
| Microsoft Presidio | テキスト中心、画像未対応 | 画像 + OCR + メタデータ統合 |
| Apple Privacy Mirror | クラウド前提 | 完全ローカル LLM ペアリング |
| Differential Privacy 画像 | 統計集計のみ、リアルタイム不可 | フレーム単位リアルタイム |
| 一般 EXIF 除去 | メタデータのみ、ピクセル PII 残存 | メタ + ピクセル + テキスト統合 |

## システム設計
```
Image bytes
   │
   ├── Stage 1: MetadataStripper (EXIF/IPTC/XMP 完全除去)
   │
   ├── Stage 2: ContentClassifier
   │       ├── face detector → L4
   │       ├── plate detector → L4
   │       └── default       → L0/L1
   │
   ├── Stage 3: OCR + PII detector (Presidio integration)
   │       └── L3 if any sensitive token detected
   │
   └── Stage 4: Decision
           L0/L1 → pass (description-only via SpatialSummarizer)
           L3    → SUMMARIZE (face mosaic + reduced text)
           L4    → BLOCK (no LLM call)
```

## 実験計画
1. **データセット**:
   - **CelebA**（顔検出ベンチマーク）
   - **CCPD**（中国ナンバープレート）
   - **DocPII**（合成 PII 文書）
   - 合成 AOI 画像（既存 LLMesh `tests/synthetic/`）
2. **指標**:
   - Recall（PII 検出漏れ率）≤ 0.1%
   - Precision ≥ 95%
   - 単一画像処理 ≤ 200 ms（Raspberry Pi 5）
3. **アブレーション**: 各 Stage を個別に無効化し、L4 漏出率を測定

## 実装計画
- v3.0 で `llmesh/privacy/image_firewall_v2.py` を実装（既存 ImageFirewall を拡張）
- Presidio (E-2.1) と onnxruntime (E-7.1) を optional 依存として統合
- D-4 系要件（FaceFirewall / LicensePlateRedactor / ScreenContentFirewall）を実装

## 必要素材
- [x] 設計（REQUIREMENTS.md Volume D-4）
- [x] 既存 ImageFirewall（v1.2.0、基礎部分）
- [ ] Stage 2 実装（YOLO-Face / EasyOCR）
- [ ] CelebA / CCPD ベンチマーク
- [ ] アブレーション研究

## 想定アブストラクト
> We propose ImageFirewall, a fail-closed multi-stage privacy gate that
> processes every image before it reaches a local industrial LLM. Stages
> include EXIF/IPTC/XMP removal, face and license-plate detection,
> OCR-based PII detection, and final L0–L4 routing that either passes a
> textual SpatialSummarizer description, summarises with mosaicing, or
> blocks entirely. We evaluate against CelebA, CCPD, and synthesised
> industrial PII documents, achieving 99.9% recall on PII while
> sustaining sub-200 ms latency on a Raspberry Pi 5. The system is the
> first to combine pixel-level, OCR-level, and metadata-level filtering
> into one fail-closed pipeline targeted at on-device industrial LLMs.
