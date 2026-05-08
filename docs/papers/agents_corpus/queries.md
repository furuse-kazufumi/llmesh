# AI Agents Corpus Queries

```bash
python tools/bulk_corpus_collector.py --domain agents --target 10000 \
    --queries "autonomous llm agent" "tool calling function" \
              "multi agent collaboration" "react planning"

python tools/collect_image_papers.py --source arxiv \
    --query "agent benchmarking evaluation" --max-results 200 \
    --out docs/papers/agents_corpus/arxiv_benchmark.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "computer use agent gui" --max-results 200 \
    --out docs/papers/agents_corpus/arxiv_computer_use.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "code agent software engineering" --max-results 200 \
    --out docs/papers/agents_corpus/arxiv_swe_agent.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "memory long term agent" --max-results 200 \
    --out docs/papers/agents_corpus/arxiv_memory.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "agentic rag retrieval" --max-results 200 \
    --out docs/papers/agents_corpus/arxiv_agentic_rag.jsonl

python tools/community_corpus_collector.py --source hn \
    --query "ai agent autogpt langchain" --target 1500 \
    --out docs/papers/agents_corpus/hn_agents.jsonl
```

**重点トピック**: ReAct, Reflexion, Toolformer, function calling,
MCP (Model Context Protocol), MultiAgent, AutoGPT, BabyAGI,
SWE-Agent, Devin, computer-use agent, browser agent, code agent,
long-term memory, episodic memory, planning, hierarchical agent,
agentic RAG, autonomous research.
