# Medical Imaging Corpus Queries

```bash
python tools/collect_image_papers.py --source arxiv \
    --query "medical imaging large language model" --max-results 100 \
    --out docs/papers/medical_corpus/arxiv_med_llm.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "dicom federated learning" --max-results 50 \
    --out docs/papers/medical_corpus/arxiv_dicom_fl.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "ecg time series anomaly" --max-results 50 \
    --out docs/papers/medical_corpus/arxiv_ecg.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "hipaa privacy preserving radiology" --max-results 50 \
    --out docs/papers/medical_corpus/arxiv_hipaa.jsonl

python tools/collect_image_papers.py --source semantic_scholar \
    --query "fhir clinical decision support" --max-results 50 \
    --out docs/papers/medical_corpus/s2_fhir.jsonl
```

トピック: `medical`, `dicom`, `fhir`, `radiology`, `ecg`, `pathology`,
`hipaa`, `clinical_nlp`.
