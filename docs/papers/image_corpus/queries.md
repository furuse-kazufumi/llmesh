# 標準クエリセット — 精密工学会 4 論文用

各論文ごとに最低 3 つのクエリを実行することを推奨。
取得後は自動トピック分類で `_by_topic/` 配下に再編成される。

## P1: SpatialSummarizer

```bash
python tools/collect_image_papers.py --source arxiv \
    --query "point cloud summarization" \
    --max-results 50 --out docs/papers/image_corpus/p1_pointcloud.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "depth scene description" \
    --max-results 50 --out docs/papers/image_corpus/p1_depth_scene.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "vision language model edge" \
    --max-results 50 --out docs/papers/image_corpus/p1_vlm_edge.jsonl
```

## P2: ImageFirewall

```bash
python tools/collect_image_papers.py --source arxiv \
    --query "image privacy filter" \
    --max-results 50 --out docs/papers/image_corpus/p2_privacy.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "face anonymization redaction" \
    --max-results 50 --out docs/papers/image_corpus/p2_face_anon.jsonl

python tools/collect_image_papers.py --source semantic_scholar \
    --query "differential privacy image" \
    --max-results 50 --out docs/papers/image_corpus/p2_dp_image.jsonl
```

## P3: AOI + LLM 診断

```bash
python tools/collect_image_papers.py --source arxiv \
    --query "automated optical inspection deep learning" \
    --max-results 100 --out docs/papers/image_corpus/p3_aoi.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "manufacturing defect detection language model" \
    --max-results 50 --out docs/papers/image_corpus/p3_defect_llm.jsonl

python tools/collect_image_papers.py --source semantic_scholar \
    --query "MVTec anomaly detection" \
    --max-results 50 --out docs/papers/image_corpus/p3_mvtec.jsonl
```

## P4: DVS（イベントカメラ）

```bash
python tools/collect_image_papers.py --source arxiv \
    --query "event camera industrial inspection" \
    --max-results 50 --out docs/papers/image_corpus/p4_dvs_industrial.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "dynamic vision sensor anomaly" \
    --max-results 50 --out docs/papers/image_corpus/p4_dvs_anomaly.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "neuromorphic vision precision" \
    --max-results 50 --out docs/papers/image_corpus/p4_neuromorphic.jsonl
```

## トピック横断クエリ

```bash
python tools/collect_image_papers.py --source arxiv \
    --query "industrial computer vision local llm" \
    --max-results 100 --out docs/papers/image_corpus/cross_industrial_llm.jsonl
```

## 一括取得（全クエリ）

```bash
bash docs/papers/image_corpus/fetch_all.sh    # 別途用意
```

## 推奨実行頻度

- 初回: すべて実行
- 週次: P3 / P4 を再取得（最新研究）
- 月次: 横断クエリを再取得
- 論文投稿前: 全クエリ再取得 + 直近 3 ヶ月の論文を厳選
