"""Tests for llmesh.skills.router (Phase 3.3)."""
from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from llmesh.mcp.server import app
from llmesh.skills import SkillChunk, SkillReplica
from llmesh.skills.router import (
    get_corrupt_reports,
    get_notifications,
    reset_state,
    set_replica,
)

client = TestClient(app, raise_server_exceptions=False)


def _make_chunk(skill_id: str, body: bytes = b"hello") -> SkillChunk:
    sk = Ed25519PrivateKey.generate()
    return SkillChunk.create_unsigned(
        skill_id=skill_id, version="v1", body=body, license="Apache-2.0"
    ).sign(sk)


@pytest.fixture
def replica(tmp_path: Path) -> SkillReplica:
    reset_state()
    rep = SkillReplica(tmp_path)
    set_replica(rep)
    yield rep
    set_replica(None)
    reset_state()


def test_get_chunk_returns_json(replica: SkillReplica) -> None:
    chunk = _make_chunk("a/b/c")
    replica.put(chunk)
    response = client.get("/skills/a/b/c")
    assert response.status_code == 200
    data = response.json()
    assert data["skill_id"] == "a/b/c"
    assert data["license"] == "Apache-2.0"


def test_get_chunk_not_found(replica: SkillReplica) -> None:
    response = client.get("/skills/does/not/exist")
    assert response.status_code == 404


def test_index_returns_all(replica: SkillReplica) -> None:
    replica.put(_make_chunk("alpha"))
    replica.put(_make_chunk("beta"))
    response = client.get("/skills/index")
    assert response.status_code == 200
    ids = sorted(r["skill_id"] for r in response.json()["chunks"])
    assert ids == ["alpha", "beta"]


def test_notify_records_payload(replica: SkillReplica) -> None:
    response = client.post(
        "/skills/notify",
        json={"skill_id": "remote/new", "version": "v2", "peer_endpoint": "http://peer.test"},
    )
    assert response.status_code == 200
    assert response.json()["accepted"] is True
    notes = get_notifications()
    assert len(notes) == 1
    assert notes[0]["skill_id"] == "remote/new"


def test_notify_rejects_missing_skill_id(replica: SkillReplica) -> None:
    response = client.post("/skills/notify", json={"version": "v1"})
    assert response.status_code == 400


def test_notify_rejects_non_json(replica: SkillReplica) -> None:
    response = client.post("/skills/notify", content="not json")
    assert response.status_code == 400


def test_report_corrupt_records(replica: SkillReplica) -> None:
    response = client.post(
        "/skills/a/b/report-corrupt",
        json={"by": "did:test:reporter", "rationale": "hash mismatch"},
    )
    assert response.status_code == 200
    assert response.json()["recorded"] is True
    reports = get_corrupt_reports()
    assert len(reports) == 1
    assert reports[0]["skill_id"] == "a/b"
    assert reports[0]["by"] == "did:test:reporter"


def test_report_corrupt_without_body(replica: SkillReplica) -> None:
    response = client.post("/skills/foo/report-corrupt")
    assert response.status_code == 200
    reports = get_corrupt_reports()
    assert reports[-1]["skill_id"] == "foo"


def test_unconfigured_replica_returns_503() -> None:
    reset_state()
    set_replica(None)
    response = client.get("/skills/index")
    assert response.status_code == 503
