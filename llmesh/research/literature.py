"""Literature agent — extract structured research metadata from a paper (Phase 1).

Given the raw text (Markdown / plain) of a paper, the agent asks an
LLM to surface four fields used by later research-orchestration
phases:

- ``research_question`` — what the paper investigates, one sentence
- ``constraints``       — preconditions / setting / known limits
- ``metrics``           — the quantitative measures the paper reports
- ``open_problems``     — explicit "future work" / unresolved threads

The agent is backend-agnostic: the LLM call is exposed as an
:data:`ExtractFn` callable that maps a prompt string to a result dict.
The same shape lets tests inject :func:`mock_extract` and production
wire :func:`make_ollama_extract` / :func:`make_anthropic_extract`.

Phase 1 constraint: "mock-first" — the e2e test runs entirely on the
mock backend so the PoC is reproducible without network or API keys.
The real-backend adapters are thin wrappers that the next phase will
exercise once a literature-extraction tool schema is registered.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from llmesh.core.agent import Agent, AgentConfig

if TYPE_CHECKING:  # pragma: no cover - import only for type-checking
    from llmesh.llm.anthropic_backend import AnthropicBackend
    from llmesh.llm.ollama import OllamaBackend


# ExtractFn: prompt string -> dict with the 4 required keys.
# The dict shape is what :func:`parse_literature_result` consumes.
ExtractFn = Callable[[str], dict[str, Any]]

# Tool name registered with backends that route prompts via TOOL_SCHEMAS.
# Adapters fall back to this name; concrete schemas live in mcp/schemas.py.
LITERATURE_TOOL_NAME = "literature_extract"


# ---------------------------------------------------------------------------
# I/O dataclasses (Agent contract)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LiteratureRequest:
    """One paper to extract metadata from.

    ``text`` is the raw body (Markdown or plain text). ``title`` is
    optional metadata; when supplied the prompt nudges the LLM to
    treat it as the canonical paper name.
    """

    text: str
    title: str = ""


@dataclass(frozen=True)
class LiteratureResponse:
    """Structured extraction result. ``raw`` keeps the unparsed dict
    for trace replay and downstream debugging."""

    research_question: str
    constraints: tuple[str, ...]
    metrics: tuple[str, ...]
    open_problems: tuple[str, ...]
    raw: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# prompt + parsing
# ---------------------------------------------------------------------------


_PROMPT_HEADER = (
    "You are a research literature analyst. Extract a strict JSON object "
    "with EXACTLY these four keys: "
    '"research_question" (one short sentence), '
    '"constraints" (array of strings), '
    '"metrics" (array of strings), '
    '"open_problems" (array of strings). '
    "Reply with the JSON object only — no surrounding prose."
)


def build_literature_prompt(text: str, title: str = "") -> str:
    """Compose the user-facing prompt sent to an LLM."""
    title_line = f"Title: {title.strip()}\n\n" if title.strip() else ""
    # Truncate gargantuan inputs at the prompt boundary so a single
    # huge paper cannot exhaust the backend's context window. The
    # caller can supply a pre-summarised text if a longer source is
    # required; PoC scope keeps the budget modest.
    body = text if len(text) <= 12_000 else (text[:12_000] + "\n... [truncated]")
    return f"{_PROMPT_HEADER}\n\n{title_line}{body}"


def _coerce_str_list(value: Any) -> tuple[str, ...]:
    """Best-effort coercion of a backend payload entry into a tuple of strings.

    LLMs occasionally return a single string where an array was asked
    for, or a list with non-string entries; both are normalised here so
    a slightly-off response is still usable downstream.
    """
    if value is None:
        return ()
    if isinstance(value, str):
        # Single-string response: treat as a one-element list.
        return (value,) if value.strip() else ()
    if isinstance(value, (list, tuple)):
        out: list[str] = []
        for item in value:
            if item is None:
                continue
            s = str(item).strip()
            if s:
                out.append(s)
        return tuple(out)
    return (str(value),)


def parse_literature_result(result: dict[str, Any]) -> LiteratureResponse:
    """Validate and coerce a backend payload into :class:`LiteratureResponse`.

    Missing list fields default to empty tuples — a partial response is
    preferable to a hard failure for a PoC; strict validation moves to
    a downstream reviewer agent in a later phase.
    Raises ``ValueError`` only when ``research_question`` is absent so
    that we never silently invent it.
    """
    if not isinstance(result, dict):
        raise ValueError("literature result must be a JSON object")
    rq = result.get("research_question")
    if not isinstance(rq, str) or not rq.strip():
        raise ValueError("missing or empty 'research_question'")
    return LiteratureResponse(
        research_question=rq.strip(),
        constraints=_coerce_str_list(result.get("constraints")),
        metrics=_coerce_str_list(result.get("metrics")),
        open_problems=_coerce_str_list(result.get("open_problems")),
        raw=dict(result),
    )


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class LiteratureAgent(Agent[LiteratureRequest, LiteratureResponse]):
    """Agent that extracts research metadata via an injected ExtractFn.

    The constructor takes the same :class:`AgentConfig` as every other
    :class:`Agent` plus the backend-agnostic ``extract_fn`` callable.
    For the Phase 1 e2e test the fn is :func:`mock_extract`; production
    callers use the ``make_*_extract`` adapters below.
    """

    def __init__(self, config: AgentConfig, extract_fn: ExtractFn) -> None:
        super().__init__(config)
        self._extract = extract_fn

    def run(self, request: LiteratureRequest) -> LiteratureResponse:
        prompt = build_literature_prompt(request.text, request.title)
        result = self._extract(prompt)
        return parse_literature_result(result)


# ---------------------------------------------------------------------------
# extract function adapters
# ---------------------------------------------------------------------------


def mock_extract(prompt: str) -> dict[str, Any]:
    """Deterministic mock — echoes a tiny extraction back.

    Echoes nothing from the prompt by design: tests pin the expected
    fields. If a callsite needs a fixture-driven mock, write a closure
    around a known dict and pass that as :data:`ExtractFn` directly.
    """
    return {
        "research_question": "How can we evaluate the contribution of X under Y?",
        "constraints": ["assumes English papers", "no figures parsed"],
        "metrics": ["accuracy", "latency_ms"],
        "open_problems": ["multilingual support", "table extraction"],
        "_mock": True,
    }


def make_ollama_extract(backend: OllamaBackend) -> ExtractFn:
    """Adapter: wrap an :class:`OllamaBackend` as an :data:`ExtractFn`.

    Both backends expose ``invoke(tool_name, request_body)`` — the
    adapter forwards the prompt under :data:`LITERATURE_TOOL_NAME`.
    A literature prompt builder must be registered in
    :mod:`llmesh.llm.prompt` for the tool name before this adapter
    yields a real extraction; until then it raises ``BackendError``,
    which is the expected behaviour for the PoC's no-real-backend run.
    """

    def _extract(prompt: str) -> dict[str, Any]:
        return backend.invoke(LITERATURE_TOOL_NAME, {"prompt": prompt})

    return _extract


def make_anthropic_extract(backend: AnthropicBackend) -> ExtractFn:
    """Adapter: wrap an :class:`AnthropicBackend` as an :data:`ExtractFn`.

    Same contract as :func:`make_ollama_extract`; see notes there.
    """

    def _extract(prompt: str) -> dict[str, Any]:
        return backend.invoke(LITERATURE_TOOL_NAME, {"prompt": prompt})

    return _extract


__all__ = [
    "LITERATURE_TOOL_NAME",
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
