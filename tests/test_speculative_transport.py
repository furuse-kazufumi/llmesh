"""Tests for the speculative-mesh real transport + executor (SPEC-MESH-02/03/04).

Covers: signed-result provenance (create/verify/tamper/binding/wire round-trip),
peer executor (verify-then-run, fail-closed rejection, allow-list, error guard),
origin dispatch wiring (registry resolution, best-effort no-raise), non-blocking
HTTP transport (background pool, error counting), origin result intake, in-process
loopback round-trip (hit / miss+fast-fallback), and coordinator.pull_or_compute.
"""
from __future__ import annotations

import dataclasses
import threading
from concurrent.futures import Executor, Future

import pytest

from llmesh.identity.node_id import NodeIdentity
from llmesh.speculative import (
    HttpMeshTransport,
    IdleNode,
    LoopbackMesh,
    SignedManifest,
    SignedResult,
    SpeculativeExecutor,
    SpeculativeManifest,
    SpeculativeMeshCoordinator,
    ingest_result,
    make_loopback_dispatch_fn,
    make_mesh_dispatch_fn,
    sign_manifest,
)


def _ident() -> NodeIdentity:
    return NodeIdentity.generate()


def _manifest(origin: str, **kw) -> SpeculativeManifest:
    kw.setdefault("branch", {"op": "insert", "value": 1})
    kw.setdefault("created_at_ms", 1000)
    return SpeculativeManifest.new(origin_node_id=origin, **kw)


def _signed(origin_ident: NodeIdentity, **kw) -> SignedManifest:
    return sign_manifest(_manifest(origin_ident.node_id, **kw), origin_ident)


class _InlineExecutor(Executor):
    """Runs submitted work synchronously — deterministic transport tests."""

    def submit(self, fn, *args, **kwargs):  # type: ignore[override]
        f: Future = Future()
        try:
            f.set_result(fn(*args, **kwargs))
        except Exception as exc:  # pragma: no cover - mirrors real pool semantics
            f.set_exception(exc)
        return f


class _StubRegistry:
    """Minimal NodeRegistry stand-in exposing only ``get(node_id) -> entry|None``."""

    def __init__(self, entries: dict[str, object], raises: bool = False) -> None:
        self._entries = entries
        self._raises = raises

    def get(self, node_id: str):
        if self._raises:
            raise RuntimeError("registry boom")
        return self._entries.get(node_id)


@dataclasses.dataclass
class _Entry:
    endpoint: str


# -- SignedResult: provenance ----------------------------------------------


def test_signed_result_create_and_verify_roundtrip():
    ex = _ident()
    sr = SignedResult.create(
        manifest_hash="ab" * 32, result={"answer": 42}, cost_ms=12.5, identity=ex
    )
    assert sr.verify() is True
    assert sr.executor_pub_hex == ex.public_key_hex
    assert sr.cost_ms == 12.5


def test_signed_result_verify_fails_on_tampered_result():
    ex = _ident()
    sr = SignedResult.create(manifest_hash="cd" * 32, result={"x": 1}, cost_ms=1.0, identity=ex)
    tampered = dataclasses.replace(sr, result={"x": 999})
    assert tampered.verify() is False


def test_signed_result_verify_fails_on_swapped_manifest_hash():
    ex = _ident()
    sr = SignedResult.create(manifest_hash="11" * 32, result={"x": 1}, cost_ms=1.0, identity=ex)
    moved = dataclasses.replace(sr, manifest_hash="22" * 32)
    assert moved.verify() is False  # signature binds to manifest_hash


def test_signed_result_verify_fails_on_malformed_signature():
    ex = _ident()
    sr = SignedResult.create(manifest_hash="ef" * 32, result=1, cost_ms=0.0, identity=ex)
    bad = dataclasses.replace(sr, signature_hex="not-hex!!")
    assert bad.verify() is False


def test_signed_result_to_dict_from_dict_roundtrip():
    ex = _ident()
    sr = SignedResult.create(
        manifest_hash="ab" * 32, result={"nested": [1, 2, 3]}, cost_ms=9.0, identity=ex
    )
    restored = SignedResult.from_dict(sr.to_dict())
    assert restored == sr
    assert restored.verify() is True


# -- SpeculativeExecutor (SPEC-MESH-03) ------------------------------------


def test_executor_runs_and_signs_result():
    origin, peer = _ident(), _ident()
    ex = SpeculativeExecutor(peer, run_fn=lambda b: {"doubled": b["value"] * 2})
    signed = _signed(origin, branch={"value": 21})
    sr = ex.handle_signed(signed)
    assert sr is not None
    assert sr.verify() is True
    assert sr.result == {"doubled": 42}
    assert sr.manifest_hash == signed.manifest_hash
    assert sr.executor_pub_hex == peer.public_key_hex
    assert sr.cost_ms >= 0.0
    assert ex.metrics.executed == 1
    assert ex.metrics.received == 1


def test_executor_rejects_bad_manifest_signature_without_running():
    origin, other, peer = _ident(), _ident(), _ident()
    ran: list[int] = []
    ex = SpeculativeExecutor(peer, run_fn=lambda b: ran.append(1))
    signed = _signed(origin)
    forged = dataclasses.replace(signed, origin_pub_hex=other.public_key_hex)
    assert ex.handle_signed(forged) is None
    assert ex.metrics.rejected_signature == 1
    assert ran == []  # never executed unverified work


def test_executor_allowlist_rejects_unknown_origin():
    origin, allowed, peer = _ident(), _ident(), _ident()
    ex = SpeculativeExecutor(
        peer, run_fn=lambda b: {"ok": True}, allowed_origins=[allowed.public_key_hex]
    )
    assert ex.handle_signed(_signed(origin)) is None
    assert ex.metrics.rejected_origin == 1
    # an allowed origin is executed
    ex2 = SpeculativeExecutor(
        peer, run_fn=lambda b: {"ok": True}, allowed_origins=[origin.public_key_hex]
    )
    assert ex2.handle_signed(_signed(origin)) is not None


def test_executor_swallows_run_errors():
    origin, peer = _ident(), _ident()

    def boom(_branch):
        raise ValueError("poisoned branch")

    ex = SpeculativeExecutor(peer, run_fn=boom)
    assert ex.handle_signed(_signed(origin)) is None
    assert ex.metrics.exec_errors == 1
    assert ex.metrics.executed == 0


def test_executor_handle_payload_roundtrip_and_malformed():
    origin, peer = _ident(), _ident()
    ex = SpeculativeExecutor(peer, run_fn=lambda b: {"echo": b})
    signed = _signed(origin, branch={"k": "v"})
    out = ex.handle_payload(signed.to_dict())
    assert out is not None
    assert SignedResult.from_dict(out).verify() is True
    # malformed payload is rejected fail-closed (no crash)
    assert ex.handle_payload({"garbage": True}) is None
    assert ex.metrics.rejected_signature == 1


# -- make_mesh_dispatch_fn (SPEC-MESH-02) ----------------------------------


def test_dispatch_fn_resolves_endpoint_and_sends():
    origin = _ident()
    sent: list[tuple[str, SignedManifest]] = []

    class _RecTransport:
        def send(self, endpoint, signed):
            sent.append((endpoint, signed))

    registry = _StubRegistry({"peer:B": _Entry("http://10.0.0.5:8080/")})
    dispatch_fn = make_mesh_dispatch_fn(registry, _RecTransport())
    signed = _signed(origin)
    dispatch_fn("peer:B", signed)
    assert sent == [("http://10.0.0.5:8080/", signed)]


def test_dispatch_fn_unknown_peer_is_noop():
    origin = _ident()
    sent: list = []

    class _RecTransport:
        def send(self, endpoint, signed):
            sent.append(endpoint)

    dispatch_fn = make_mesh_dispatch_fn(_StubRegistry({}), _RecTransport())
    dispatch_fn("peer:gone", _signed(origin))  # no raise
    assert sent == []


def test_dispatch_fn_never_raises_on_registry_error():
    origin = _ident()

    class _RecTransport:
        def send(self, endpoint, signed):
            raise AssertionError("should not be reached")

    dispatch_fn = make_mesh_dispatch_fn(_StubRegistry({}, raises=True), _RecTransport())
    # registry.get raising must not propagate into the origin dispatch path.
    dispatch_fn("peer:B", _signed(origin))


def test_dispatch_fn_wires_into_coordinator():
    origin = _ident()
    sent: list[tuple[str, SignedManifest]] = []

    class _RecTransport:
        def send(self, endpoint, signed):
            sent.append((endpoint, signed))

    registry = _StubRegistry({"peer:B": _Entry("http://10.0.0.9:8080")})
    coord = SpeculativeMeshCoordinator(
        origin, dispatch_fn=make_mesh_dispatch_fn(registry, _RecTransport())
    )
    signed = coord.dispatch(_manifest(origin.node_id), [IdleNode("peer:B")])
    assert signed is not None
    assert sent == [("http://10.0.0.9:8080", signed)]


# -- HttpMeshTransport (non-blocking, fail-soft) ---------------------------


def test_http_transport_builds_url_and_payload():
    origin = _ident()
    calls: list[tuple[str, dict]] = []
    t = HttpMeshTransport(executor=_InlineExecutor(), post_fn=lambda url, p: calls.append((url, p)))
    signed = _signed(origin)
    t.send("http://192.168.1.9:8080/", signed)
    assert calls[0][0] == "http://192.168.1.9:8080/speculative/dispatch"
    assert calls[0][1] == signed.to_dict()
    assert t.metrics.sent == 1


def test_http_transport_counts_errors_without_raising():
    origin = _ident()

    def boom(url, payload):
        raise OSError("connection refused")

    t = HttpMeshTransport(executor=_InlineExecutor(), post_fn=boom)
    t.send("http://192.168.1.9:8080", _signed(origin))  # must not raise
    assert t.metrics.send_errors == 1
    assert t.metrics.sent == 0


def test_http_transport_send_is_non_blocking_on_background_thread():
    origin = _ident()
    main_ident = threading.get_ident()
    seen: list[int] = []
    # default thread pool — POST runs off the calling thread.
    t = HttpMeshTransport(post_fn=lambda url, p: seen.append(threading.get_ident()))
    t.send("http://192.168.1.9:8080", _signed(origin))
    t.close()  # drain the pool
    assert len(seen) == 1
    assert seen[0] != main_ident  # proves the POST did not run inline (fast-fallback)
    assert t.metrics.sent == 1


# -- ingest_result (origin intake) -----------------------------------------


def _origin_with_pending(origin: NodeIdentity, peer: NodeIdentity):
    """Coordinator with one manifest dispatched to ``peer`` (real node id)."""
    coord = SpeculativeMeshCoordinator(origin)
    signed = coord.dispatch(_manifest(origin.node_id), [IdleNode(peer.node_id)])
    assert signed is not None
    return coord, signed


def test_ingest_result_accepts_and_enables_hit():
    origin, peer = _ident(), _ident()
    coord, signed = _origin_with_pending(origin, peer)
    sr = SignedResult.create(
        manifest_hash=signed.manifest_hash, result={"v": 7}, cost_ms=33.0, identity=peer
    )
    assert ingest_result(coord, signed, sr) is True
    hit, value = coord.pull(signed.manifest_hash)
    assert hit is True
    assert value == {"v": 7}
    assert coord.metrics.used_compute_ms == 33.0


def test_ingest_result_rejects_bad_result_signature():
    origin, peer = _ident(), _ident()
    coord, signed = _origin_with_pending(origin, peer)
    sr = SignedResult.create(
        manifest_hash=signed.manifest_hash, result={"v": 1}, cost_ms=1.0, identity=peer
    )
    forged = dataclasses.replace(sr, result={"v": 666})  # invalidates signature
    assert ingest_result(coord, signed, forged) is False
    assert coord.metrics.signature_rejections == 1
    assert coord.pull(signed.manifest_hash)[0] is False


def test_ingest_result_rejects_manifest_hash_mismatch():
    origin, peer = _ident(), _ident()
    coord, signed = _origin_with_pending(origin, peer)
    sr = SignedResult.create(
        manifest_hash="00" * 32, result={"v": 1}, cost_ms=1.0, identity=peer
    )
    assert ingest_result(coord, signed, sr) is False
    assert coord.metrics.signature_rejections == 1


def test_ingest_result_rejects_undispatched_peer_by_default():
    # A peer that was never dispatched to (but signs a valid result over the public
    # manifest_hash) must be rejected by the default dispatched-peer binding.
    origin, peer, impostor = _ident(), _ident(), _ident()
    coord, signed = _origin_with_pending(origin, peer)  # dispatched to `peer`
    sr = SignedResult.create(
        manifest_hash=signed.manifest_hash, result={"v": 1}, cost_ms=1.0, identity=impostor
    )
    assert ingest_result(coord, signed, sr) is False
    assert coord.metrics.signature_rejections == 1
    assert coord.pull(signed.manifest_hash)[0] is False  # cache not poisoned


def test_ingest_result_open_relay_accepts_any_signer():
    # enforce_dispatched_peer=False is the explicit opt-out for open-relay topologies.
    origin, peer, anyone = _ident(), _ident(), _ident()
    coord, signed = _origin_with_pending(origin, peer)
    sr = SignedResult.create(
        manifest_hash=signed.manifest_hash, result={"v": 2}, cost_ms=1.0, identity=anyone
    )
    assert ingest_result(coord, signed, sr, enforce_dispatched_peer=False) is True
    assert coord.pull(signed.manifest_hash) == (True, {"v": 2})


# -- LoopbackMesh: in-process end-to-end -----------------------------------


def _loopback_pair(*, immediate: bool):
    origin, peer = _ident(), _ident()
    mesh = LoopbackMesh(immediate=immediate)
    executor = SpeculativeExecutor(peer, run_fn=lambda b: {"sq": b["n"] ** 2})
    # register/dispatch by the executor's REAL node id so the default
    # dispatched-peer binding in ingest_result holds.
    mesh.register_executor(peer.node_id, executor)
    coord = SpeculativeMeshCoordinator(origin, dispatch_fn=make_loopback_dispatch_fn(mesh))
    mesh.register_origin(origin.node_id, coord)
    return origin, peer, coord, mesh


def test_loopback_immediate_roundtrip_is_a_hit():
    origin, peer, coord, mesh = _loopback_pair(immediate=True)
    signed = coord.dispatch(_manifest(origin.node_id, branch={"n": 9}), [IdleNode(peer.node_id)])
    assert signed is not None
    hit, value = coord.pull(signed.manifest_hash)
    assert hit is True
    assert value == {"sq": 81}
    assert coord.metrics.hits == 1


def test_loopback_deferred_delivery_then_hit():
    origin, peer, coord, mesh = _loopback_pair(immediate=False)
    signed = coord.dispatch(_manifest(origin.node_id, branch={"n": 4}), [IdleNode(peer.node_id)])
    assert signed is not None
    # not delivered yet → result not present
    assert coord.inflight == 1
    assert mesh.deliver_pending() == 1
    hit, value = coord.pull(signed.manifest_hash)
    assert hit is True
    assert value == {"sq": 16}


def test_loopback_pull_before_delivery_falls_back_locally():
    origin, peer, coord, mesh = _loopback_pair(immediate=False)
    signed = coord.dispatch(_manifest(origin.node_id, branch={"n": 5}), [IdleNode(peer.node_id)])
    assert signed is not None
    # fast-fallback: origin reaches the branch before the speculation lands.
    value = coord.pull_or_compute(signed.manifest_hash, local_fn=lambda: {"local": 25})
    assert value == {"local": 25}
    assert coord.metrics.misses == 1
    # the late speculation is now useless and rejected as wasted on delivery.
    assert mesh.deliver_pending() == 0


def test_loopback_no_executor_is_undeliverable():
    origin = _ident()
    mesh = LoopbackMesh(immediate=True)
    coord = SpeculativeMeshCoordinator(origin, dispatch_fn=make_loopback_dispatch_fn(mesh))
    mesh.register_origin(origin.node_id, coord)
    signed = coord.dispatch(_manifest(origin.node_id), [IdleNode("peer:missing")])
    assert signed is not None
    assert mesh.undeliverable == 1
    assert coord.pull(signed.manifest_hash)[0] is False


# -- coordinator.pull_or_compute (SPEC-MESH-04) ----------------------------


def test_pull_or_compute_returns_hit_without_calling_local():
    origin, peer = _ident(), _ident()
    coord, signed = _origin_with_pending(origin)
    sr = SignedResult.create(
        manifest_hash=signed.manifest_hash, result={"v": 1}, cost_ms=5.0, identity=peer
    )
    ingest_result(coord, signed, sr)
    called: list[int] = []
    value = coord.pull_or_compute(signed.manifest_hash, local_fn=lambda: called.append(1))
    assert value == {"v": 1}
    assert called == []  # local compute skipped on a hit


def test_pull_or_compute_computes_locally_on_miss():
    origin = _ident()
    coord, signed = _origin_with_pending(origin)
    value = coord.pull_or_compute(signed.manifest_hash, local_fn=lambda: "local-answer")
    assert value == "local-answer"
    assert coord.metrics.misses == 1
