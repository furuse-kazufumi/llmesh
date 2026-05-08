# MLOps / Edge AI Corpus Queries

```bash
python tools/collect_image_papers.py --source arxiv \
    --query "edge llm inference quantization" --max-results 100 \
    --out docs/papers/mlops_corpus/arxiv_edge_llm.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "onnx runtime tflite micro" --max-results 50 \
    --out docs/papers/mlops_corpus/arxiv_runtimes.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "model compression distillation" --max-results 50 \
    --out docs/papers/mlops_corpus/arxiv_compression.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "mlops monitoring drift detection" --max-results 50 \
    --out docs/papers/mlops_corpus/arxiv_drift.jsonl

python tools/collect_image_papers.py --source semantic_scholar \
    --query "federated learning industrial" --max-results 50 \
    --out docs/papers/mlops_corpus/s2_fl.jsonl
```

トピック: `edge`, `quantization`, `distillation`, `onnx`, `tflite`,
`mlops`, `drift_detection`, `federated_learning`.
