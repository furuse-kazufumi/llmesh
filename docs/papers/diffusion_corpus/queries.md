# Diffusion Models Corpus Queries

```bash
python tools/bulk_corpus_collector.py --domain diffusion --target 10000 \
    --queries "denoising diffusion probabilistic model" \
              "score based generative" \
              "stable diffusion latent" \
              "video diffusion temporal"

python tools/collect_image_papers.py --source arxiv \
    --query "flow matching rectified flow" --max-results 200 \
    --out docs/papers/diffusion_corpus/arxiv_flow_matching.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "consistency model distillation" --max-results 200 \
    --out docs/papers/diffusion_corpus/arxiv_consistency.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "text to image controlnet" --max-results 200 \
    --out docs/papers/diffusion_corpus/arxiv_controlnet.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "diffusion 3d gaussian splatting" --max-results 200 \
    --out docs/papers/diffusion_corpus/arxiv_3d.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "audio music diffusion synthesis" --max-results 200 \
    --out docs/papers/diffusion_corpus/arxiv_audio.jsonl

python tools/community_corpus_collector.py --source crossref \
    --query "generative model diffusion" --target 5000 \
    --out docs/papers/diffusion_corpus/crossref_diffusion.jsonl
```

**重点トピック**: DDPM, DDIM, score-based, EDM, latent diffusion,
flow matching, rectified flow, consistency model, ControlNet, LoRA
adapters, text-to-image, video diffusion, 3D diffusion, gaussian
splatting, audio diffusion, motion diffusion, robot policy diffusion.
