# Information Theory Corpus Queries

LLMesh のプライバシーパイプライン（PromptFirewall）の理論的基礎、
通信プロトコル (MQTT / CAN / EtherCAT) のフレーム圧縮、量子情報。

```bash
python tools/bulk_corpus_collector.py --domain information_theory --target 10000 \
    --queries "shannon entropy mutual information" \
              "channel capacity coding theorem" \
              "data compression lossless" \
              "differential privacy bounds"

python tools/community_corpus_collector.py --source crossref \
    --query "information theory" --target 5000 \
    --out docs/papers/information_theory_corpus/crossref_it.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "ldpc polar code error correction" --max-results 200 \
    --out docs/papers/information_theory_corpus/arxiv_ecc.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "rate distortion compression neural" --max-results 200 \
    --out docs/papers/information_theory_corpus/arxiv_rate_distortion.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "kullback leibler divergence variational" --max-results 200 \
    --out docs/papers/information_theory_corpus/arxiv_kl.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "quantum information theory entanglement" --max-results 200 \
    --out docs/papers/information_theory_corpus/arxiv_quantum_info.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "minimum description length mdl" --max-results 200 \
    --out docs/papers/information_theory_corpus/arxiv_mdl.jsonl
```

**重点トピック**: Shannon エントロピー / 相互情報量 / KL 発散 /
チャネル容量 / 符号化定理 (source / channel) / 誤り訂正符号 (Hamming /
Reed-Solomon / LDPC / Polar / Turbo) / レート歪み理論 / データ圧縮
(Huffman / arithmetic / LZ77/78 / zstd) / 量子情報 / エンタングルメント
エントロピー / no-cloning / ホレヴォー上限 / MDL / Fisher 情報量 /
情報理論的プライバシー.
