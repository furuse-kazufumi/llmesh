# Security Corpus Queries

```bash
python tools/collect_image_papers.py --source arxiv \
    --query "industrial control system security" --max-results 100 \
    --out docs/papers/security_corpus/arxiv_ics_security.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "llm prompt injection defense" --max-results 50 \
    --out docs/papers/security_corpus/arxiv_prompt_injection.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "differential privacy edge" --max-results 50 \
    --out docs/papers/security_corpus/arxiv_dp_edge.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "audit trail tamper detection" --max-results 50 \
    --out docs/papers/security_corpus/arxiv_audit.jsonl

python tools/collect_image_papers.py --source semantic_scholar \
    --query "supply chain security sbom" --max-results 50 \
    --out docs/papers/security_corpus/s2_sbom.jsonl
```

トピック: `privacy`, `prompt_injection`, `audit`, `sbom`, `iec_62443`,
`zero_trust`, `differential_privacy`, `tee`, `secure_aggregation`.
