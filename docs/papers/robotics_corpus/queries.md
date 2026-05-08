# Robotics Corpus Queries

```bash
python tools/collect_image_papers.py --source arxiv \
    --query "ros 2 large language model" --max-results 100 \
    --out docs/papers/robotics_corpus/arxiv_ros2_llm.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "slam point cloud edge" --max-results 50 \
    --out docs/papers/robotics_corpus/arxiv_slam.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "robot manipulation language" --max-results 50 \
    --out docs/papers/robotics_corpus/arxiv_manipulation.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "event camera robotics navigation" --max-results 50 \
    --out docs/papers/robotics_corpus/arxiv_dvs_robot.jsonl

python tools/collect_image_papers.py --source semantic_scholar \
    --query "humanoid embodied agent" --max-results 50 \
    --out docs/papers/robotics_corpus/s2_humanoid.jsonl
```

トピック: `ros`, `slam`, `manipulation`, `navigation`, `humanoid`,
`teleoperation`, `embodied_agent`.
