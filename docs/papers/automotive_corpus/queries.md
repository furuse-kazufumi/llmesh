# Automotive / ADAS Corpus Queries

```bash
python tools/collect_image_papers.py --source arxiv \
    --query "controller area network anomaly" --max-results 100 \
    --out docs/papers/automotive_corpus/arxiv_can.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "autosar safety verification" --max-results 50 \
    --out docs/papers/automotive_corpus/arxiv_autosar.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "obd-ii diagnostic deep learning" --max-results 50 \
    --out docs/papers/automotive_corpus/arxiv_obd.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "adas perception event camera" --max-results 50 \
    --out docs/papers/automotive_corpus/arxiv_adas_dvs.jsonl

python tools/collect_image_papers.py --source semantic_scholar \
    --query "v2x communications security" --max-results 50 \
    --out docs/papers/automotive_corpus/s2_v2x.jsonl
```

トピック: `can`, `autosar`, `obd`, `adas`, `v2x`, `automotive_security`,
`uds`, `iso26262`.
