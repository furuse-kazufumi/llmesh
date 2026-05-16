"""Capability-aware clustering & peer matching (RFC Phase 2a).

`CapabilityProfile` summarises the parts of a peer's CapabilityManifest that
matter for clustering / query routing. Routing logic in `discovery/router.py`
or higher layers can use `matching_score()` + `pick_top_peers()` to send a
query only to peers that are likely to satisfy it, instead of broadcasting.

Pure functions — no I/O, no Zeroconf interaction. Safe to unit test in
isolation.

See `docs/llmesh_p2p_mesh_rfc.md` (the FullSense umbrella RFC).
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class CapabilityProfile:
    """Compact, hashable summary of a peer's capabilities for clustering.

    Built from a CapabilityManifest dict; only the fields relevant to query
    routing are kept (the rest is integrity-checked via capability_hash).
    """

    tools: frozenset[str] = field(default_factory=frozenset)
    domains: frozenset[str] = field(default_factory=frozenset)
    languages: frozenset[str] = field(default_factory=frozenset)
    model_size: str = ""
    """e.g. '4B', '7B', '13B'. Empty string = unknown."""
    data_levels: frozenset[int] = field(default_factory=frozenset)
    """Accepted privacy levels (e.g. {0, 1, 2})."""

    @classmethod
    def from_manifest(cls, manifest: dict[str, Any]) -> CapabilityProfile:
        """Build a profile from a signed CapabilityManifest dict.

        Unknown fields are silently ignored to stay forward-compatible.
        """
        return cls(
            tools=frozenset(str(t) for t in manifest.get("tools", []) or []),
            domains=frozenset(str(d) for d in manifest.get("domains", []) or []),
            languages=frozenset(str(language) for language in manifest.get("languages", []) or []),
            model_size=str(manifest.get("model_size", "") or ""),
            data_levels=frozenset(int(level) for level in manifest.get("data_levels_accepted", []) or []),
        )

    def cluster_key(self) -> str:
        """Compact partition key. Peers with the same key form a cluster.

        Format: ``size:{model_size}/lang:{langs}`` where ``langs`` is the
        sorted comma-joined languages. Empty fields collapse to ``-``.
        """
        size = self.model_size or "-"
        langs = ",".join(sorted(self.languages)) or "-"
        return f"size:{size}/lang:{langs}"


@dataclass(frozen=True)
class CapabilityQuery:
    """A query for the cluster: filter + ranking signal."""

    required_tools: frozenset[str] = field(default_factory=frozenset)
    """If non-empty, peers missing any of these are excluded outright."""
    preferred_domains: frozenset[str] = field(default_factory=frozenset)
    preferred_languages: frozenset[str] = field(default_factory=frozenset)
    min_data_level: int = 0
    """Reject peers whose max accepted privacy level is below this."""


# --- pure functions ---------------------------------------------------


def _max_or_minus_one(levels: Iterable[int]) -> int:
    """Return max(levels) or -1 if empty (used so 0 is a real level)."""
    items = list(levels)
    return max(items) if items else -1


def matching_score(profile: CapabilityProfile, query: CapabilityQuery) -> float:
    """Score a profile against a query. Range [0.0, 1.0].

    Behaviour:
      * Hard filter: required_tools must be subset of profile.tools.
      * Hard filter: profile must accept at least one level >= min_data_level.
      * Soft score: ratio of preferred_domains ∩ profile.domains (weight 0.5)
        plus ratio of preferred_languages ∩ profile.languages (weight 0.5).
      * If no preferences are specified, score is 1.0 when filters pass.

    Returns 0.0 for any peer that fails the hard filters.
    """
    # Hard filters. min_data_level == 0 means "no requirement" (default).
    if query.required_tools and not query.required_tools.issubset(profile.tools):
        return 0.0
    if query.min_data_level > 0:
        if _max_or_minus_one(profile.data_levels) < query.min_data_level:
            return 0.0

    # Soft scoring
    score_parts: list[float] = []
    if query.preferred_domains:
        overlap = len(query.preferred_domains & profile.domains)
        score_parts.append(overlap / len(query.preferred_domains))
    if query.preferred_languages:
        overlap = len(query.preferred_languages & profile.languages)
        score_parts.append(overlap / len(query.preferred_languages))

    if not score_parts:
        return 1.0  # filters passed and no preferences expressed
    return sum(score_parts) / len(score_parts)


def pick_top_peers(
    peers: Sequence[tuple[CapabilityProfile, T]],
    query: CapabilityQuery,
    *,
    k: int = 3,
) -> list[tuple[float, T]]:
    """Rank peers by matching_score and return top-k (score, peer).

    Peers with score 0.0 are excluded. Stable ordering: peers with the same
    score preserve input order.
    """
    scored: list[tuple[float, int, T]] = []
    for idx, (profile, peer) in enumerate(peers):
        s = matching_score(profile, query)
        if s > 0.0:
            scored.append((s, idx, peer))
    # Sort by score descending, then by input index for stability
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [(s, peer) for s, _idx, peer in scored[:k]]


def partition_peers(
    peers: Sequence[tuple[CapabilityProfile, T]],
) -> dict[str, list[T]]:
    """Group peers by `cluster_key()` for cluster-aware lookup.

    Returns ``{cluster_key: [peers ...]}``. Useful for DHT-like routing
    where a query for a cluster key is sent only to that partition.
    """
    out: dict[str, list[T]] = {}
    for profile, peer in peers:
        key = profile.cluster_key()
        out.setdefault(key, []).append(peer)
    return out


__all__ = [
    "CapabilityProfile",
    "CapabilityQuery",
    "matching_score",
    "partition_peers",
    "pick_top_peers",
]
