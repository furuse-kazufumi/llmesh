"""Tests for Phase 3.6c — router glue (reputation + rate limit) + sync hook."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from llmesh.mcp.server import app
from llmesh.skills import (
    PeerReputation,
    SkillChunk,
    SkillReplica,
    SkillSyncClient,
)
from llmesh.skills.router import (
    RateLimiter,
    get_corrupt_reports,
    reset_state,
    set_rate_limiter,
    set_replica,
    set_reputation,
)

client = TestClient(app, raise_server_exceptions=False)


def _make_chunk(skill_id: str, body: bytes = b"hello") -> SkillChunk:
    sk = Ed25519PrivateKey.generate()
    return SkillChunk.create_unsigned(
        skill_id=skill_id, version="v1", body=body, license="Apache-2.0"
    ).sign(sk)


@pytest.fixture
def replica(tmp_path: Path):
    reset_state()
    rep = SkillReplica(tmp_path)
    set_replica(rep)
    yield rep
    set_replica(None)
    set_reputation(None)
    set_rate_limiter(None)
    reset_state()


# ---------------------------------------------------------------------------
# report-corrupt → PeerReputation glue
# ---------------------------------------------------------------------------


def test_report_corrupt_feeds_reputation(replica: SkillReplica) -> None:
    rep = PeerReputation()
    try:
        set_reputation(rep)
        resp = client.post(
            "/skills/some/skill/report-corrupt",
            json={"against": "did:key:eve", "by": "did:key:alice", "rationale": "bad merkle"},
        )
        assert resp.status_code == 200
        assert resp.json()["reputation_updated"] is True
        stats = rep.stats("did:key:eve")
        assert stats.corruptions == 1
    finally:
        rep.close()


def test_report_corrupt_without_against_skips_reputation(replica: SkillReplica) -> None:
    rep = PeerReputation()
    try:
        set_reputation(rep)
        resp = client.post(
            "/skills/some/skill/report-corrupt",
            json={"by": "did:key:alice"},
        )
        assert resp.status_code == 200
        assert resp.json()["reputation_updated"] is False
        # In-memory queue still recorded (backward compat).
        reports = get_corrupt_reports()
        assert reports[-1]["skill_id"] == "some/skill"
    finally:
        rep.close()


def test_report_corrupt_without_reputation_is_no_op(replica: SkillReplica) -> None:
    # No set_reputation call.
    resp = client.post(
        "/skills/some/skill/report-corrupt",
        json={"against": "did:key:eve"},
    )
    assert resp.status_code == 200
    assert resp.json()["reputation_updated"] is False


# ---------------------------------------------------------------------------
# RateLimiter on notify / report-corrupt
# ---------------------------------------------------------------------------


def test_rate_limiter_returns_429_after_threshold(replica: SkillReplica) -> None:
    clock = [0.0]
    limiter = RateLimiter(max_events=2, window_s=60.0, clock=lambda: clock[0])
    set_rate_limiter(limiter)

    for _ in range(2):
        r = client.post(
            "/skills/notify",
            json={"skill_id": "x"},
            headers={"X-Peer-Id": "did:key:alice"},
        )
        assert r.status_code == 200

    r3 = client.post(
        "/skills/notify",
        json={"skill_id": "x"},
        headers={"X-Peer-Id": "did:key:alice"},
    )
    assert r3.status_code == 429
    assert r3.json()["detail"] == "rate_limited"


def test_rate_limiter_window_expires(replica: SkillReplica) -> None:
    clock = [0.0]
    limiter = RateLimiter(max_events=1, window_s=5.0, clock=lambda: clock[0])
    set_rate_limiter(limiter)

    headers = {"X-Peer-Id": "did:key:bob"}
    assert client.post("/skills/notify", json={"skill_id": "a"}, headers=headers).status_code == 200
    assert client.post("/skills/notify", json={"skill_id": "a"}, headers=headers).status_code == 429
    # Advance past the window
    clock[0] = 10.0
    assert client.post("/skills/notify", json={"skill_id": "a"}, headers=headers).status_code == 200


def test_rate_limiter_keys_are_per_peer(replica: SkillReplica) -> None:
    limiter = RateLimiter(max_events=1, window_s=60.0, clock=lambda: 0.0)
    set_rate_limiter(limiter)

    assert client.post(
        "/skills/notify", json={"skill_id": "a"}, headers={"X-Peer-Id": "p1"}
    ).status_code == 200
    # Different peer: not blocked
    assert client.post(
        "/skills/notify", json={"skill_id": "a"}, headers={"X-Peer-Id": "p2"}
    ).status_code == 200
    # Original peer again: blocked
    assert client.post(
        "/skills/notify", json={"skill_id": "a"}, headers={"X-Peer-Id": "p1"}
    ).status_code == 429


def test_rate_limiter_invalid_args() -> None:
    with pytest.raises(ValueError):
        RateLimiter(max_events=0)
    with pytest.raises(ValueError):
        RateLimiter(window_s=0)


# ---------------------------------------------------------------------------
# SkillSyncClient.reputation hook
# ---------------------------------------------------------------------------


class _TestClientTransport:
    def __init__(self, http: TestClient) -> None:
        self._http = http

    @staticmethod
    def _path(url: str) -> str:
        if "://" in url:
            return "/" + url.split("://", 1)[1].split("/", 1)[1]
        return url

    def get_json(self, url: str) -> Any:
        r = self._http.get(self._path(url))
        if r.status_code >= 400:
            from llmesh.skills import SkillSyncError
            raise SkillSyncError(f"GET {url}: HTTP {r.status_code}")
        return r.json()

    def post_json(self, url: str, body: dict[str, Any]) -> Any:
        return self._http.post(self._path(url), json=body).json()


def test_sync_with_records_transfer_on_pull(
    replica: SkillReplica, tmp_path: Path
) -> None:
    replica.put(_make_chunk("alpha"))
    replica.put(_make_chunk("beta"))

    local = SkillReplica(tmp_path / "local")
    rep = PeerReputation()
    try:
        sync = SkillSyncClient(
            transport=_TestClientTransport(client),
            reputation=rep,
        )
        result = sync.sync_with("http://peer.test", local)
        assert len(result.pulled) == 2
        # 2 pulls → 2 transfer records under the peer URL key
        stats = rep.stats("http://peer.test")
        assert stats.transfers == 2
        assert stats.verdict == "trusted"
    finally:
        rep.close()
        local.close()


def test_sync_with_skipped_chunks_do_not_count_as_transfer(
    replica: SkillReplica, tmp_path: Path
) -> None:
    chunk = _make_chunk("shared")
    replica.put(chunk)
    local = SkillReplica(tmp_path / "local")
    local.put(chunk)

    rep = PeerReputation()
    try:
        sync = SkillSyncClient(
            transport=_TestClientTransport(client),
            reputation=rep,
        )
        result = sync.sync_with("http://peer.test", local)
        assert result.pulled == ()
        assert result.skipped_existing == ("shared",)
        # Skipped chunks must not inflate transfer count.
        assert rep.stats("http://peer.test").transfers == 0
    finally:
        rep.close()
        local.close()
