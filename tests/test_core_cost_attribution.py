"""Tests for Phase 12 D1 — cost-aware trace + attribution chains."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from llmesh.core import (
    KIND_PROMPT,
    AttributionLink,
    CostBreakdown,
    TraceLogger,
    attribution_from_extra,
    attribution_to_extra,
    build_attribution_chain,
    cost_from_metrics,
    cost_to_metrics,
    count_redundancy,
    is_redundant,
    summarize_costs,
)
from llmesh.core.cost_attribution import (
    EXTRA_KEY_ATTRIBUTION,
    EXTRA_KEY_REDUNDANCY,
    METRIC_KEY_INPUT_TOKENS,
    METRIC_KEY_OUTPUT_TOKENS,
    METRIC_KEY_USD,
)
from llmesh.core.trace import make_entry


def _read_lines(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").strip().split("\n")
    ]


# ---------------------------------------------------------------------------
# CostBreakdown
# ---------------------------------------------------------------------------


class TestCostBreakdown:
    def test_default_is_zero_cost(self) -> None:
        c = CostBreakdown()
        assert c.usd == 0.0
        assert c.input_tokens == 0
        assert c.output_tokens == 0
        assert c.cached_tokens == 0
        assert c.currency == "USD"

    def test_negative_usd_rejected(self) -> None:
        with pytest.raises(ValueError):
            CostBreakdown(usd=-0.1)

    def test_negative_tokens_rejected(self) -> None:
        with pytest.raises(ValueError):
            CostBreakdown(input_tokens=-1)

    def test_cost_to_metrics_round_trip(self) -> None:
        c = CostBreakdown(usd=0.012, input_tokens=120, output_tokens=80, cached_tokens=40)
        m = cost_to_metrics(c)
        back = cost_from_metrics(m)
        assert back == c

    def test_cost_from_empty_metrics_is_zero(self) -> None:
        assert cost_from_metrics({}) == CostBreakdown()

    def test_cost_from_malformed_metrics_tolerated(self) -> None:
        # all-None values should not crash
        c = cost_from_metrics({METRIC_KEY_USD: None, METRIC_KEY_INPUT_TOKENS: None})
        assert c == CostBreakdown()


# ---------------------------------------------------------------------------
# AttributionLink / attribution_to_extra / attribution_from_extra
# ---------------------------------------------------------------------------


class TestAttributionLink:
    def test_to_extra_renders_list(self) -> None:
        links = [AttributionLink(seq=3, role="retry_of", notes="timeout")]
        extra = attribution_to_extra(links)
        assert extra[EXTRA_KEY_ATTRIBUTION] == [
            {"seq": 3, "role": "retry_of", "notes": "timeout"}
        ]

    def test_to_extra_with_redundancy_adds_field(self) -> None:
        extra = attribution_to_extra([], redundancy="cached_hit")
        assert extra[EXTRA_KEY_REDUNDANCY] == "cached_hit"

    def test_to_extra_without_redundancy_omits_field(self) -> None:
        extra = attribution_to_extra([])
        assert EXTRA_KEY_REDUNDANCY not in extra

    def test_round_trip(self) -> None:
        original = [
            AttributionLink(seq=1, role="caused_by"),
            AttributionLink(seq=2, role="reflection_of", notes="poor score"),
        ]
        roundtripped = attribution_from_extra(attribution_to_extra(original))
        assert roundtripped == original

    def test_from_extra_handles_missing_field(self) -> None:
        assert attribution_from_extra({}) == []

    def test_from_extra_skips_malformed_entries(self) -> None:
        raw = {
            EXTRA_KEY_ATTRIBUTION: [
                {"seq": 5, "role": "caused_by"},   # valid
                {"seq": "x"},                       # bad seq
                "not-a-dict",                       # bad shape
                {"seq": 7, "role": "alien_role"},   # unknown role -> falls back
            ]
        }
        out = attribution_from_extra(raw)
        assert len(out) == 2
        assert out[0].seq == 5
        assert out[1].seq == 7
        assert out[1].role == "derived_from"  # fallback


# ---------------------------------------------------------------------------
# TraceLogger D1 integration
# ---------------------------------------------------------------------------


class TestTraceLoggerD1:
    def test_log_prompt_records_cost_in_metrics(self, tmp_path: Path) -> None:
        log = tmp_path / "trace.jsonl"
        with TraceLogger(log, run_id="r") as tl:
            tl.log_prompt(
                "lit",
                prompt="hi",
                response="hello",
                model="claude-haiku-4-5",
                cost=CostBreakdown(usd=0.001, input_tokens=10, output_tokens=5),
            )
        lines = _read_lines(log)
        prompt_line = next(l for l in lines if l["kind"] == KIND_PROMPT)
        assert prompt_line["metrics"][METRIC_KEY_USD] == pytest.approx(0.001)
        assert prompt_line["metrics"][METRIC_KEY_INPUT_TOKENS] == 10
        assert prompt_line["metrics"][METRIC_KEY_OUTPUT_TOKENS] == 5

    def test_log_prompt_records_attribution_and_redundancy(self, tmp_path: Path) -> None:
        log = tmp_path / "trace.jsonl"
        with TraceLogger(log, run_id="r") as tl:
            tl.log_prompt(
                "lit",
                prompt="hi",
                response="hello",
                model="m",
                attribution=[AttributionLink(seq=0, role="caused_by")],
                redundancy="speculative",
            )
        lines = _read_lines(log)
        prompt_line = next(l for l in lines if l["kind"] == KIND_PROMPT)
        attrs = prompt_line["extra"][EXTRA_KEY_ATTRIBUTION]
        assert attrs[0]["seq"] == 0 and attrs[0]["role"] == "caused_by"
        assert prompt_line["extra"][EXTRA_KEY_REDUNDANCY] == "speculative"

    def test_log_tool_call_supports_d1(self, tmp_path: Path) -> None:
        log = tmp_path / "trace.jsonl"
        with TraceLogger(log, run_id="r") as tl:
            tl.log_tool_call(
                "fs.read",
                input_payload={"path": "a"},
                output_payload={"bytes": 10},
                cost=CostBreakdown(usd=0.0),  # free local tool
                redundancy="cached_hit",
            )
        lines = _read_lines(log)
        tool_line = next(l for l in lines if l["actor"] == "fs.read")
        assert tool_line["extra"][EXTRA_KEY_REDUNDANCY] == "cached_hit"

    def test_log_step_generic_kind(self, tmp_path: Path) -> None:
        log = tmp_path / "trace.jsonl"
        with TraceLogger(log, run_id="r") as tl:
            tl.log_step(
                "custom",
                kind="vla.replan",
                output_payload={"decision": "abort"},
                cost=CostBreakdown(usd=0.0001),
                attribution=[AttributionLink(seq=1, role="retry_of")],
                redundancy="retried",
            )
        lines = _read_lines(log)
        custom_line = next(l for l in lines if l["kind"] == "vla.replan")
        assert custom_line["metrics"][METRIC_KEY_USD] == pytest.approx(0.0001)
        assert custom_line["extra"][EXTRA_KEY_REDUNDANCY] == "retried"

    def test_caller_metric_wins_over_d1(self, tmp_path: Path) -> None:
        log = tmp_path / "trace.jsonl"
        with TraceLogger(log, run_id="r") as tl:
            tl.log_step(
                "x",
                kind="custom",
                metrics={METRIC_KEY_USD: 9.99},  # caller-supplied
                cost=CostBreakdown(usd=0.001),  # D1 helper
            )
        lines = _read_lines(log)
        line = next(l for l in lines if l["kind"] == "custom")
        # caller value preserved (setdefault semantics)
        assert line["metrics"][METRIC_KEY_USD] == 9.99


# ---------------------------------------------------------------------------
# summarize_costs
# ---------------------------------------------------------------------------


def _entry(seq: int, actor: str, kind: str, **cost_kwargs) -> object:
    c = CostBreakdown(**cost_kwargs)
    return make_entry(
        "r", seq, actor, kind, metrics=cost_to_metrics(c)
    )


class TestSummarizeCosts:
    def test_sums_across_entries(self) -> None:
        entries = [
            _entry(0, "lit", "llm.prompt", usd=0.01, input_tokens=100, output_tokens=50),
            _entry(1, "hyp", "llm.prompt", usd=0.02, input_tokens=200, output_tokens=100),
        ]
        s = summarize_costs(entries)
        assert s.total_usd == pytest.approx(0.03)
        assert s.total_input_tokens == 300
        assert s.total_output_tokens == 150
        assert s.n_entries_costed == 2

    def test_breakdown_by_actor_and_kind(self) -> None:
        entries = [
            _entry(0, "lit", "llm.prompt", usd=0.01),
            _entry(1, "hyp", "llm.prompt", usd=0.02),
            _entry(2, "hyp", "agent.run", usd=0.04),
        ]
        s = summarize_costs(entries)
        assert s.by_actor["lit"] == pytest.approx(0.01)
        assert s.by_actor["hyp"] == pytest.approx(0.06)
        assert s.by_kind["llm.prompt"] == pytest.approx(0.03)
        assert s.by_kind["agent.run"] == pytest.approx(0.04)

    def test_zero_cost_entries_dont_count(self) -> None:
        entries = [
            _entry(0, "a", "llm.prompt"),  # all zero
            _entry(1, "b", "llm.prompt", usd=0.005),
        ]
        s = summarize_costs(entries)
        assert s.total_usd == pytest.approx(0.005)
        assert s.n_entries_costed == 1

    def test_non_usd_currency_skipped_from_total(self) -> None:
        from llmesh.core.cost_attribution import METRIC_KEY_CURRENCY
        # one USD, one JPY: JPY should be skipped from USD total
        e_usd = _entry(0, "a", "llm.prompt", usd=0.01, input_tokens=10)
        m_jpy = cost_to_metrics(CostBreakdown(usd=100.0, input_tokens=20))
        m_jpy[METRIC_KEY_CURRENCY] = "JPY"
        e_jpy = make_entry("r", 1, "b", "llm.prompt", metrics=m_jpy)
        s = summarize_costs([e_usd, e_jpy])
        assert s.total_usd == pytest.approx(0.01)
        # tokens still aggregate across currencies
        assert s.total_input_tokens == 30

    def test_empty_returns_zero_summary(self) -> None:
        s = summarize_costs([])
        assert s.total_usd == 0.0
        assert s.n_entries_costed == 0
        assert s.by_actor == {}


# ---------------------------------------------------------------------------
# build_attribution_chain
# ---------------------------------------------------------------------------


def _attr_entry(seq: int, links: list[AttributionLink]) -> object:
    return make_entry(
        "r", seq, "a", "step", extra=attribution_to_extra(links)
    )


class TestBuildAttributionChain:
    def test_linear_chain(self) -> None:
        entries = [
            _attr_entry(0, []),
            _attr_entry(1, [AttributionLink(seq=0, role="caused_by")]),
            _attr_entry(2, [AttributionLink(seq=1, role="reflection_of")]),
        ]
        chain = build_attribution_chain(entries, target_seq=2)
        assert [e.seq for e in chain] == [2, 1, 0]

    def test_branching_chain(self) -> None:
        entries = [
            _attr_entry(0, []),
            _attr_entry(1, []),
            _attr_entry(
                2,
                [
                    AttributionLink(seq=0, role="caused_by"),
                    AttributionLink(seq=1, role="derived_from"),
                ],
            ),
        ]
        chain = build_attribution_chain(entries, target_seq=2)
        assert chain[0].seq == 2
        ancestor_seqs = sorted(e.seq for e in chain[1:])
        assert ancestor_seqs == [0, 1]

    def test_unknown_target_returns_empty(self) -> None:
        entries = [_attr_entry(0, [])]
        assert build_attribution_chain(entries, target_seq=99) == []

    def test_cycle_does_not_infinite_loop(self) -> None:
        entries = [
            _attr_entry(0, [AttributionLink(seq=1)]),
            _attr_entry(1, [AttributionLink(seq=0)]),  # cycle
        ]
        chain = build_attribution_chain(entries, target_seq=0)
        # both seqs visited at most once
        assert sorted(e.seq for e in chain) == [0, 1]


# ---------------------------------------------------------------------------
# count_redundancy
# ---------------------------------------------------------------------------


class TestCountRedundancy:
    def test_counts_known_flags(self) -> None:
        entries = [
            make_entry(
                "r", 0, "a", "step", extra=attribution_to_extra([], redundancy="novel")
            ),
            make_entry(
                "r", 1, "a", "step", extra=attribution_to_extra([], redundancy="novel")
            ),
            make_entry(
                "r", 2, "a", "step",
                extra=attribution_to_extra([], redundancy="cached_hit"),
            ),
            make_entry("r", 3, "a", "step"),  # unlabelled
        ]
        out = count_redundancy(entries)
        assert out == {"novel": 2, "cached_hit": 1, "unlabelled": 1}

    def test_unknown_flag_falls_into_unlabelled(self) -> None:
        e = make_entry("r", 0, "a", "step", extra={EXTRA_KEY_REDUNDANCY: "alien"})
        assert count_redundancy([e]) == {"unlabelled": 1}

    def test_is_redundant_helper(self) -> None:
        assert is_redundant("duplicate") is True
        assert is_redundant("cached_hit") is True
        assert is_redundant("novel") is False
        assert is_redundant("retried") is False  # not pruneable; counts as work done
        assert is_redundant(None) is False
