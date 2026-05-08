# LLM Corpus Queries

```bash
python tools/bulk_corpus_collector.py --domain llm --target 10000 \
    --queries "large language model alignment" "instruction tuning rlhf" \
              "long context attention" "mixture of experts moe"

python tools/community_corpus_collector.py --source crossref \
    --query "large language model" --target 8000 \
    --out docs/papers/llm_corpus/crossref_llm.jsonl

python tools/community_corpus_collector.py --source hn \
    --query "llm production deployment" --target 1500 \
    --out docs/papers/llm_corpus/hn_llm_practice.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "tool use reasoning chain of thought" --max-results 300 \
    --out docs/papers/llm_corpus/arxiv_tool_use.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "constitutional ai dpo orpo" --max-results 200 \
    --out docs/papers/llm_corpus/arxiv_alignment.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "llm efficient training pretraining" --max-results 300 \
    --out docs/papers/llm_corpus/arxiv_pretraining.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "in context learning few shot" --max-results 200 \
    --out docs/papers/llm_corpus/arxiv_icl.jsonl
```

**重点トピック**: alignment, RLHF, DPO/KTO/ORPO, instruction tuning,
ICL (in-context learning), CoT, ToT, ReAct, function calling, tool use,
long-context, MoE, sparse activation, sliding-window attention,
constitutional AI, model evaluation, jailbreak / red-teaming, agentic.
