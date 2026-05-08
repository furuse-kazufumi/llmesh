# 大量論文コーパス収集ガイド（v2.8 — 各分野 10,000+ 件目標）

LLMesh の RAD（分野別論文コーパス）は **arXiv 単独では足りない**ため、
**OpenAlex / CrossRef / DBLP / PubMed / Semantic Scholar /
HackerNews** など複数ソースを統合して **各分野 10,000+ 件**を収集できる
仕組みを提供します。

## 収集ソースの能力比較（2026 時点）

| ソース | 総レコード数 | 認証 | 1分野上限見込み | 備考 |
|--------|-----------:|----|--------------:|------|
| **OpenAlex** | 245M+ | 不要 | **10,000+** | カーソルベース・最大効率 |
| **CrossRef** | 145M+ | 不要 | 10,000+ | DOI 完備 |
| **Semantic Scholar** | 200M+ | 任意 | 1,000（API 制限） | 引用ネットワーク豊富 |
| **arXiv** | 2.4M+ | 不要 | 数千 | 最新プレプリント |
| **DBLP** | 6M | 不要 | 数千 | CS 領域に特化 |
| **PubMed** | 36M+ | 不要 | 5,000 | 医療専門 |
| **HackerNews** | 数 M | 不要 | 数千 | 実践記事補完 |

合計 **600M+** ユニーク論文・記事から各分野で 10,000 件以上を収集可能。

## 推奨運用フロー

### 1 分野で 10,000 件
```bash
python tools/bulk_corpus_collector.py \
    --domain industrial_iot \
    --target 10000 \
    --queries "predictive maintenance" "modbus iot" "OPC-UA digital twin" "MT method anomaly"

python tools/community_corpus_collector.py \
    --source crossref --query "predictive maintenance" \
    --target 5000 --out docs/papers/industrial_iot_corpus/crossref_pdm.jsonl

python tools/community_corpus_collector.py \
    --source dblp --query "industrial iot" \
    --target 3000 --out docs/papers/industrial_iot_corpus/dblp_iiot.jsonl
```

### 9 分野一気
```bash
python tools/bulk_corpus_collector.py --all --target 10000
```

実行時間目安：約 6〜12 時間（API レート制限のため）。

### 医療専門
```bash
python tools/community_corpus_collector.py \
    --source pubmed --query "DICOM federated learning" \
    --target 5000 --out docs/papers/medical_corpus/pubmed_dicom.jsonl
```

### コミュニティ補完
```bash
# HackerNews — 実践記事
python tools/community_corpus_collector.py \
    --source hn --query "industrial iot LLM" \
    --target 1000 --out docs/papers/industrial_iot_corpus/hn_practice.jsonl
```

## 重複除去

各 JSONL に `title_hash` フィールドが含まれており、`bulk_corpus_collector.py`
の `dedupe_records()` を使って統合時に重複除去できます：

```python
from tools.bulk_corpus_collector import dedupe_records
import json
from pathlib import Path

records = []
for f in Path("docs/papers/industrial_iot_corpus").glob("*.jsonl"):
    with open(f, encoding="utf-8") as fp:
        for line in fp:
            records.append(json.loads(line))

unique = dedupe_records(records)
print(f"Deduped: {len(records)} → {len(unique)}")
```

## 想定収集量サマリー（各分野）

| 分野 | OpenAlex | arXiv | CrossRef | DBLP/Pubmed | HN | 合計目標 |
|------|--------:|-----:|--------:|---------:|--:|------:|
| image | 5,000 | 2,000 | 3,000 | 1,000 (DBLP) | 500 | 11,500 |
| security | 5,000 | 2,000 | 3,000 | 1,000 | 500 | 11,500 |
| industrial_iot | 5,000 | 2,000 | 3,000 | 1,000 | 500 | 11,500 |
| mlops | 5,000 | 2,000 | 3,000 | 1,000 | 500 | 11,500 |
| game_dev | 4,000 | 1,500 | 2,500 | 800 | 500 | 9,300 |
| medical | 4,000 | 1,500 | 2,500 | 5,000 (PubMed) | 200 | 13,200 |
| automotive | 4,000 | 1,500 | 2,500 | 800 | 300 | 9,100 |
| infrastructure | 3,000 | 1,000 | 2,000 | 600 | 200 | 6,800 |
| robotics | 5,000 | 2,000 | 3,000 | 1,000 | 500 | 11,500 |

**全分野合計目標: ≈ 95,900 ユニーク論文 / 記事**

## CI / cron での日次運用

```yaml
# .github/workflows/corpus-refresh.yml
on:
  schedule:
    - cron: "0 3 * * 1"   # 毎週月曜 03:00 UTC
jobs:
  refresh:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
      - run: pip install -e .
      - run: python tools/bulk_corpus_collector.py --all --target 10000
      - run: |
          git add docs/papers/
          git commit -m "auto: refresh corpus $(date -u +%Y-%m-%d)"
          git push
```

## ストレージ見積もり

- 平均レコードサイズ: 2 KB（JSONL）
- 100,000 件 ≈ 200 MB
- 年次分類後（重複除去）: ~150 MB
- gzip 圧縮で ~30 MB

## ライセンス・倫理

- API 利用規約遵守（特に CrossRef polite pool / NCBI 3 req/s）
- 収集はメタデータのみ（タイトル + アブストラクト）
- 営利利用時は各 API の Commercial license を確認
- robots.txt 尊重、wholesale scraping は禁止

## 関連ドキュメント

- 9 分野インデックス: [`CORPUS_INDEX.md`](CORPUS_INDEX.md)
- 各分野クエリ: 各 `*_corpus/queries.md`
- 元の汎用コレクター: [`../../tools/collect_image_papers.py`](../../tools/collect_image_papers.py)
- 大量コレクター: [`../../tools/bulk_corpus_collector.py`](../../tools/bulk_corpus_collector.py)
- コミュニティコレクター: [`../../tools/community_corpus_collector.py`](../../tools/community_corpus_collector.py)
