"""Research-automation agents (Phase 1+).

Built on :mod:`llmesh.core` primitives, this package hosts the
domain-specific research orchestration agents (literature, hypothesis,
planner, executor, reviewer). Each agent is backend-agnostic: callers
inject an :data:`ExtractFn` that wraps any concrete LLM backend
(Ollama, Anthropic, ...) or a mock for deterministic testing.
"""

from __future__ import annotations

from llmesh.research.literature import (
    ExtractFn,
    LiteratureAgent,
    LiteratureRequest,
    LiteratureResponse,
    build_literature_prompt,
    make_anthropic_extract,
    make_ollama_extract,
    mock_extract,
    parse_literature_result,
)

__all__ = [
    "ExtractFn",
    "LiteratureAgent",
    "LiteratureRequest",
    "LiteratureResponse",
    "build_literature_prompt",
    "make_anthropic_extract",
    "make_ollama_extract",
    "mock_extract",
    "parse_literature_result",
]
