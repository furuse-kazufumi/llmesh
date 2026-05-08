# Quantum Computing Corpus Queries

```bash
python tools/bulk_corpus_collector.py --domain quantum --target 10000 \
    --queries "quantum machine learning" "variational quantum algorithm" \
              "quantum error correction" "quantum simulation"

python tools/community_corpus_collector.py --source crossref \
    --query "quantum computing algorithm" --target 5000 \
    --out docs/papers/quantum_computing_corpus/crossref_qc.jsonl

python tools/community_corpus_collector.py --source dblp \
    --query "quantum complexity" --target 3000 \
    --out docs/papers/quantum_computing_corpus/dblp_qc.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "quantum supremacy advantage" --max-results 200 \
    --out docs/papers/quantum_computing_corpus/arxiv_supremacy.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "noise intermediate scale quantum nisq" --max-results 200 \
    --out docs/papers/quantum_computing_corpus/arxiv_nisq.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "topological qubit majorana" --max-results 200 \
    --out docs/papers/quantum_computing_corpus/arxiv_topological.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "quantum cryptography qkd" --max-results 200 \
    --out docs/papers/quantum_computing_corpus/arxiv_qkd.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "quantum sensor gravimeter clock" --max-results 200 \
    --out docs/papers/quantum_computing_corpus/arxiv_sensor.jsonl
```

**重点トピック**: quantum machine learning (QML), VQE/VQA, QAOA,
quantum error correction (surface code, color code), NISQ,
fault-tolerant computing, topological qubits, ion trap,
superconducting qubits, photonic, quantum sensors, quantum cryptography
(QKD, BB84), quantum simulation chemistry, quantum advantage.
