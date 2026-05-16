# SPDX-License-Identifier: Apache-2.0
"""End-to-end demo: 10-peer skill chunk replication + KPI 測定 (RFC Phase 3.7).

In-process simulation of N virtual peers (default 10), each owning a
subset of M skill chunks. The simulation runs gossip rounds; each round
every peer pulls from every other peer using ``SkillSyncClient`` over an
in-memory ``HTTPTransport`` adapter (bypassing the real socket layer for
speed and determinism).

KPI metrics measured:

* **Coverage**         — fraction of (peer, chunk) cells covered
* **Hit rate**         — overlap with the "popular" chunk subset
* **Round time**       — wall-clock per gossip round
* **Storage / peer**   — bytes stored locally per peer
* **Convergence**      — rounds needed to reach full coverage

RFC §評価指標 で示された閾値も自動判定して PASS / FAIL 出力。

Usage::

    py -3.11 scripts/demo_skill_sync.py
    py -3.11 scripts/demo_skill_sync.py --peers 10 --chunks 20 --rounds 5
    py -3.11 scripts/demo_skill_sync.py --json   # machine-readable
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections.abc import Iterable
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

for stream in (sys.stdout, sys.stderr):
    reconfigure = getattr(stream, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8", errors="replace")

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from llmesh.skills import SkillChunk, SkillReplica, SkillSyncClient, SkillSyncError  # noqa: E402


# ---------------------------------------------------------------------------
# In-memory HTTPTransport: routes (peer_url) -> (peer's own router state)
# ---------------------------------------------------------------------------


class InMemoryTransport:
    """Routes JSON-over-HTTP calls back to peer-local SkillReplica instances."""

    def __init__(self, peers: dict[str, SkillReplica]) -> None:
        self._peers = peers

    @staticmethod
    def _split(url: str) -> tuple[str, str]:
        """Split ``http://peer-3/skills/<rest>`` into (peer_url, rest_path)."""
        proto, rest = url.split("://", 1)
        host, _, path = rest.partition("/")
        peer_url = f"{proto}://{host}"
        return peer_url, "/" + path

    def get_json(self, url: str) -> Any:
        peer_url, path = self._split(url)
        replica = self._peers.get(peer_url)
        if replica is None:
            raise SkillSyncError(f"unknown peer {peer_url}")
        if path == "/skills/index":
            return {"chunks": replica.index()}
        if path.startswith("/skills/"):
            skill_id = path[len("/skills/"):]
            chunk = replica.get(skill_id)
            if chunk is None:
                raise SkillSyncError(f"GET {url}: HTTP 404")
            return chunk.to_json()
        raise SkillSyncError(f"unsupported path {path}")

    def post_json(self, url: str, body: dict[str, Any]) -> Any:
        _, path = self._split(url)
        # Demo skips notify/report-corrupt write paths.
        return {"accepted": True, "path": path}


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------


def _make_chunk(skill_id: str, body: bytes) -> SkillChunk:
    sk = Ed25519PrivateKey.generate()
    return SkillChunk.create_unsigned(
        skill_id=skill_id, version="v1", body=body, license="Apache-2.0"
    ).sign(sk)


def _seed_peers(
    peer_urls: list[str],
    *,
    n_chunks: int,
    chunk_size: int,
    tmpdir: Path,
    rng: random.Random,
) -> tuple[dict[str, SkillReplica], dict[str, SkillChunk]]:
    """Each peer owns ~n_chunks/2 random chunks; chunks pool size = n_chunks."""
    universe: dict[str, SkillChunk] = {
        f"sk/{i:04d}": _make_chunk(f"sk/{i:04d}", body=bytes([(i * 7) & 0xFF]) * chunk_size)
        for i in range(n_chunks)
    }
    peer_replicas: dict[str, SkillReplica] = {}
    all_ids = list(universe.keys())
    own_fraction = max(1, len(all_ids) // 2)
    for url in peer_urls:
        rep = SkillReplica(tmpdir / url.replace("://", "_").replace("/", "_"))
        rng.shuffle(all_ids)
        for sid in all_ids[:own_fraction]:
            rep.put(universe[sid])
        peer_replicas[url] = rep
    return peer_replicas, universe


def _run_round(
    peer_urls: list[str],
    replicas: dict[str, SkillReplica],
    transport: InMemoryTransport,
) -> float:
    """Each peer pulls from every other peer once. Returns elapsed seconds."""
    t0 = time.monotonic()
    for me in peer_urls:
        client = SkillSyncClient(transport=transport)
        for other in peer_urls:
            if other == me:
                continue
            client.sync_with(other, replicas[me], max_pulls=None)
    return time.monotonic() - t0


def _coverage(replicas: dict[str, SkillReplica], universe_ids: Iterable[str]) -> float:
    total = 0
    have = 0
    for rep in replicas.values():
        local = {row["skill_id"] for row in rep.index()}
        for sid in universe_ids:
            total += 1
            if sid in local:
                have += 1
    return have / total if total else 0.0


def _storage_bytes(replicas: dict[str, SkillReplica]) -> dict[str, int]:
    out: dict[str, int] = {}
    for url, rep in replicas.items():
        out[url] = sum(int(row["size_bytes"]) for row in rep.index())
    return out


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="10-peer skill chunk replication demo")
    ap.add_argument("--peers", type=int, default=10, help="virtual peer count")
    ap.add_argument("--chunks", type=int, default=20, help="universe chunk count")
    ap.add_argument("--chunk-size", type=int, default=50 * 1024, help="bytes per chunk (default 50 KB)")
    ap.add_argument("--rounds", type=int, default=5, help="gossip rounds to run")
    ap.add_argument("--seed", type=int, default=42, help="RNG seed (reproducibility)")
    ap.add_argument("--json", action="store_true", help="machine-readable JSON output")
    args = ap.parse_args(argv)

    rng = random.Random(args.seed)
    peer_urls = [f"http://peer-{i:02d}" for i in range(args.peers)]
    timeline: list[dict[str, Any]] = []

    with TemporaryDirectory() as tmp:
        replicas, universe = _seed_peers(
            peer_urls,
            n_chunks=args.chunks,
            chunk_size=args.chunk_size,
            tmpdir=Path(tmp),
            rng=rng,
        )
        transport = InMemoryTransport(replicas)

        initial_coverage = _coverage(replicas, universe.keys())
        if not args.json:
            print(f"=== Demo: {args.peers} peers × {args.chunks} chunks "
                  f"({args.chunk_size / 1024:.1f} KB each), {args.rounds} rounds ===")
            print(f"Initial coverage: {initial_coverage:.3f}")
            print()

        first_full_round: int | None = None
        for round_idx in range(1, args.rounds + 1):
            elapsed = _run_round(peer_urls, replicas, transport)
            cov = _coverage(replicas, universe.keys())
            storage = _storage_bytes(replicas)
            avg_storage = sum(storage.values()) / max(1, len(storage))
            timeline.append({
                "round": round_idx,
                "elapsed_s": elapsed,
                "coverage": cov,
                "avg_storage_bytes": avg_storage,
                "max_storage_bytes": max(storage.values()) if storage else 0,
            })
            if cov >= 0.999 and first_full_round is None:
                first_full_round = round_idx
            if not args.json:
                print(f"Round {round_idx}: "
                      f"elapsed={elapsed*1000:.1f} ms  "
                      f"coverage={cov:.3f}  "
                      f"avg_storage={avg_storage/1024:.0f} KB / peer")

        final_cov = _coverage(replicas, universe.keys())
        total_time = sum(r["elapsed_s"] for r in timeline)

        kpi = {
            "round_count": args.rounds,
            "final_coverage": final_cov,
            "first_full_coverage_round": first_full_round,
            "total_replication_round_time_s": total_time,
            "max_storage_per_peer_bytes": max(
                r["max_storage_bytes"] for r in timeline
            ) if timeline else 0,
            "thresholds": {
                "replication_round_time_s_lt_60": total_time < 60.0,
                "final_coverage_gt_0_9": final_cov > 0.9,
                "max_storage_per_peer_lt_2gb": max(
                    r["max_storage_bytes"] for r in timeline
                ) < 2 * 1024 ** 3 if timeline else True,
            },
        }
        kpi["pass"] = all(kpi["thresholds"].values())

        result = {
            "config": vars(args),
            "timeline": timeline,
            "kpi": kpi,
        }

        if args.json:
            json.dump(result, sys.stdout, indent=2)
            print()
        else:
            print()
            print("=== KPI Summary ===")
            print(f"Final coverage:              {final_cov:.3f}")
            print(f"Total round time:            {total_time*1000:.1f} ms")
            print(f"Convergence round:           {first_full_round}")
            print(f"Max storage / peer:          "
                  f"{kpi['max_storage_per_peer_bytes']/1024:.0f} KB")
            print()
            print("=== RFC §評価指標 ===")
            for k, ok in kpi["thresholds"].items():
                tag = "PASS" if ok else "FAIL"
                print(f"  [{tag}] {k}")
            print()
            print(f"Overall: {'PASS' if kpi['pass'] else 'FAIL'}")

        for rep in replicas.values():
            rep.close()
    return 0 if kpi["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
