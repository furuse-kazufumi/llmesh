# Multivariate Analysis Corpus Queries

LLMesh の MTEngine（マハラノビス・タグチ法）の理論的基礎と関連手法。

```bash
python tools/bulk_corpus_collector.py --domain multivariate --target 10000 \
    --queries "Mahalanobis distance anomaly" \
              "principal component analysis pca" \
              "discriminant analysis classification" \
              "multivariate statistical process control"

python tools/community_corpus_collector.py --source crossref \
    --query "multivariate statistical analysis" --target 5000 \
    --out docs/papers/multivariate_analysis_corpus/crossref_mva.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "factor analysis structural equation" --max-results 200 \
    --out docs/papers/multivariate_analysis_corpus/arxiv_factor.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "canonical correlation analysis cca" --max-results 200 \
    --out docs/papers/multivariate_analysis_corpus/arxiv_cca.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "Hotelling t squared multivariate control" --max-results 200 \
    --out docs/papers/multivariate_analysis_corpus/arxiv_hotelling.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "manifold learning umap tsne" --max-results 200 \
    --out docs/papers/multivariate_analysis_corpus/arxiv_manifold.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "covariance matrix shrinkage estimation" --max-results 200 \
    --out docs/papers/multivariate_analysis_corpus/arxiv_covariance.jsonl
```

**重点トピック**: マハラノビス距離 / PCA / ICA / 因子分析 / 判別分析 /
正準相関分析 (CCA) / クラスタリング / 多次元尺度法 (MDS) / UMAP / t-SNE /
manifold 学習 / 共分散行列推定 / Hotelling T² / MANOVA / 多変量管理図 /
タグチ法 / MT 法 / 直交配列実験計画.
