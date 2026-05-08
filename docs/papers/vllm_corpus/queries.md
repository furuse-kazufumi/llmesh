# Vision-Language Models / vLLM Serving Corpus Queries

このコーパスは **2 つの重要トピック**を統合します：

1. **VLM = Vision-Language Models** — マルチモーダル基盤モデル
2. **vLLM = LLM serving framework** — 高速推論エンジン

```bash
# Vision-Language Models
python tools/bulk_corpus_collector.py --domain vllm --target 10000 \
    --queries "vision language model clip" \
              "multimodal foundation model" \
              "visual instruction tuning" \
              "image text retrieval"

python tools/collect_image_papers.py --source arxiv \
    --query "video language model temporal" --max-results 200 \
    --out docs/papers/vllm_corpus/arxiv_video_llm.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "visual question answering vqa" --max-results 200 \
    --out docs/papers/vllm_corpus/arxiv_vqa.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "document understanding multimodal" --max-results 200 \
    --out docs/papers/vllm_corpus/arxiv_doc_vlm.jsonl

# vLLM serving (PagedAttention, continuous batching)
python tools/collect_image_papers.py --source arxiv \
    --query "paged attention kv cache serving" --max-results 200 \
    --out docs/papers/vllm_corpus/arxiv_paged_attention.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "continuous batching llm inference" --max-results 200 \
    --out docs/papers/vllm_corpus/arxiv_batching.jsonl

python tools/collect_image_papers.py --source arxiv \
    --query "speculative decoding draft model" --max-results 200 \
    --out docs/papers/vllm_corpus/arxiv_speculative.jsonl

python tools/community_corpus_collector.py --source hn \
    --query "vllm sglang tgi serving" --target 1000 \
    --out docs/papers/vllm_corpus/hn_serving.jsonl
```

**重点トピック (VLM)**: CLIP, BLIP, LLaVA, Flamingo, GPT-4V, Gemini,
Claude Vision, visual instruction tuning, OCR-free document AI,
embodied VLM, video understanding, audio-visual, OWL-ViT.

**重点トピック (vLLM serving)**: PagedAttention, continuous batching,
speculative decoding, KV cache, prefix caching, FlashAttention,
RoCm vs CUDA, TGI, SGLang, OpenLLM, ray serve, dynamic batching.
