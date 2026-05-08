# LLMesh Image-Processing Paper Corpus（RAD 形式）

`raptor /hacker-corpus` 相当の **画像処理論文コーパス**。
arXiv / Semantic Scholar から関連論文のメタデータを収集し、
LLMesh の `corpus2skill` で階層スキル化することを想定。

## 構造

```
docs/papers/image_corpus/
├── README.md              # このファイル
├── queries.md             # 標準クエリセット
├── arxiv_*.jsonl          # arXiv 由来コーパス（クエリ別）
├── s2_*.jsonl             # Semantic Scholar 由来
└── _by_topic/             # 自動分類後（topics ベース）
    ├── AOI.jsonl
    ├── DVS.jsonl
    ├── depth.jsonl
    ├── anomaly_detection.jsonl
    ├── manufacturing.jsonl
    ├── privacy.jsonl
    ├── ocr.jsonl
    ├── medical.jsonl
    ├── segmentation.jsonl
    ├── transformer.jsonl
    ├── multimodal.jsonl
    ├── llm_integration.jsonl
    └── edge.jsonl
```

## 標準クエリセット（精密工学会 4 論文向け）

| Paper | クエリ | カテゴリ |
|-------|------|--------|
| P1 (SpatialSummarizer) | `point cloud summarization llm` | cs.CV, eess.IV |
| P1 | `depth camera scene description` | cs.CV |
| P2 (ImageFirewall) | `image privacy filtering pii` | cs.CV, cs.CR |
| P2 | `face anonymization detection` | cs.CV |
| P3 (AOI-LLM) | `automated optical inspection deep learning` | cs.CV, cs.LG |
| P3 | `industrial defect detection llm` | cs.CV |
| P4 (DVS) | `event camera anomaly detection` | cs.CV |
| P4 | `dynamic vision sensor industrial` | cs.CV, cs.RO |

## 取得スクリプト

```bash
python tools/collect_image_papers.py \
    --source arxiv \
    --query "automated optical inspection" \
    --max-results 100 \
    --out docs/papers/image_corpus/arxiv_aoi.jsonl

python tools/collect_image_papers.py \
    --source arxiv \
    --query "event camera industrial" \
    --max-results 50 \
    --out docs/papers/image_corpus/arxiv_dvs.jsonl
```

## レコードスキーマ（JSONL）

```json
{
  "id": "arxiv:2401.12345",
  "title": "...",
  "abstract": "...",
  "authors": ["A. Author", "B. Author"],
  "year": 2024,
  "categories": ["cs.CV", "cs.LG"],
  "url": "https://arxiv.org/abs/2401.12345",
  "source": "arxiv",
  "topics": ["AOI", "anomaly_detection"],
  "fetched_at": "2026-05-07T..."
}
```

## トピック自動分類

`tools/collect_image_papers.py` は title + abstract を 35 種以上の
キーワードルールで照合し、以下のトピックタグを自動付与：

- `AOI` / `DVS` / `depth`
- `anomaly_detection` / `manufacturing`
- `privacy` / `ocr` / `medical`
- `object_detection` / `segmentation`
- `transformer` / `multimodal`
- `llm_integration` / `edge`

## ライセンス・倫理

- arXiv API: 利用無料、レート制限あり（リクエスト間 3 秒推奨）
- Semantic Scholar API: 利用無料、API キー任意
- 収集したアブストラクトは **論文の引用文脈のみ** に使用
- フルテキスト PDF は本コーパスには含めない（arXiv 直接取得）

## RAD（Research Aggregation Directory）統合

LLMesh の `corpus2skill` 機能で：
```bash
python -m llmesh corpus2skill \
    --source docs/papers/image_corpus/ \
    --name image_processing \
    --hierarchy true
```
を実行すると、トピック別にスキルが階層化され、
`/sourcehunt` などからヒントとして参照可能になる。

## CI 統合（任意）

GitHub Actions で日次クローラーを動かす案：
```yaml
- name: Update image-paper corpus
  run: |
    python tools/collect_image_papers.py --source arxiv \
        --query "industrial inspection llm" --max-results 50 \
        --out docs/papers/image_corpus/arxiv_daily.jsonl
```

ただし論文メタデータは公開でも、頻繁な API 呼び出しはマナー違反のため
週次以下を推奨。
