"""Tests for speculative execution across the mesh (Phase 2 + Phase 3).

Ed25519 signing / tamper-evidence / idle-node selection (LAN-first) /
hit-miss-waste lifecycle / fail-closed signature verification /
honest-disclosure metrics.
"""
from __future__ import annotations

import dataclasses

import pytest

from llmesh.identity.node_id import NodeIdentity
from llmesh.speculative import (
    IdleNode,
    SignatureError,
    SignedManifest,
    SpeculativeManifest,
    SpeculativeMeshCoordinator,
    sign_manifest,
)


class StepClock:
    """Deterministic millisecond clock; advances by 1ms per read unless set."""

    def __init__(self, start: int = 1000) -> None:
        self.now = int(start)

    def __call__(self) -> int:
        return self.now

    def advance(self, dt_ms: int) -> None:
        self.now += int(dt_ms)


def _ident() -> NodeIdentity:
    return NodeIdentity.generate()


def _manifest(origin: str, **kw) -> SpeculativeManifest:
    kw.setdefault("branch", {"op": "insert", "value": 1})
    kw.setdefault("created_at_ms", 1000)
    return SpeculativeManifest.new(origin_node_id=origin, **kw)


# -- manifest + signing -----------------------------------------------------


def test_manifest_hash_is_deterministic_and_dict_order_independent():
    ident = _ident()
    m1 = SpeculativeManifest.new(
        origin_node_id=ident.node_id,
        branch={"a": 1, "b": 2},
        manifest_id="fixed",
        created_at_ms=1000,
    )
    m2 = SpeculativeManifest.new(
        origin_node_id=ident.node_id,
        branch={"b": 2, "a": 1},  # different insertion order
        manifest_id="fixed",
        created_at_ms=1000,
    )
    assert m1.manifest_hash == m2.manifest_hash
    assert len(m1.manifest_hash) == 64


def test_sign_and_verify_roundtrip():
    ident = _ident()
    m = _manifest(ident.node_id)
    signed = sign_manifest(m, ident)
    assert isinstance(signed, SignedManifest)
    assert signed.speculative is True
    assert signed.verify() is True
    assert signed.manifest_hash == m.manifest_hash
    assert signed.origin_pub_hex == ident.public_key_hex


def test_sign_rejects_foreign_origin():
    ident = _ident()
    other = _ident()
    m = _manifest(other.node_id)  # manifest claims a different origin
    with pytest.raises(SignatureError):
        sign_manifest(m, ident)


def test_verify_fails_on_tampered_branch():
    ident = _ident()
    m = _manifest(ident.node_id)
    signed = sign_manifest(m, ident)
    tampered_manifest = dataclasses.replace(m, branch={"op": "evil"})
    tampered = dataclasses.replace(signed, manifest=tampered_manifest)
    assert tampered.verify() is False


def test_verify_fails_on_wrong_pubkey():
    ident = _ident()
    other = _ident()
    m = _manifest(ident.node_id)
    signed = sign_manifest(m, ident)
    wrong = dataclasses.replace(signed, origin_pub_hex=other.public_key_hex)
    assert wrong.verify() is False


def test_verify_fails_on_malformed_signature_hex():
    ident = _ident()
    signed = sign_manifest(_manifest(ident.node_id), ident)
    bad = dataclasses.replace(signed, signature_hex="not-hex!!")
    assert bad.verify() is False


# -- idle-node selection (LAN-first) ----------------------------------------


def test_select_picks_least_loaded_lan_node():
    coord = SpeculativeMeshCoordinator(_ident(), max_load_score=5.0)
    nodes = [
        IdleNode("peer:A", pending_tasks=3, cpu_load=0.2),
        IdleNode("peer:B", pending_tasks=0, cpu_load=0.1),
        IdleNode("peer:C", pending_tasks=1, cpu_load=0.5),
    ]
    chosen = coord.select_idle_node(nodes)
    assert chosen.node_id == "peer:B"


def test_select_prefers_lan_over_idler_wan():
    coord = SpeculativeMeshCoordinator(_ident(), require_lan=False, max_load_score=5.0)
    nodes = [
        IdleNode("peer:WAN", pending_tasks=0, cpu_load=0.0, is_lan=False),
        IdleNode("peer:LAN", pending_tasks=1, cpu_load=0.2, is_lan=True),
    ]
    # LAN preferred even though WAN is idler.
    assert coord.select_idle_node(nodes).node_id == "peer:LAN"


def test_select_excludes_overloaded_nodes():
    coord = SpeculativeMeshCoordinator(_ident(), max_load_score=1.0)
    nodes = [IdleNode("peer:busy", pending_tasks=5, cpu_load=0.9)]
    assert coord.select_idle_node(nodes) is None


def test_select_filters_on_min_vram():
    coord = SpeculativeMeshCoordinator(_ident(), max_load_score=5.0)
    nodes = [IdleNode("peer:A", pending_tasks=0, vram_free_mb=512.0)]
    assert coord.select_idle_node(nodes, min_vram_mb=2048.0) is None
    assert coord.select_idle_node(nodes, min_vram_mb=256.0).node_id == "peer:A"


def test_require_lan_excludes_wan_by_default():
    coord = SpeculativeMeshCoordinator(_ident(), max_load_score=5.0)
    nodes = [IdleNode("peer:WAN", pending_tasks=0, is_lan=False)]
    assert coord.select_idle_node(nodes) is None


# -- dispatch ---------------------------------------------------------------


def test_dispatch_signs_and_records_pending():
    ident = _ident()
    sent: list[tuple[str, SignedManifest]] = []
    coord = SpeculativeMeshCoordinator(
        ident, dispatch_fn=lambda nid, s: sent.append((nid, s)), clock_ms=StepClock()
    )
    signed = coord.dispatch(_manifest(ident.node_id), [IdleNode("peer:B")])
    assert signed is not None
    assert signed.verify()
    assert coord.metrics.dispatched == 1
    assert coord.inflight == 1
    assert sent == [("peer:B", signed)]


def test_dispatch_no_idle_node_returns_none():
    ident = _ident()
    coord = SpeculativeMeshCoordinator(ident, max_load_score=0.0)
    signed = coord.dispatch(_manifest(ident.node_id), [IdleNode("peer:B", pending_tasks=10)])
    assert signed is None
    assert coord.metrics.no_idle_node == 1
    assert coord.metrics.dispatched == 0


def test_dispatch_wan_counted():
    ident = _ident()
    coord = SpeculativeMeshCoordinator(ident, require_lan=False, max_load_score=5.0)
    coord.dispatch(_manifest(ident.node_id), [IdleNode("peer:WAN", is_lan=False)])
    assert coord.metrics.wan_dispatches == 1


# -- collect: hit / miss / waste --------------------------------------------


def test_full_hit_lifecycle():
    ident = _ident()
    coord = SpeculativeMeshCoordinator(ident, clock_ms=StepClock())
    signed = coord.dispatch(_manifest(ident.node_id), [IdleNode("peer:B")])
    assert coord.submit_result(signed, {"answer": 42}, cost_ms=120.0) is True
    hit, value = coord.pull(signed.manifest_hash)
    assert hit is True
    assert value == {"answer": 42}
    assert coord.metrics.hits == 1
    assert coord.metrics.misses == 0
    assert coord.metrics.used_compute_ms == 120.0
    assert coord.metrics.hit_rate == 1.0


def test_miss_when_result_not_ready():
    ident = _ident()
    coord = SpeculativeMeshCoordinator(ident, clock_ms=StepClock())
    signed = coord.dispatch(_manifest(ident.node_id), [IdleNode("peer:B")])
    # pull before any result → miss, and the slow speculation is marked wasted.
    hit, value = coord.pull(signed.manifest_hash)
    assert hit is False
    assert value is None
    assert coord.metrics.misses == 1
    assert coord.metrics.wasted == 1


def test_miss_for_never_dispatched_branch():
    coord = SpeculativeMeshCoordinator(_ident())
    hit, value = coord.pull("deadbeef" * 8)
    assert hit is False
    assert coord.metrics.misses == 1
    assert coord.metrics.wasted == 0


def test_late_result_after_miss_is_wasted_compute():
    ident = _ident()
    coord = SpeculativeMeshCoordinator(ident, clock_ms=StepClock())
    signed = coord.dispatch(_manifest(ident.node_id), [IdleNode("peer:B")])
    coord.pull(signed.manifest_hash)  # miss → marked wasted
    accepted = coord.submit_result(signed, {"x": 1}, cost_ms=80.0)
    assert accepted is False
    assert coord.metrics.wasted_compute_ms == 80.0


def test_discard_unpulled_counts_ready_waste():
    ident = _ident()
    coord = SpeculativeMeshCoordinator(ident, clock_ms=StepClock())
    s1 = coord.dispatch(_manifest(ident.node_id, manifest_id="m1"), [IdleNode("peer:B")])
    s2 = coord.dispatch(_manifest(ident.node_id, manifest_id="m2"), [IdleNode("peer:B")])
    coord.submit_result(s1, {"r": 1}, cost_ms=50.0)  # ready but never pulled
    # s2 left dispatched
    discarded = coord.discard_unpulled()
    assert discarded == 2
    assert coord.metrics.wasted == 2
    assert coord.metrics.wasted_compute_ms == 50.0  # only the ready one had compute
    assert coord.inflight == 0
    assert s2 is not None


# -- fail-closed signature verification -------------------------------------


def test_submit_result_rejects_bad_signature():
    ident = _ident()
    other = _ident()
    coord = SpeculativeMeshCoordinator(ident, clock_ms=StepClock())
    signed = coord.dispatch(_manifest(ident.node_id), [IdleNode("peer:B")])
    forged = dataclasses.replace(signed, origin_pub_hex=other.public_key_hex)
    assert coord.submit_result(forged, {"evil": True}, cost_ms=10.0) is False
    assert coord.metrics.signature_rejections == 1
    # legitimate pull still misses (no valid result stored)
    hit, _ = coord.pull(signed.manifest_hash)
    assert hit is False


def test_submit_result_rejects_tampered_manifest():
    ident = _ident()
    coord = SpeculativeMeshCoordinator(ident, clock_ms=StepClock())
    signed = coord.dispatch(_manifest(ident.node_id), [IdleNode("peer:B")])
    tampered_m = dataclasses.replace(signed.manifest, branch={"op": "evil"})
    tampered = dataclasses.replace(signed, manifest=tampered_m)
    assert coord.submit_result(tampered, {"x": 1}) is False
    assert coord.metrics.signature_rejections == 1


# -- honest disclosure ------------------------------------------------------


def test_disclosure_reports_hit_rate_and_waste():
    ident = _ident()
    coord = SpeculativeMeshCoordinator(ident, clock_ms=StepClock())
    # one hit
    s1 = coord.dispatch(_manifest(ident.node_id, manifest_id="m1"), [IdleNode("peer:B")])
    coord.submit_result(s1, {"r": 1}, cost_ms=100.0)
    coord.pull(s1.manifest_hash)
    # one miss
    s2 = coord.dispatch(_manifest(ident.node_id, manifest_id="m2"), [IdleNode("peer:B")])
    coord.pull(s2.manifest_hash)

    d = coord.disclosure()
    assert d["dispatched"] == 2
    assert d["hits"] == 1
    assert d["misses"] == 1
    assert d["hit_rate"] == 0.5
    assert d["used_compute_ms"] == 100.0
    assert d["require_lan"] is True
    assert "honest_note" in d


def test_disclosure_hit_rate_none_when_no_pulls():
    coord = SpeculativeMeshCoordinator(_ident())
    d = coord.disclosure()
    assert d["hit_rate"] is None
    assert d["pulls"] == 0
