# Deep Learning Corpus Queries

```bash
python tools/bulk_corpus_collector.py --domain deep_learning --target 10000 \
    --queries "deep learning optimization" "convolutional neural network" \
              "self supervised learning" "contrastive learning"

python tools/community_corpus_collector.py --source crossref \
    --query "deep learning architecture" --target 5000 \
    --out docs/papers/deep_learning_corpus/crossref_dl.jsonl

python tools/community_corpus_collector.py --source dblp \
    --query "deep learning" --target 5000 \
    --out docs/papers/deep_learning_corpus/dblp_dl.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "scaling laws neural" --max-results 200 \
    --out docs/papers/deep_learning_corpus/arxiv_scaling.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "regularization generalization deep" --max-results 200 \
    --out docs/papers/deep_learning_corpus/arxiv_regularization.jsonl
```

**重点トピック**: optimization, architecture, regularization, scaling laws,
self-supervised, contrastive, distillation, sparsity, lottery ticket,
implicit bias, double descent, generalization, batch norm, layer norm.
