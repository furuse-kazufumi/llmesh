"""Tests for llmesh.research.literature — Phase 1 literature agent PoC.

The PoC e2e path is mock-only: the literature agent runs against
:func:`mock_extract` and a deterministic closure mock, and the
Ollama / Anthropic adapters are exercised against a fake backend
that records the tool_name / request_body it was called with.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from llmesh.core.agent import AgentConfig
from llmesh.research.literature import (
    LITERATURE_TOOL_NAME,
    LiteratureAgent,
    LiteratureRequest,
    LiteratureResponse,
    build_literature_prompt,
    make_anthropic_extract,
    make_ollama_extract,
    mock_extract,
    parse_literature_result,
)

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# prompt builder
# ---------------------------------------------------------------------------


class TestPromptBuilder:
    def test_contains_four_required_keys(self) -> None:
        prompt = build_literature_prompt("body", title="t")
        for key in ("research_question", "constraints", "metrics", "open_problems"):
            assert key in prompt

    def test_includes_title_when_given(self) -> None:
        prompt = build_literature_prompt("body", title="My Paper")
        assert "Title: My Paper" in prompt

    def test_omits_title_when_blank(self) -> None:
        prompt = build_literature_prompt("body", title="   ")
        assert "Title:" not in prompt

    def test_truncates_very_long_text(self) -> None:
        long = "x" * 20_000
        prompt = build_literature_prompt(long)
        # truncation marker present and full body NOT included
        assert "[truncated]" in prompt
        assert len(prompt) < len(long) + 1000


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------


class TestParseResult:
    def test_happy_path(self) -> None:
        res = parse_literature_result(
            {
                "research_question": "Q?",
                "constraints": ["c1", "c2"],
                "metrics": ["m1"],
                "open_problems": ["op1"],
            }
        )
        assert isinstance(res, LiteratureResponse)
        assert res.research_question == "Q?"
        assert res.constraints == ("c1", "c2")
        assert res.metrics == ("m1",)
        assert res.open_problems == ("op1",)

    def test_missing_research_question_raises(self) -> None:
        with pytest.raises(ValueError, match="research_question"):
            parse_literature_result({"constraints": []})

    def test_empty_research_question_raises(self) -> None:
        with pytest.raises(ValueError, match="research_question"):
            parse_literature_result({"research_question": "   "})

    def test_non_dict_raises(self) -> None:
        with pytest.raises(ValueError, match="JSON object"):
            parse_literature_result(["nope"])  # type: ignore[arg-type]

    def test_missing_list_fields_default_to_empty(self) -> None:
        res = parse_literature_result({"research_question": "Q?"})
        assert res.constraints == ()
        assert res.metrics == ()
        assert res.open_problems == ()

    def test_coerces_single_string_to_tuple(self) -> None:
        # An LLM occasionally returns a bare string where an array was asked
        res = parse_literature_result(
            {"research_question": "Q?", "constraints": "only one"},
        )
        assert res.constraints == ("only one",)

    def test_drops_blank_entries(self) -> None:
        res = parse_literature_result(
            {"research_question": "Q?", "metrics": ["m1", "  ", "", "m2", None]},
        )
        assert res.metrics == ("m1", "m2")

    def test_raw_payload_preserved(self) -> None:
        payload = {"research_question": "Q?", "extra_field": 42}
        res = parse_literature_result(payload)
        assert res.raw["extra_field"] == 42


# ---------------------------------------------------------------------------
# Agent e2e
# ---------------------------------------------------------------------------


class TestLiteratureAgentE2E:
    def test_mock_extract_via_agent(self) -> None:
        agent = LiteratureAgent(
            AgentConfig(name="agent.literature", model="mock"),
            extract_fn=mock_extract,
        )
        res = agent.run(LiteratureRequest(text="anything"))
        assert res.research_question
        assert len(res.metrics) >= 1
        assert res.raw["_mock"] is True

    def test_agent_passes_prompt_to_extract_fn(self) -> None:
        captured: dict[str, str] = {}

        def closure_extract(prompt: str) -> dict[str, object]:
            captured["prompt"] = prompt
            return {
                "research_question": "captured?",
                "constraints": [],
                "metrics": [],
                "open_problems": [],
            }

        agent = LiteratureAgent(
            AgentConfig(name="agent.literature", model="closure"),
            extract_fn=closure_extract,
        )
        res = agent.run(LiteratureRequest(text="paper body", title="My Title"))
        assert "Title: My Title" in captured["prompt"]
        assert "paper body" in captured["prompt"]
        assert res.research_question == "captured?"

    def test_dummy_paper_fixture_end_to_end(self) -> None:
        # Phase 1 acceptance: a real-looking dummy paper round-trips
        # through prompt → mock extract → parsed response without error.
        paper = (FIXTURES / "dummy_paper.md").read_text(encoding="utf-8")

        # Closure that pretends the LLM read the paper and pulled out
        # the four fields based on its headings. The "mock-first"
        # constraint allows this kind of deterministic synthetic
        # response so the e2e test stays reproducible.
        def fake_extract(prompt: str) -> dict[str, object]:
            assert "Toward Reproducible LLM Evaluation" in prompt
            return {
                "research_question": (
                    "Does activation checkpointing change downstream "
                    "task accuracy at fixed training budget?"
                ),
                "constraints": [
                    "single 16 GB GPU",
                    "training budget 4 GPU-hours per condition",
                    "English Wikipedia and English C4 only",
                    "BPE 32k vocabulary",
                ],
                "metrics": [
                    "GLUE dev accuracy",
                    "validation loss on held-out C4",
                    "wall-clock latency (ms) for a single forward pass",
                ],
                "open_problems": [
                    "multilingual training",
                    "retrieval-augmented fine-tuning",
                    "models above 350M parameters",
                    "comparison vs mixture-of-experts",
                ],
            }

        agent = LiteratureAgent(
            AgentConfig(name="agent.literature", model="mock"),
            extract_fn=fake_extract,
        )
        res = agent.run(
            LiteratureRequest(
                text=paper,
                title="Toward Reproducible LLM Evaluation under Resource Constraints",
            )
        )
        assert "activation checkpointing" in res.research_question
        assert any("16 GB" in c for c in res.constraints)
        assert any("GLUE" in m for m in res.metrics)
        assert "multilingual training" in res.open_problems


# ---------------------------------------------------------------------------
# backend adapters — exercised against a fake backend
# ---------------------------------------------------------------------------


class _FakeBackend:
    """Mimics OllamaBackend / AnthropicBackend.invoke for adapter tests.

    Recording-only: captures the tool_name and request_body so we can
    assert the adapter forwards them under :data:`LITERATURE_TOOL_NAME`.
    """

    def __init__(self, response: dict[str, object] | None = None) -> None:
        self.response = response or {
            "research_question": "fake?",
            "constraints": [],
            "metrics": [],
            "open_problems": [],
        }
        self.calls: list[tuple[str, dict[str, object]]] = []

    def invoke(self, tool_name: str, request_body: dict[str, object]) -> dict[str, object]:
        self.calls.append((tool_name, request_body))
        return self.response


class TestOllamaAdapter:
    def test_forwards_prompt_under_literature_tool_name(self) -> None:
        be = _FakeBackend()
        ex = make_ollama_extract(be)  # type: ignore[arg-type]
        result = ex("a prompt")
        assert be.calls == [(LITERATURE_TOOL_NAME, {"prompt": "a prompt"})]
        assert result["research_question"] == "fake?"


class TestAnthropicAdapter:
    def test_forwards_prompt_under_literature_tool_name(self) -> None:
        be = _FakeBackend()
        ex = make_anthropic_extract(be)  # type: ignore[arg-type]
        ex("another prompt")
        assert be.calls == [(LITERATURE_TOOL_NAME, {"prompt": "another prompt"})]

    def test_adapter_round_trips_through_agent(self) -> None:
        # Adapter + Agent stack — same dataflow as a real run, just with
        # a fake backend. Confirms the ExtractFn contract is uniform.
        be = _FakeBackend(
            response={
                "research_question": "wrapped?",
                "constraints": ["c"],
                "metrics": ["m"],
                "open_problems": ["o"],
            }
        )
        agent = LiteratureAgent(
            AgentConfig(name="agent.literature", model="anthropic:fake"),
            extract_fn=make_anthropic_extract(be),  # type: ignore[arg-type]
        )
        res = agent.run(LiteratureRequest(text="body", title="t"))
        assert res.research_question == "wrapped?"
        assert res.constraints == ("c",)
