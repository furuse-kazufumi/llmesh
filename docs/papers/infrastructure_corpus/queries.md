# Critical Infrastructure Corpus Queries

```bash
python tools/collect_image_papers.py --source arxiv \
    --query "dnp3 scada anomaly detection" --max-results 100 \
    --out docs/papers/infrastructure_corpus/arxiv_dnp3.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "iec 61850 substation cybersecurity" --max-results 50 \
    --out docs/papers/infrastructure_corpus/arxiv_iec61850.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "smart grid demand response" --max-results 50 \
    --out docs/papers/infrastructure_corpus/arxiv_smart_grid.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "water treatment iot anomaly" --max-results 50 \
    --out docs/papers/infrastructure_corpus/arxiv_water.jsonl

python tools/collect_image_papers.py --source semantic_scholar \
    --query "bacnet building automation" --max-results 50 \
    --out docs/papers/infrastructure_corpus/s2_bacnet.jsonl
```

トピック: `scada`, `dnp3`, `iec61850`, `smart_grid`, `water_treatment`,
`bacnet`, `nerc_cip`, `nis2`.
