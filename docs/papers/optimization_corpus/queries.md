# Optimization Corpus Queries

```bash
python tools/bulk_corpus_collector.py --domain optimization --target 10000 \
    --queries "convex optimization machine learning" \
              "stochastic gradient descent sgd" \
              "mixed integer programming" \
              "metaheuristic genetic algorithm"

python tools/community_corpus_collector.py --source crossref \
    --query "optimization algorithm" --target 5000 \
    --out docs/papers/optimization_corpus/crossref_opt.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "adam optimizer adaptive learning rate" --max-results 200 \
    --out docs/papers/optimization_corpus/arxiv_adam.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "second order quasi newton lbfgs" --max-results 200 \
    --out docs/papers/optimization_corpus/arxiv_second_order.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "robust optimization uncertainty" --max-results 200 \
    --out docs/papers/optimization_corpus/arxiv_robust_opt.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "bayesian optimization hyperparameter" --max-results 200 \
    --out docs/papers/optimization_corpus/arxiv_bayes_opt.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "linear programming simplex interior point" --max-results 200 \
    --out docs/papers/optimization_corpus/arxiv_lp.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "combinatorial optimization np hard" --max-results 200 \
    --out docs/papers/optimization_corpus/arxiv_combinatorial.jsonl
```

**重点トピック**: 凸最適化 / 非凸最適化 / SGD 系 (Adam / RMSProp /
AdaGrad / SGD-Momentum) / 二階法 (LBFGS / Newton) / LP / IP / MIP /
SDP / robust optimization / Bayesian optimization / メタヒューリスティクス
(GA / PSO / SA / TS) / ADMM / proximal methods / MIRROR descent /
ALM / penalty method / トラスト領域法.
