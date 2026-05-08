# Industrial IoT Corpus Queries

```bash
python tools/collect_image_papers.py --source arxiv \
    --query "predictive maintenance deep learning" --max-results 100 \
    --out docs/papers/industrial_iot_corpus/arxiv_pdm.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "Mahalanobis Taguchi anomaly manufacturing" --max-results 50 \
    --out docs/papers/industrial_iot_corpus/arxiv_mt.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "OPC-UA digital twin" --max-results 50 \
    --out docs/papers/industrial_iot_corpus/arxiv_opcua.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "industrial internet of things llm" --max-results 50 \
    --out docs/papers/industrial_iot_corpus/arxiv_iiot_llm.jsonl

python tools/collect_image_papers.py --source semantic_scholar \
    --query "modbus EtherCAT real-time" --max-results 50 \
    --out docs/papers/industrial_iot_corpus/s2_fieldbus.jsonl
```

トピック: `pdm`, `digital_twin`, `mt_method`, `manufacturing`, `opcua`,
`modbus`, `time_series_anomaly`.
