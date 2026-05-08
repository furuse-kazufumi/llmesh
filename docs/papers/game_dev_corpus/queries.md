# Game Development Corpus Queries

```bash
python tools/collect_image_papers.py --source arxiv \
    --query "npc dialogue large language model" --max-results 100 \
    --out docs/papers/game_dev_corpus/arxiv_npc_llm.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "procedural generation game" --max-results 50 \
    --out docs/papers/game_dev_corpus/arxiv_procedural.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "anti cheat behavioural detection" --max-results 50 \
    --out docs/papers/game_dev_corpus/arxiv_anticheat.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "game telemetry player analytics" --max-results 50 \
    --out docs/papers/game_dev_corpus/arxiv_telemetry.jsonl

python tools/collect_image_papers.py --source semantic_scholar \
    --query "esports commentary generation" --max-results 50 \
    --out docs/papers/game_dev_corpus/s2_esports.jsonl
```

トピック: `npc_ai`, `procedural`, `anti_cheat`, `telemetry`,
`esports`, `motion_capture`, `unity`, `unreal`.
