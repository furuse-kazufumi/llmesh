"""Tests for llmesh.skills.replica (Phase 3.2)."""
from __future__ import annotations

import time
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from llmesh.skills import SkillChunk, SkillReplica


def _make_chunk(skill_id: str, *, body: bytes | None = None, license: str = "Apache-2.0") -> SkillChunk:  # noqa: A002
    body = body or (f"body for {skill_id}".encode() * 10)
    sk = Ed25519PrivateKey.generate()
    return SkillChunk.create_unsigned(
        skill_id=skill_id,
        version="v1",
        body=body,
        license=license,
    ).sign(sk)


def test_put_and_get_round_trip(tmp_path: Path) -> None:
    rep = SkillReplica(tmp_path)
    chunk = _make_chunk("a/b/c")
    rep.put(chunk)
    got = rep.get("a/b/c")
    assert got is not None
    assert got.body == chunk.body
    assert got.skill_id == "a/b/c"


def test_get_missing_returns_none(tmp_path: Path) -> None:
    rep = SkillReplica(tmp_path)
    assert rep.get("nonexistent") is None


def test_hit_count_increments(tmp_path: Path) -> None:
    rep = SkillReplica(tmp_path)
    rep.put(_make_chunk("popular"))
    for _ in range(5):
        rep.get("popular")
    idx = rep.index()
    row = next(r for r in idx if r["skill_id"] == "popular")
    assert row["hit_count"] == 5


def test_hot_to_warm_eviction(tmp_path: Path) -> None:
    """Adding more than hot_bytes triggers LRU demotion to warm tier."""
    rep = SkillReplica(tmp_path, hot_bytes=1500, warm_bytes=100 * 1024 * 1024)
    # Each chunk ~ 100 bytes (10 reps * "body for X" ~= 13 chars)
    # Use larger bodies to be deterministic
    big_body = b"x" * 600  # 600 bytes per chunk
    for i in range(5):
        rep.put(_make_chunk(f"s/{i}", body=big_body))
    # Hot cap = 1500 → only 2 chunks fit (1200 bytes), so 3 evicted to warm
    idx = rep.index()
    tiers = {r["skill_id"]: r["tier"] for r in idx}
    n_hot = sum(1 for t in tiers.values() if t == "hot")
    n_warm = sum(1 for t in tiers.values() if t == "warm")
    assert n_hot <= 3
    assert n_warm >= 2


def test_get_promotes_warm_back_to_hot(tmp_path: Path) -> None:
    rep = SkillReplica(tmp_path, hot_bytes=1500)
    big_body = b"y" * 600
    for i in range(5):
        rep.put(_make_chunk(f"k/{i}", body=big_body))
    # The earliest are now in warm; get one back
    got = rep.get("k/0")
    assert got is not None
    idx = {r["skill_id"]: r["tier"] for r in rep.index()}
    assert idx["k/0"] == "hot"


def test_warm_cap_deletes_oldest(tmp_path: Path) -> None:
    rep = SkillReplica(tmp_path, hot_bytes=500, warm_bytes=1500)
    big_body = b"z" * 600
    for i in range(10):
        rep.put(_make_chunk(f"d/{i}", body=big_body))
    # Hot has at most 0 entries (each > cap), all 10 went to warm.
    # warm cap = 1500 → only 2 fit, so 8 should have been deleted.
    rep.evict()  # explicit pass to force enforcement
    idx = rep.index()
    assert len(idx) <= 3  # surviving entries


def test_popularity_decays_with_time(tmp_path: Path) -> None:
    rep = SkillReplica(tmp_path, decay_hours=0.0001)  # very fast decay for test
    rep.put(_make_chunk("decaying"))
    rep.get("decaying")  # hit_count = 1
    initial = rep.popularity("decaying")
    time.sleep(0.5)  # plenty of decay time
    later = rep.popularity("decaying")
    assert later < initial


def test_index_returns_all_known_chunks(tmp_path: Path) -> None:
    rep = SkillReplica(tmp_path)
    rep.put(_make_chunk("a"))
    rep.put(_make_chunk("b"))
    rep.put(_make_chunk("c"))
    ids = sorted(r["skill_id"] for r in rep.index())
    assert ids == ["a", "b", "c"]


def test_put_replaces_existing(tmp_path: Path) -> None:
    rep = SkillReplica(tmp_path)
    rep.put(_make_chunk("same", body=b"old"))
    rep.put(_make_chunk("same", body=b"newer"))
    got = rep.get("same")
    assert got is not None
    assert got.body == b"newer"


def test_index_includes_tier_and_size(tmp_path: Path) -> None:
    rep = SkillReplica(tmp_path)
    chunk = _make_chunk("meta/test")
    rep.put(chunk)
    row = rep.index()[0]
    assert row["tier"] == "hot"
    assert row["size_bytes"] == chunk.size_bytes
    assert row["content_sha"] == chunk.content_sha256


def test_corrupt_warm_file_recovered_as_none(tmp_path: Path) -> None:
    rep = SkillReplica(tmp_path, hot_bytes=100)
    chunk = _make_chunk("corrupt-me", body=b"q" * 200)
    rep.put(chunk)
    # Force demotion
    rep.put(_make_chunk("another", body=b"r" * 200))
    # Corrupt the warm file
    warm = rep._warm_path("corrupt-me")
    if warm.exists():
        warm.write_text("{not json", encoding="utf-8")
    got = rep.get("corrupt-me")
    assert got is None
