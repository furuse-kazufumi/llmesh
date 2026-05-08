# Statistics Corpus Queries

統計的品質管理 (SPC) を含む統計学全般。LLMesh の Xbar-R / CUSUM チャート、
hypothesis ベースの property testing と関連。

```bash
python tools/bulk_corpus_collector.py --domain statistics --target 10000 \
    --queries "statistical process control spc" \
              "bayesian inference posterior" \
              "hypothesis testing power" \
              "time series forecasting"

python tools/community_corpus_collector.py --source crossref \
    --query "statistical methods" --target 8000 \
    --out docs/papers/statistics_corpus/crossref_stats.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "cusum change point detection" --max-results 200 \
    --out docs/papers/statistics_corpus/arxiv_cusum.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "shewhart control chart manufacturing" --max-results 200 \
    --out docs/papers/statistics_corpus/arxiv_shewhart.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "extreme value theory rare events" --max-results 200 \
    --out docs/papers/statistics_corpus/arxiv_evt.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "robust statistics outlier" --max-results 200 \
    --out docs/papers/statistics_corpus/arxiv_robust.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "causal inference do-calculus" --max-results 200 \
    --out docs/papers/statistics_corpus/arxiv_causal.jsonl
```

**重点トピック**: SPC (Xbar-R / CUSUM / EWMA) / ベイズ統計 / MCMC /
仮説検定 / 検出力分析 / extreme value theory / robust statistics /
時系列解析 (ARIMA / GARCH) / 因果推論 / propensity score / sequential
analysis / Wald test / Bonferroni / Bayesian network.
