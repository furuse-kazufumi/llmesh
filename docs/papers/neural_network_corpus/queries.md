# Neural Network Corpus Queries

```bash
python tools/bulk_corpus_collector.py --domain neural_network --target 10000 \
    --queries "spiking neural network" "graph neural network" \
              "neural ode" "neural radiance field"

python tools/community_corpus_collector.py --source crossref \
    --query "neural network architecture" --target 5000 \
    --out docs/papers/neural_network_corpus/crossref_nn.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "transformer architecture survey" --max-results 200 \
    --out docs/papers/neural_network_corpus/arxiv_transformer_survey.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "recurrent neural network state space" --max-results 200 \
    --out docs/papers/neural_network_corpus/arxiv_rnn_ssm.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "neural network pruning quantization" --max-results 200 \
    --out docs/papers/neural_network_corpus/arxiv_compression.jsonl
```

**重点トピック**: SNN, GNN, transformer, mamba, S4, S6, RNN, LSTM,
attention variants, CNN architectures, ResNet, EfficientNet, NeRF,
Neural ODE, Hopfield network, capsule network, MoE.
