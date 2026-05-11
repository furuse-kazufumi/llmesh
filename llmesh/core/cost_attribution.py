"""Cost-aware tracing and failure-attribution chains (Phase 12, D1).

The Phase 0b :class:`~llmesh.core.trace_logger.TraceLogger` records
*what happened* (prompts, tool calls, evaluations). D1 asks for
*how much it cost* and *why it happened* on top of that, in a way
that's grep-friendly and replayable. Two small additions cover both:

- :class:`CostBreakdown` — per-step USD + token counts (input /
  output / cached). Lives in :attr:`TraceEntry.metrics` under
  stable keys so an aggregator can sum across a run without parsing
  free-form text.
- :class:`AttributionLink` — points back at the prior ``seq`` that
  caused this step (``"reflection_of"``, ``"retry_of"``, ``"caused_by"``
  etc.). A chain of links is enough to reconstruct *why this entry
  exists* without re-running the agent.

A :class:`RedundancyFlag` rounds it out: marking an entry as
``duplicate`` / ``retried`` / ``cached_hit`` / ``speculative`` is
what lets the next phase prune them with confidence.

The aggregation helpers (``summarize_costs``, ``build_attribution_chain``,
``count_redundancy``) read back what the helpers wrote, so a downstream
viewer (llove dashboards, paper exporters) doesn't need to know the
JSON keys directly. That contract is the differentiator: competing
tracers expose cost and lineage as two separate sub-systems; here
they're one append-only file with first-class accessors.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal

from llmesh.core.trace import TraceEntry


# ---------------------------------------------------------------------------
# Per-step cost
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CostBreakdown:
    """USD cost + token accounting for one trace step.

    All fields are non-negative and default to zero so a tool that
    doesn't bill (e.g. a local Python function) can be logged with
    a zero-cost breakdown without bespoke handling. ``cached_tokens``
    are reported separately from ``input_tokens`` because most LLM
    providers price them differently — we keep both raw counts so a
    re-pricer can produce a different USD figure without re-tracing.
    """

    usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    currency: str = "USD"  # informational; the agg helpers ignore non-USD

    def __post_init__(self) -> None:
        for name, value in (
            ("usd", self.usd),
            ("input_tokens", self.input_tokens),
            ("output_tokens", self.output_tokens),
            ("cached_tokens", self.cached_tokens),
        ):
            if value < 0:
                raise ValueError(f"{name} must be >= 0 (got {value})")


# Standard keys used in :attr:`TraceEntry.metrics` so an aggregator
# can find cost data without parsing free-form notes.
METRIC_KEY_USD = "cost_usd"
METRIC_KEY_INPUT_TOKENS = "input_tokens"
METRIC_KEY_OUTPUT_TOKENS = "output_tokens"
METRIC_KEY_CACHED_TOKENS = "cached_tokens"
METRIC_KEY_CURRENCY = "currency"


def cost_to_metrics(cost: CostBreakdown) -> dict[str, Any]:
    """Render a :class:`CostBreakdown` as a metrics-dict patch.

    Returned dict is a fresh, mutation-safe copy — callers can merge
    it into their own metrics without aliasing concerns.
    """
    return {
        METRIC_KEY_USD: float(cost.usd),
        METRIC_KEY_INPUT_TOKENS: int(cost.input_tokens),
        METRIC_KEY_OUTPUT_TOKENS: int(cost.output_tokens),
        METRIC_KEY_CACHED_TOKENS: int(cost.cached_tokens),
        METRIC_KEY_CURRENCY: cost.currency,
    }


def cost_from_metrics(metrics: dict[str, Any]) -> CostBreakdown:
    """Reverse of :func:`cost_to_metrics`. Tolerates missing keys.

    A trace entry written before D1 will lack the standard keys; this
    returns a zero-cost breakdown for those rather than raising, so
    aggregators don't have to special-case the pre-D1 corpus.
    """
    return CostBreakdown(
        usd=float(metrics.get(METRIC_KEY_USD, 0.0) or 0.0),
        input_tokens=int(metrics.get(METRIC_KEY_INPUT_TOKENS, 0) or 0),
        output_tokens=int(metrics.get(METRIC_KEY_OUTPUT_TOKENS, 0) or 0),
        cached_tokens=int(metrics.get(METRIC_KEY_CACHED_TOKENS, 0) or 0),
        currency=str(metrics.get(METRIC_KEY_CURRENCY, "USD") or "USD"),
    )


# ---------------------------------------------------------------------------
# Attribution chain
# ---------------------------------------------------------------------------


AttributionRole = Literal[
    "reflection_of",      # this entry reflects on / critiques a prior step
    "retry_of",           # this entry retries a failed prior step
    "caused_by",          # this entry was triggered by a prior step's output
    "supersedes",         # this entry replaces a prior step's result
    "evaluation_of",      # this entry scores a prior step
    "derived_from",       # generic upstream dependency
]


@dataclass(frozen=True)
class AttributionLink:
    """One edge of the attribution graph.

    ``seq`` is the prior :attr:`TraceEntry.seq` we're pointing at;
    ``role`` says how this current entry relates to it. ``notes``
    is free-form for the human reviewer (model name, failure mode,
    etc.) — aggregators do not parse it.
    """

    seq: int
    role: AttributionRole = "derived_from"
    notes: str = ""


# Standard keys used in :attr:`TraceEntry.extra`.
EXTRA_KEY_ATTRIBUTION = "attribution"
EXTRA_KEY_REDUNDANCY = "redundancy"


def attribution_to_extra(
    links: Iterable[AttributionLink],
    *,
    redundancy: "RedundancyFlag | None" = None,
) -> dict[str, Any]:
    """Render attribution + redundancy as an extra-dict patch.

    ``links`` is materialised eagerly so passing in a generator is
    safe. Empty links are still recorded as an empty list so a viewer
    can distinguish "no upstream" from "field absent".
    """
    out: dict[str, Any] = {
        EXTRA_KEY_ATTRIBUTION: [
            {"seq": int(lk.seq), "role": lk.role, "notes": lk.notes}
            for lk in links
        ],
    }
    if redundancy is not None:
        out[EXTRA_KEY_REDUNDANCY] = redundancy
    return out


def attribution_from_extra(extra: dict[str, Any]) -> list[AttributionLink]:
    """Reverse of :func:`attribution_to_extra`. Returns ``[]`` on absent / malformed."""
    raw = extra.get(EXTRA_KEY_ATTRIBUTION) or []
    out: list[AttributionLink] = []
    if not isinstance(raw, list):
        return out
    for r in raw:
        if not isinstance(r, dict):
            continue
        try:
            seq = int(r.get("seq"))
        except (TypeError, ValueError):
            continue
        role_raw = str(r.get("role", "derived_from"))
        role: AttributionRole = role_raw if role_raw in _ATTR_ROLES else "derived_from"  # type: ignore[assignment]
        out.append(AttributionLink(seq=seq, role=role, notes=str(r.get("notes", ""))))
    return out


_ATTR_ROLES: frozenset[str] = frozenset(
    {
        "reflection_of",
        "retry_of",
        "caused_by",
        "supersedes",
        "evaluation_of",
        "derived_from",
    }
)


# ---------------------------------------------------------------------------
# Redundancy classification
# ---------------------------------------------------------------------------


RedundancyFlag = Literal[
    "novel",          # default — step did genuinely new work
    "duplicate",      # exact repeat of a prior step's input/output
    "retried",        # second-or-later attempt at the same intent
    "cached_hit",     # response served from a cache; no model call
    "speculative",    # produced for a possible branch, may be discarded
]


_REDUNDANCY_FLAGS: frozenset[str] = frozenset(
    {"novel", "duplicate", "retried", "cached_hit", "speculative"}
)


def is_redundant(flag: str | None) -> bool:
    """True if ``flag`` represents an entry the pruner can drop without loss."""
    return flag in ("duplicate", "cached_hit")


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CostSummary:
    """Aggregate of :class:`CostBreakdown` across many trace entries."""

    total_usd: float
    total_input_tokens: int
    total_output_tokens: int
    total_cached_tokens: int
    by_actor: dict[str, float] = field(default_factory=dict)   # actor -> usd
    by_kind: dict[str, float] = field(default_factory=dict)    # kind -> usd
    n_entries_costed: int = 0  # entries that contributed any non-zero cost


def summarize_costs(entries: Iterable[TraceEntry]) -> CostSummary:
    """Sum cost across trace entries, broken down by actor and kind.

    Entries with no cost metadata (pre-D1 or genuinely free) contribute
    zeros; they don't increment ``n_entries_costed``. Currency strings
    other than ``"USD"`` are skipped from the USD totals to avoid
    silently mixing currencies — log them in the same run on purpose
    if you need a multi-currency view, or convert upstream.
    """
    total_usd = 0.0
    total_in = 0
    total_out = 0
    total_cached = 0
    by_actor: Counter[str] = Counter()
    by_kind: Counter[str] = Counter()
    n_costed = 0
    for e in entries:
        c = cost_from_metrics(e.metrics)
        if c.currency != "USD":
            # tokens still count, USD does not
            total_in += c.input_tokens
            total_out += c.output_tokens
            total_cached += c.cached_tokens
            continue
        any_value = (
            c.usd > 0 or c.input_tokens or c.output_tokens or c.cached_tokens
        )
        if any_value:
            n_costed += 1
        total_usd += c.usd
        total_in += c.input_tokens
        total_out += c.output_tokens
        total_cached += c.cached_tokens
        by_actor[e.actor] += c.usd
        by_kind[e.kind] += c.usd
    return CostSummary(
        total_usd=total_usd,
        total_input_tokens=total_in,
        total_output_tokens=total_out,
        total_cached_tokens=total_cached,
        by_actor=dict(by_actor),
        by_kind=dict(by_kind),
        n_entries_costed=n_costed,
    )


def build_attribution_chain(
    entries: list[TraceEntry], target_seq: int
) -> list[TraceEntry]:
    """Walk back from ``target_seq`` through attribution links.

    Returns the entries in reverse-causal order: the target first, then
    its immediate ancestors, then theirs. Cycles (which shouldn't happen
    but defensive code stays defensive) are detected and the walk stops.
    Unknown ``seq`` references are skipped silently — a partial chain is
    more useful than no chain when an upstream log was truncated.
    """
    by_seq = {e.seq: e for e in entries}
    if target_seq not in by_seq:
        return []
    chain: list[TraceEntry] = []
    seen: set[int] = set()
    frontier: list[int] = [target_seq]
    while frontier:
        next_frontier: list[int] = []
        for seq in frontier:
            if seq in seen:
                continue
            seen.add(seq)
            entry = by_seq.get(seq)
            if entry is None:
                continue
            chain.append(entry)
            for lk in attribution_from_extra(entry.extra):
                if lk.seq not in seen:
                    next_frontier.append(lk.seq)
        frontier = next_frontier
    return chain


def count_redundancy(entries: Iterable[TraceEntry]) -> dict[str, int]:
    """Count entries per :data:`RedundancyFlag` value (plus ``"unlabelled"``).

    Useful for the dashboard view: a single bar chart "novel vs retried
    vs cached_hit" lets a reviewer spot wasted work at a glance.
    """
    out: Counter[str] = Counter()
    for e in entries:
        flag = e.extra.get(EXTRA_KEY_REDUNDANCY)
        if isinstance(flag, str) and flag in _REDUNDANCY_FLAGS:
            out[flag] += 1
        else:
            out["unlabelled"] += 1
    return dict(out)


__all__ = [
    "AttributionLink",
    "AttributionRole",
    "CostBreakdown",
    "CostSummary",
    "EXTRA_KEY_ATTRIBUTION",
    "EXTRA_KEY_REDUNDANCY",
    "METRIC_KEY_CACHED_TOKENS",
    "METRIC_KEY_CURRENCY",
    "METRIC_KEY_INPUT_TOKENS",
    "METRIC_KEY_OUTPUT_TOKENS",
    "METRIC_KEY_USD",
    "RedundancyFlag",
    "attribution_from_extra",
    "attribution_to_extra",
    "build_attribution_chain",
    "cost_from_metrics",
    "cost_to_metrics",
    "count_redundancy",
    "is_redundant",
    "summarize_costs",
]
