# Toward Reproducible LLM Evaluation under Resource Constraints

## Abstract

We investigate how to evaluate the relative contribution of architectural
choices in small language models when GPU memory is fixed at 16 GB. Our
research question is whether activation checkpointing materially changes
downstream task accuracy when training budget is held constant.

## Setup and Constraints

- All experiments run on a single 16 GB consumer GPU.
- Training budget capped at 4 GPU-hours per condition.
- We restrict ourselves to English Wikipedia and English C4 data only.
- Tokenisation is BPE with a 32k vocabulary.

## Metrics

We report:

1. **Accuracy** on GLUE dev set (averaged across tasks).
2. **Validation loss** on a held-out 1 GB slice of C4.
3. **Wall-clock latency** in milliseconds for a single forward pass at
   batch size 1.

## Future Work

This study does not cover multilingual training, retrieval-augmented
fine-tuning, or models above 350M parameters. We also leave for future
work a comparison against mixture-of-experts architectures of comparable
training cost.
