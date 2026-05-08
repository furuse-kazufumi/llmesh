# Numerical Methods / Linear Algebra Corpus Queries

LLMesh が依存する numpy / scipy 系の理論的基礎。

```bash
python tools/bulk_corpus_collector.py --domain numerical --target 10000 \
    --queries "singular value decomposition svd" \
              "matrix factorization low rank" \
              "iterative linear solver krylov" \
              "numerical pde finite element"

python tools/community_corpus_collector.py --source crossref \
    --query "numerical analysis" --target 5000 \
    --out docs/papers/numerical_methods_corpus/crossref_numerical.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "randomized linear algebra sketching" --max-results 200 \
    --out docs/papers/numerical_methods_corpus/arxiv_randomized.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "tensor decomposition cp tucker" --max-results 200 \
    --out docs/papers/numerical_methods_corpus/arxiv_tensor.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "automatic differentiation jax pytorch" --max-results 200 \
    --out docs/papers/numerical_methods_corpus/arxiv_autodiff.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "fast fourier transform fft" --max-results 200 \
    --out docs/papers/numerical_methods_corpus/arxiv_fft.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "preconditioner sparse matrix" --max-results 200 \
    --out docs/papers/numerical_methods_corpus/arxiv_sparse.jsonl
```

**重点トピック**: SVD / QR / LU / Cholesky 分解 / 固有値計算 (QR 法 /
Lanczos / Arnoldi) / Krylov 部分空間法 (CG / GMRES / BiCGStab) /
randomized 線形代数 / sketching / テンソル分解 (CP / Tucker / TT) /
自動微分 / FFT / 有限要素法 (FEM) / 有限体積法 (FVM) / 多重格子法 (MG) /
sparse 行列計算 / 前処理.
