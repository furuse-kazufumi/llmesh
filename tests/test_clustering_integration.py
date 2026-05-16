"""Integration: NodeRegistry.find_matching + POST /registry/query (Phase 2a)."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from llmesh.discovery.clustering import CapabilityQuery
from llmesh.discovery.registry import NodeEntry, NodeRegistry
from llmesh.discovery.router import set_registry
from llmesh.mcp.server import app

client = TestClient(app, raise_server_exceptions=False)


def _inject_entry(
    reg: NodeRegistry,
    *,
    node_id: str,
    tools: list[str],
    domains: list[str],
    languages: list[str],
    model_size: str = "",
    data_levels: list[int] | None = None,
    ttl: int = 3600,
) -> None:
    """Manually inject a NodeEntry (bypassing signature verification)."""
    now = time.time()
    manifest = {
        "tools": tools,
        "domains": domains,
        "languages": languages,
        "model_size": model_size,
        "data_levels_accepted": data_levels or [0, 1, 2],
    }
    entry = NodeEntry(
        node_id=node_id,
        did=f"did:test:{node_id}",
        endpoint=f"http://{node_id}.test:9000",
        subnets=["test"],
        tools=tools,
        public_key_hex="00" * 32,
        registered_at=now,
        expires_at=now + ttl,
        display_name=node_id,
        manifest_dict=manifest,
    )
    reg._nodes[node_id] = entry  # bypass signature verify for test


# --- NodeRegistry.find_matching --------------------------------------------


class TestFindMatching:
    def setup_method(self) -> None:
        self.reg = NodeRegistry(verify_signatures=False)
        _inject_entry(
            self.reg,
            node_id="ja-code",
            tools=["chat"],
            domains=["code"],
            languages=["ja"],
            model_size="7B",
        )
        _inject_entry(
            self.reg,
            node_id="en-math",
            tools=["chat", "math"],
            domains=["math"],
            languages=["en"],
            model_size="13B",
        )
        _inject_entry(
            self.reg,
            node_id="ja-en-code-math",
            tools=["chat", "math"],
            domains=["code", "math"],
            languages=["ja", "en"],
            model_size="7B",
        )

    def test_returns_top_k_sorted(self) -> None:
        query = CapabilityQuery(
            preferred_domains=frozenset({"code", "math"}),
            preferred_languages=frozenset({"ja"}),
        )
        result = self.reg.find_matching(query, k=3)
        node_ids = [entry.node_id for _, entry in result]
        # Perfect match (both domains + ja language)
        assert node_ids[0] == "ja-en-code-math"
        assert result[0][0] == 1.0

    def test_filters_by_required_tools(self) -> None:
        query = CapabilityQuery(required_tools=frozenset({"math"}))
        result = self.reg.find_matching(query, k=5)
        node_ids = sorted(entry.node_id for _, entry in result)
        # ja-code has only "chat", so excluded
        assert node_ids == ["en-math", "ja-en-code-math"]

    def test_returns_empty_when_no_match(self) -> None:
        query = CapabilityQuery(required_tools=frozenset({"nonexistent"}))
        assert self.reg.find_matching(query, k=5) == []

    def test_k_limits_results(self) -> None:
        query = CapabilityQuery()  # all match with score 1.0
        result = self.reg.find_matching(query, k=2)
        assert len(result) == 2


# --- POST /registry/query --------------------------------------------------


class TestQueryEndpoint:
    def setup_method(self) -> None:
        reg = NodeRegistry(verify_signatures=False)
        _inject_entry(
            reg,
            node_id="alpha",
            tools=["chat"],
            domains=["code"],
            languages=["ja"],
            model_size="7B",
        )
        _inject_entry(
            reg,
            node_id="beta",
            tools=["chat", "math"],
            domains=["math"],
            languages=["en"],
            model_size="13B",
        )
        set_registry(reg)

    def test_query_basic_match(self) -> None:
        response = client.post(
            "/registry/query",
            json={"preferred_domains": ["code"], "k": 5},
        )
        assert response.status_code == 200
        data = response.json()
        node_ids = [m["node_id"] for m in data["matches"]]
        assert "alpha" in node_ids

    def test_query_required_tools_excludes(self) -> None:
        response = client.post(
            "/registry/query",
            json={"required_tools": ["math"], "k": 5},
        )
        assert response.status_code == 200
        node_ids = [m["node_id"] for m in response.json()["matches"]]
        assert node_ids == ["beta"]

    def test_query_invalid_k_returns_400(self) -> None:
        response = client.post(
            "/registry/query",
            json={"k": 0},
        )
        assert response.status_code == 400

    def test_query_k_too_large_returns_400(self) -> None:
        response = client.post(
            "/registry/query",
            json={"k": 999},
        )
        assert response.status_code == 400

    def test_query_returns_score_node_id_endpoint_did(self) -> None:
        response = client.post("/registry/query", json={"k": 5})
        assert response.status_code == 200
        for match in response.json()["matches"]:
            assert set(match.keys()) == {"score", "node_id", "endpoint", "did"}
            assert 0.0 < match["score"] <= 1.0

    def test_query_empty_body_returns_all(self) -> None:
        response = client.post("/registry/query", json={})
        assert response.status_code == 200
        assert len(response.json()["matches"]) == 2


@pytest.fixture(autouse=True)
def _reset_registry():
    """Each test gets its own injected registry; reset after."""
    yield
    set_registry(NodeRegistry(verify_signatures=True))
