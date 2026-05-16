# SPDX-License-Identifier: Apache-2.0
"""End-to-end demo: capability clustering across multiple virtual peers.

llmesh の NodeRegistry に複数の virtual peer (異なる capabilities) を
inject し、HTTP API (POST /registry/query) を叩いて clustering の挙動を
体感する scriptable demo。FastAPI の TestClient で in-process HTTP を
動かすので、外部 process は不要。

Usage::

    py -3.11 scripts/demo_clustering.py [--json]

`--json` flag を付けると最後に全 query 結果を JSON dump (CI / 機械処理用).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

for stream in (sys.stdout, sys.stderr):
    reconfigure = getattr(stream, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8", errors="replace")

from fastapi.testclient import TestClient

from llmesh.discovery.registry import NodeEntry, NodeRegistry
from llmesh.discovery.router import set_registry
from llmesh.mcp.server import app


PEERS: list[dict[str, Any]] = [
    {
        "node_id": "ja-code-7B",
        "tools": ["chat", "embed"],
        "domains": ["code"],
        "languages": ["ja"],
        "model_size": "7B",
        "data_levels": [0, 1],
    },
    {
        "node_id": "en-code-7B",
        "tools": ["chat"],
        "domains": ["code"],
        "languages": ["en"],
        "model_size": "7B",
        "data_levels": [0, 1, 2],
    },
    {
        "node_id": "en-math-13B",
        "tools": ["chat", "math"],
        "domains": ["math"],
        "languages": ["en"],
        "model_size": "13B",
        "data_levels": [0, 1, 2],
    },
    {
        "node_id": "multi-lang-7B",
        "tools": ["chat", "embed", "math"],
        "domains": ["code", "math"],
        "languages": ["ja", "en", "zh"],
        "model_size": "7B",
        "data_levels": [0, 1, 2],
    },
    {
        "node_id": "private-only-7B",
        "tools": ["chat"],
        "domains": ["legal"],
        "languages": ["ja"],
        "model_size": "7B",
        "data_levels": [0],  # accepts only level-0 (most private) data
    },
]


QUERIES: list[dict[str, Any]] = [
    {
        "name": "Japanese coding assistance",
        "body": {
            "preferred_domains": ["code"],
            "preferred_languages": ["ja"],
            "k": 3,
        },
    },
    {
        "name": "Math computation, English",
        "body": {
            "required_tools": ["math"],
            "preferred_languages": ["en"],
            "k": 3,
        },
    },
    {
        "name": "High data sensitivity (level >= 2 only)",
        "body": {
            "min_data_level": 2,
            "preferred_domains": ["code", "math"],
            "k": 5,
        },
    },
    {
        "name": "Embedding tool required, any language",
        "body": {
            "required_tools": ["embed"],
            "k": 5,
        },
    },
    {
        "name": "Anything goes (no preferences)",
        "body": {"k": 5},
    },
]


def _inject_peers(reg: NodeRegistry) -> None:
    now = time.time()
    for p in PEERS:
        manifest = {
            "tools": p["tools"],
            "domains": p["domains"],
            "languages": p["languages"],
            "model_size": p["model_size"],
            "data_levels_accepted": p["data_levels"],
        }
        entry = NodeEntry(
            node_id=p["node_id"],
            did=f"did:demo:{p['node_id']}",
            endpoint=f"http://{p['node_id']}.demo:9000",
            subnets=["demo"],
            tools=p["tools"],
            public_key_hex="00" * 32,
            registered_at=now,
            expires_at=now + 3600,
            display_name=p["node_id"],
            manifest_dict=manifest,
        )
        reg._nodes[p["node_id"]] = entry


def _format_peer(p: dict[str, Any]) -> str:
    tools = "+".join(p["tools"])
    langs = "+".join(p["languages"])
    doms = "+".join(p["domains"])
    return f"{p['node_id']:20s} ({langs:10s} | {doms:12s} | {p['model_size']:4s} | tools={tools})"


def _print_header() -> None:
    print("=" * 78)
    print(" llmesh — Capability Clustering Demo (RFC Phase 2a)")
    print("=" * 78)
    print()
    print("Registered peers:")
    for p in PEERS:
        print(f"  - {_format_peer(p)}")
    print()


def _run_query(client: TestClient, query: dict[str, Any]) -> dict[str, Any]:
    name = query["name"]
    body = query["body"]
    print("-" * 78)
    print(f"Query: {name}")
    print(f"  body: {json.dumps(body, ensure_ascii=False)}")
    response = client.post("/registry/query", json=body)
    assert response.status_code == 200, f"unexpected status: {response.status_code}"
    data = response.json()
    print(f"  Top {len(data['matches'])}:")
    for match in data["matches"]:
        score = match["score"]
        nid = match["node_id"]
        print(f"    {score:>4.2f}  {nid}")
    print()
    return {"name": name, "body": body, "matches": data["matches"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="dump full results as JSON at the end")
    args = parser.parse_args(argv)

    reg = NodeRegistry(verify_signatures=False)
    _inject_peers(reg)
    set_registry(reg)

    client = TestClient(app, raise_server_exceptions=False)

    _print_header()
    results: list[dict[str, Any]] = []
    for query in QUERIES:
        results.append(_run_query(client, query))

    if args.json:
        print("=" * 78)
        print(" JSON dump")
        print("=" * 78)
        print(json.dumps({"peers": PEERS, "queries": results}, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
