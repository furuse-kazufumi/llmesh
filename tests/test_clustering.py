"""Tests for discovery/clustering.py (RFC Phase 2a)."""

from __future__ import annotations

from llmesh.discovery.clustering import (
    CapabilityProfile,
    CapabilityQuery,
    matching_score,
    partition_peers,
    pick_top_peers,
)


def test_profile_from_manifest_extracts_known_fields() -> None:
    manifest = {
        "tools": ["chat", "embed"],
        "domains": ["code", "math"],
        "languages": ["ja", "en"],
        "model_size": "7B",
        "data_levels_accepted": [0, 1, 2],
        "unknown_field": "should be ignored",
    }
    p = CapabilityProfile.from_manifest(manifest)
    assert p.tools == frozenset({"chat", "embed"})
    assert p.domains == frozenset({"code", "math"})
    assert p.languages == frozenset({"ja", "en"})
    assert p.model_size == "7B"
    assert p.data_levels == frozenset({0, 1, 2})


def test_profile_handles_missing_fields() -> None:
    p = CapabilityProfile.from_manifest({})
    assert p.tools == frozenset()
    assert p.model_size == ""
    assert p.data_levels == frozenset()


def test_cluster_key_format() -> None:
    p = CapabilityProfile(model_size="7B", languages=frozenset({"ja", "en"}))
    assert p.cluster_key() == "size:7B/lang:en,ja"  # sorted


def test_cluster_key_empty_fields() -> None:
    p = CapabilityProfile()
    assert p.cluster_key() == "size:-/lang:-"


def test_required_tools_hard_filter() -> None:
    p = CapabilityProfile(tools=frozenset({"chat"}))
    q = CapabilityQuery(required_tools=frozenset({"chat", "embed"}))
    assert matching_score(p, q) == 0.0  # embed missing


def test_min_data_level_hard_filter() -> None:
    p = CapabilityProfile(data_levels=frozenset({0, 1}))
    q = CapabilityQuery(min_data_level=2)
    assert matching_score(p, q) == 0.0


def test_min_data_level_satisfied_by_higher_level() -> None:
    p = CapabilityProfile(data_levels=frozenset({0, 1, 2}))
    q = CapabilityQuery(min_data_level=1)
    assert matching_score(p, q) > 0.0


def test_perfect_domain_match() -> None:
    p = CapabilityProfile(domains=frozenset({"code", "math"}))
    q = CapabilityQuery(preferred_domains=frozenset({"code", "math"}))
    assert matching_score(p, q) == 1.0


def test_partial_domain_match() -> None:
    p = CapabilityProfile(domains=frozenset({"code"}))
    q = CapabilityQuery(preferred_domains=frozenset({"code", "math"}))
    assert matching_score(p, q) == 0.5  # 1 / 2


def test_combined_domain_and_language_score() -> None:
    p = CapabilityProfile(domains=frozenset({"code"}), languages=frozenset({"ja"}))
    q = CapabilityQuery(
        preferred_domains=frozenset({"code", "math"}),
        preferred_languages=frozenset({"ja", "en"}),
    )
    # domain score 0.5, language score 0.5, average 0.5
    assert matching_score(p, q) == 0.5


def test_no_preferences_means_filters_passed() -> None:
    p = CapabilityProfile(tools=frozenset({"chat"}))
    q = CapabilityQuery()
    assert matching_score(p, q) == 1.0


def test_pick_top_peers_orders_by_score() -> None:
    peers = [
        (CapabilityProfile(domains=frozenset({"code"})), "peer-A"),
        (CapabilityProfile(domains=frozenset({"code", "math"})), "peer-B"),
        (CapabilityProfile(domains=frozenset({"music"})), "peer-C"),
    ]
    q = CapabilityQuery(preferred_domains=frozenset({"code", "math"}))
    top = pick_top_peers(peers, q, k=2)
    assert [name for _, name in top] == ["peer-B", "peer-A"]
    assert top[0][0] == 1.0
    assert top[1][0] == 0.5


def test_pick_top_peers_excludes_zero_score() -> None:
    peers = [
        (CapabilityProfile(tools=frozenset({"chat"})), "ok"),
        (CapabilityProfile(tools=frozenset()), "bad"),
    ]
    q = CapabilityQuery(required_tools=frozenset({"chat"}))
    top = pick_top_peers(peers, q, k=5)
    assert [name for _, name in top] == ["ok"]


def test_pick_top_peers_stable_for_ties() -> None:
    peers = [
        (CapabilityProfile(), f"peer-{i}") for i in range(5)
    ]
    q = CapabilityQuery()  # all score 1.0
    top = pick_top_peers(peers, q, k=3)
    assert [name for _, name in top] == ["peer-0", "peer-1", "peer-2"]


def test_partition_peers_groups_by_cluster_key() -> None:
    peers = [
        (CapabilityProfile(model_size="7B", languages=frozenset({"ja"})), "A"),
        (CapabilityProfile(model_size="7B", languages=frozenset({"ja"})), "B"),
        (CapabilityProfile(model_size="13B", languages=frozenset({"en"})), "C"),
    ]
    parts = partition_peers(peers)
    assert parts == {
        "size:7B/lang:ja": ["A", "B"],
        "size:13B/lang:en": ["C"],
    }
