"""Tests for llmesh.skills.sync (Phase 3.4 Pull/Push/Gossip protocol)."""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from llmesh.mcp.server import app
from llmesh.skills import (
    DEFAULT_ALLOWED_LICENSES,
    GossipScheduler,
    PolicyDecision,
    SkillChunk,
    SkillReplica,
    SkillSyncClient,
    SkillSyncError,
    SyncResult,
    allow_licenses,
)
from llmesh.skills.router import reset_state, set_replica


# ---------------------------------------------------------------------------
# In-process FastAPI transport — lets the urllib-shaped client talk to the
# Phase 3.3 router without binding a TCP socket.
# ---------------------------------------------------------------------------


class _TestClientTransport:
    """HTTPTransport adapter wrapping fastapi.testclient.TestClient.

    The real router lives behind a global singleton (``set_replica``); this
    transport ignores the ``base_url`` part of incoming URLs and forwards
    only the path so each test can swap which replica is "the peer."
    """

    def __init__(self, http: TestClient) -> None:
        self._http = http

    @staticmethod
    def _path(url: str) -> str:
        # Strip "http://peer.test" prefix if present, keep path + query.
        if "://" in url:
            return "/" + url.split("://", 1)[1].split("/", 1)[1]
        return url

    def get_json(self, url: str) -> Any:
        resp = self._http.get(self._path(url))
        if resp.status_code >= 400:
            raise SkillSyncError(f"GET {url}: HTTP {resp.status_code}")
        return resp.json()

    def post_json(self, url: str, body: dict[str, Any]) -> Any:
        resp = self._http.post(self._path(url), json=body)
        if resp.status_code >= 400:
            raise SkillSyncError(f"POST {url}: HTTP {resp.status_code}")
        return resp.json()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

PEER_URL = "http://peer.test"


def _make_chunk(
    skill_id: str, body: bytes = b"hello", license: str = "Apache-2.0"  # noqa: A002
) -> SkillChunk:
    sk = Ed25519PrivateKey.generate()
    return SkillChunk.create_unsigned(
        skill_id=skill_id, version="v1", body=body, license=license
    ).sign(sk)


@pytest.fixture
def remote_replica(tmp_path: Path) -> SkillReplica:
    """The 'peer' replica: served via the router, accessed by SkillSyncClient."""
    reset_state()
    rep = SkillReplica(tmp_path / "remote")
    set_replica(rep)
    yield rep
    set_replica(None)
    reset_state()


@pytest.fixture
def local_replica(tmp_path: Path) -> SkillReplica:
    """The local replica that sync_with writes into."""
    return SkillReplica(tmp_path / "local")


@pytest.fixture
def client(remote_replica: SkillReplica) -> SkillSyncClient:
    http = TestClient(app, raise_server_exceptions=False)
    return SkillSyncClient(transport=_TestClientTransport(http))


# ---------------------------------------------------------------------------
# Low-level: pull_chunk / pull_index / notify
# ---------------------------------------------------------------------------


def test_pull_chunk_returns_signed_chunk(
    client: SkillSyncClient, remote_replica: SkillReplica
) -> None:
    chunk = _make_chunk("a/b/c", body=b"payload")
    remote_replica.put(chunk)

    pulled = client.pull_chunk(PEER_URL, "a/b/c")
    assert pulled is not None
    assert pulled.skill_id == "a/b/c"
    assert pulled.body == b"payload"
    assert pulled.content_sha256 == chunk.content_sha256
    assert pulled.signature == chunk.signature


def test_pull_chunk_returns_none_when_missing(client: SkillSyncClient) -> None:
    assert client.pull_chunk(PEER_URL, "nope/missing") is None


def test_pull_index_lists_all_chunks(
    client: SkillSyncClient, remote_replica: SkillReplica
) -> None:
    remote_replica.put(_make_chunk("alpha"))
    remote_replica.put(_make_chunk("beta"))

    rows = client.pull_index(PEER_URL)
    ids = sorted(r["skill_id"] for r in rows)
    assert ids == ["alpha", "beta"]
    # content_sha is the diff key used by sync_with.
    assert all("content_sha" in r for r in rows)


def test_notify_round_trips_through_router(client: SkillSyncClient) -> None:
    result = client.notify(
        PEER_URL,
        "remote/new",
        version="v2",
        merkle_root="deadbeef" * 8,
        peer_endpoint="http://producer.test",
        license="Apache-2.0",
    )
    assert result["accepted"] is True


# ---------------------------------------------------------------------------
# High-level: sync_with
# ---------------------------------------------------------------------------


def test_sync_with_pulls_missing_chunks(
    client: SkillSyncClient,
    remote_replica: SkillReplica,
    local_replica: SkillReplica,
) -> None:
    remote_replica.put(_make_chunk("alpha", b"a"))
    remote_replica.put(_make_chunk("beta", b"b"))

    result = client.sync_with(PEER_URL, local_replica)

    assert isinstance(result, SyncResult)
    assert sorted(result.pulled) == ["alpha", "beta"]
    assert result.skipped_existing == ()
    assert result.failed == ()
    assert local_replica.get("alpha") is not None
    assert local_replica.get("beta") is not None


def test_sync_with_skips_existing_chunks(
    client: SkillSyncClient,
    remote_replica: SkillReplica,
    local_replica: SkillReplica,
) -> None:
    chunk = _make_chunk("alpha", b"shared")
    remote_replica.put(chunk)
    local_replica.put(chunk)

    result = client.sync_with(PEER_URL, local_replica)

    assert result.pulled == ()
    assert result.skipped_existing == ("alpha",)


def test_sync_with_repulls_when_content_changes(
    client: SkillSyncClient,
    remote_replica: SkillReplica,
    local_replica: SkillReplica,
) -> None:
    old = _make_chunk("alpha", b"v1")
    local_replica.put(old)
    new = _make_chunk("alpha", b"v2-different")
    remote_replica.put(new)

    result = client.sync_with(PEER_URL, local_replica)

    assert result.pulled == ("alpha",)
    refreshed = local_replica.get("alpha")
    assert refreshed is not None
    assert refreshed.body == b"v2-different"


def test_sync_with_respects_max_pulls(
    client: SkillSyncClient,
    remote_replica: SkillReplica,
    local_replica: SkillReplica,
) -> None:
    for i in range(5):
        remote_replica.put(_make_chunk(f"skill-{i}", body=f"body-{i}".encode()))

    result = client.sync_with(PEER_URL, local_replica, max_pulls=2)

    assert len(result.pulled) == 2


def test_sync_with_records_index_failure() -> None:
    class _BrokenTransport:
        def get_json(self, url: str) -> Any:
            raise SkillSyncError("connection refused")

        def post_json(self, url: str, body: dict[str, Any]) -> Any:
            raise SkillSyncError("connection refused")

    client = SkillSyncClient(transport=_BrokenTransport())
    replica = SkillReplica(Path("."))  # lazily created by test cleanup
    try:
        result = client.sync_with(PEER_URL, replica)
    finally:
        replica.close()

    assert result.pulled == ()
    assert len(result.failed) == 1
    assert result.failed[0][0] == "<index>"


# ---------------------------------------------------------------------------
# GossipScheduler
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Phase 3.5: policy gate
# ---------------------------------------------------------------------------


def test_policy_denies_pull_records_skill_id(
    remote_replica: SkillReplica, local_replica: SkillReplica
) -> None:
    remote_replica.put(_make_chunk("trusted/ok"))
    remote_replica.put(_make_chunk("untrusted/blocked"))

    def gate(peer_url: str, skill_id: str) -> PolicyDecision:
        return "approved" if skill_id.startswith("trusted/") else "denied"

    http = TestClient(app, raise_server_exceptions=False)
    client = SkillSyncClient(transport=_TestClientTransport(http), policy=gate)

    result = client.sync_with(PEER_URL, local_replica)

    assert sorted(result.pulled) == ["trusted/ok"]
    assert sorted(result.denied) == ["untrusted/blocked"]
    assert local_replica.get("trusted/ok") is not None
    assert local_replica.get("untrusted/blocked") is None


def test_policy_exception_is_treated_as_deny(
    remote_replica: SkillReplica, local_replica: SkillReplica
) -> None:
    remote_replica.put(_make_chunk("alpha"))

    def broken(peer_url: str, skill_id: str) -> PolicyDecision:
        raise RuntimeError("policy backend down")

    http = TestClient(app, raise_server_exceptions=False)
    client = SkillSyncClient(transport=_TestClientTransport(http), policy=broken)

    result = client.sync_with(PEER_URL, local_replica)

    assert result.pulled == ()
    assert result.denied == ("alpha",)
    assert result.failed == ()
    assert local_replica.get("alpha") is None


def test_no_policy_means_no_gate(
    client: SkillSyncClient,
    remote_replica: SkillReplica,
    local_replica: SkillReplica,
) -> None:
    remote_replica.put(_make_chunk("any"))
    result = client.sync_with(PEER_URL, local_replica)
    assert result.denied == ()
    assert result.pulled == ("any",)


# ---------------------------------------------------------------------------
# Phase 3.6a: license filter
# ---------------------------------------------------------------------------


def test_allow_licenses_accepts_listed(
    remote_replica: SkillReplica, local_replica: SkillReplica
) -> None:
    remote_replica.put(_make_chunk("ok/apache", license="Apache-2.0"))
    remote_replica.put(_make_chunk("ok/mit", license="MIT"))

    http = TestClient(app, raise_server_exceptions=False)
    client = SkillSyncClient(
        transport=_TestClientTransport(http),
        license_filter=allow_licenses({"Apache-2.0", "MIT"}),
    )

    result = client.sync_with(PEER_URL, local_replica)
    assert sorted(result.pulled) == ["ok/apache", "ok/mit"]
    assert result.denied_license == ()


def test_allow_licenses_rejects_unlisted(
    remote_replica: SkillReplica, local_replica: SkillReplica
) -> None:
    remote_replica.put(_make_chunk("ok/apache", license="Apache-2.0"))
    remote_replica.put(_make_chunk("bad/proprietary", license="Proprietary"))

    http = TestClient(app, raise_server_exceptions=False)
    client = SkillSyncClient(
        transport=_TestClientTransport(http),
        license_filter=allow_licenses({"Apache-2.0"}),
    )

    result = client.sync_with(PEER_URL, local_replica)
    assert result.pulled == ("ok/apache",)
    assert result.denied_license == ("bad/proprietary",)
    assert local_replica.get("bad/proprietary") is None


def test_default_allowed_licenses_contains_recommended_set() -> None:
    for spdx in ("Apache-2.0", "MIT", "CC0-1.0", "CC-BY-4.0"):
        assert spdx in DEFAULT_ALLOWED_LICENSES


def test_license_filter_exception_is_treated_as_reject(
    remote_replica: SkillReplica, local_replica: SkillReplica
) -> None:
    remote_replica.put(_make_chunk("any"))

    def broken(chunk: SkillChunk) -> bool:
        raise RuntimeError("filter backend down")

    http = TestClient(app, raise_server_exceptions=False)
    client = SkillSyncClient(transport=_TestClientTransport(http), license_filter=broken)

    result = client.sync_with(PEER_URL, local_replica)
    assert result.pulled == ()
    assert result.denied_license == ("any",)


# ---------------------------------------------------------------------------
# GossipScheduler
# ---------------------------------------------------------------------------


def test_gossip_scheduler_tick_pulls_from_each_peer(
    client: SkillSyncClient,
    remote_replica: SkillReplica,
    local_replica: SkillReplica,
) -> None:
    remote_replica.put(_make_chunk("gamma"))

    sched = GossipScheduler(
        client,
        local_replica,
        peer_provider=lambda: [PEER_URL],
        interval_s=60,
    )
    results = sched.tick()

    assert PEER_URL in results
    assert results[PEER_URL].pulled == ("gamma",)
    assert sched.last_results()[PEER_URL].pulled == ("gamma",)


def test_gossip_scheduler_swallows_provider_errors(
    client: SkillSyncClient, local_replica: SkillReplica
) -> None:
    def boom() -> list[str]:
        raise RuntimeError("registry unreachable")

    sched = GossipScheduler(client, local_replica, peer_provider=boom)
    # Must not raise; returns empty mapping.
    assert sched.tick() == {}


def test_gossip_scheduler_start_stop_runs_at_least_one_round(
    client: SkillSyncClient,
    remote_replica: SkillReplica,
    local_replica: SkillReplica,
) -> None:
    remote_replica.put(_make_chunk("delta"))

    seen = threading.Event()

    def provider() -> list[str]:
        seen.set()
        return [PEER_URL]

    sched = GossipScheduler(
        client, local_replica, peer_provider=provider, interval_s=1
    )
    sched.start()
    try:
        # Wait for at least one round to fire (immediate).
        assert seen.wait(timeout=3.0)
        # Allow the round to finish before checking the replica.
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if local_replica.get("delta") is not None:
                break
            time.sleep(0.05)
    finally:
        sched.stop()

    assert local_replica.get("delta") is not None
