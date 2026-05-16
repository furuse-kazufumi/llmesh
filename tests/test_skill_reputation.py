"""Tests for llmesh.skills.reputation (Phase 3.6b)."""
from __future__ import annotations

import pytest

from llmesh.skills import PeerReputation, PeerStats


@pytest.fixture
def clock_box() -> list[float]:
    """Mutable container so a test can advance the fake clock in-place."""
    return [1_000_000.0]


@pytest.fixture
def rep(clock_box: list[float]) -> PeerReputation:
    r = PeerReputation(clock=lambda: clock_box[0])
    yield r
    r.close()


def test_unknown_peer_is_trusted(rep: PeerReputation) -> None:
    stats = rep.stats("did:key:unknown")
    assert stats.transfers == 0
    assert stats.corruptions == 0
    assert stats.score == 1.0
    assert stats.verdict == "trusted"


def test_perfect_peer_stays_trusted(rep: PeerReputation) -> None:
    for _ in range(10):
        rep.record_transfer("did:key:alice")
    assert rep.verdict("did:key:alice") == "trusted"
    assert rep.score("did:key:alice") == 1.0


def test_partial_corruption_drops_to_warn(rep: PeerReputation) -> None:
    for _ in range(10):
        rep.record_transfer("did:key:bob")
    # 4 / 10 = 0.4 corruption rate → score 0.6 → warn (0.5 <= 0.6 < 0.7)
    for _ in range(4):
        rep.record_corruption("did:key:bob")
    stats = rep.stats("did:key:bob")
    assert stats.score == pytest.approx(0.6)
    assert stats.verdict == "warn"


def test_heavy_corruption_blocks_peer(rep: PeerReputation) -> None:
    for _ in range(10):
        rep.record_transfer("did:key:eve")
    for _ in range(6):
        rep.record_corruption("did:key:eve")
    stats = rep.stats("did:key:eve")
    assert stats.score == pytest.approx(0.4)
    assert stats.verdict == "blocked"


def test_reputation_filtered_drops_blocked_only(rep: PeerReputation) -> None:
    # bob: warn — kept (with log warning)
    for _ in range(10):
        rep.record_transfer("did:key:bob")
    for _ in range(4):
        rep.record_corruption("did:key:bob")
    # eve: blocked — filtered out
    for _ in range(10):
        rep.record_transfer("did:key:eve")
    for _ in range(6):
        rep.record_corruption("did:key:eve")

    out = rep.reputation_filtered(
        ["did:key:trusted", "did:key:bob", "did:key:eve", "did:key:also_ok"]
    )
    assert out == ["did:key:trusted", "did:key:bob", "did:key:also_ok"]


def test_window_drops_old_events(
    rep: PeerReputation, clock_box: list[float]
) -> None:
    # 31-day-old burst of corruption
    for _ in range(10):
        rep.record_transfer("did:key:carol")
    for _ in range(8):
        rep.record_corruption("did:key:carol")
    # Advance clock past the 30-day window
    clock_box[0] += 31 * 24 * 60 * 60
    # Fresh activity is clean
    for _ in range(5):
        rep.record_transfer("did:key:carol")
    stats = rep.stats("did:key:carol")
    assert stats.transfers == 5
    assert stats.corruptions == 0
    assert stats.verdict == "trusted"


def test_prune_removes_expired_rows(
    rep: PeerReputation, clock_box: list[float]
) -> None:
    for _ in range(3):
        rep.record_transfer("did:key:dan")
        rep.record_corruption("did:key:dan")
    clock_box[0] += 31 * 24 * 60 * 60
    removed = rep.prune()
    assert removed == 6
    # No in-window data → trusted (unknown-peer default)
    assert rep.verdict("did:key:dan") == "trusted"


def test_invalid_thresholds_rejected() -> None:
    with pytest.raises(ValueError):
        PeerReputation(warn_threshold=0.3, block_threshold=0.5)
    with pytest.raises(ValueError):
        PeerReputation(warn_threshold=1.5)
    with pytest.raises(ValueError):
        PeerReputation(block_threshold=-0.1)


def test_custom_thresholds_take_effect(clock_box: list[float]) -> None:
    strict = PeerReputation(
        clock=lambda: clock_box[0], warn_threshold=0.95, block_threshold=0.9
    )
    try:
        for _ in range(100):
            strict.record_transfer("did:key:p")
        for _ in range(7):  # 0.93 score → warn under strict thresholds
            strict.record_corruption("did:key:p")
        assert strict.verdict("did:key:p") == "warn"
    finally:
        strict.close()


def test_stats_is_immutable_dataclass(rep: PeerReputation) -> None:
    stats = rep.stats("did:key:any")
    assert isinstance(stats, PeerStats)
    with pytest.raises(Exception):
        stats.transfers = 99  # type: ignore[misc]
