"""Real mesh transport for speculative execution (SPEC-MESH-02 / -03 return / -04).

The PoC :class:`~llmesh.speculative.coordinator.SpeculativeMeshCoordinator` left two
seams unwired: ``dispatch_fn`` recorded only, and ``submit_result`` was driven by hand.
This module fills them with a real, fail-closed, **non-blocking** transport:

* **SPEC-MESH-02 — origin dispatch.** :func:`make_mesh_dispatch_fn` resolves a target
  peer's endpoint via the llmesh :class:`~llmesh.discovery.registry.NodeRegistry` and
  ships the signed manifest over a :class:`MeshTransport`. Dispatch is **best-effort**:
  a vanished peer or a network error is counted, never raised — a failed dispatch just
  becomes a cache miss the origin computes locally (fast-fallback).
* **SPEC-MESH-03 — result return.** :class:`SignedResult` lets the executing peer sign
  its result so the origin can verify provenance + tamper-evidence; :func:`ingest_result`
  is the origin-side intake that verifies the result and feeds the coordinator.
* **SPEC-MESH-04 — fast-fallback.** :class:`HttpMeshTransport` sends on a background
  thread pool so ``coord.dispatch`` returns immediately; the origin never blocks on a
  speculation. Pair with ``coordinator.pull_or_compute`` for the miss path.

stdlib only on the wire (``urllib``, mirroring :class:`~llmesh.discovery.client.DiscoveryClient`).
Ed25519 signing reuses :class:`~llmesh.identity.node_id.NodeIdentity`.

Honest disclosure: a valid signature proves *authenticity*, never *correctness*.
Adopting a speculative result still needs the SPEC-MESH-11 cross-check / predictive
verification gate — do not treat ``verify() == True`` as "the answer is right".
"""
from __future__ import annotations

import hashlib
import json
import threading
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from concurrent.futures import Executor, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..identity.node_id import NodeIdentity
from .manifest import SignedManifest

if TYPE_CHECKING:  # avoid import cycles at runtime
    from ..discovery.registry import NodeRegistry
    from .coordinator import SpeculativeMeshCoordinator

_DISPATCH_PATH = "/speculative/dispatch"
_RESULT_PATH = "/speculative/result"
_DEFAULT_TIMEOUT = 10  # seconds (mirrors DiscoveryClient)


def _canonical_json(payload: dict[str, Any]) -> bytes:
    """Deterministic JSON encoding used as the signed / hashed representation.

    Identical rules to :func:`llmesh.speculative.manifest._canonical_json`
    (sorted keys, no ASCII escaping, compact separators) so signatures are stable
    across nodes and Python versions.
    """
    return json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")


# ----------------------------------------------------------------------
# SPEC-MESH-03: signed result (provenance on the return path)
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class SignedResult:
    """An executing peer's Ed25519-signed result for a speculative manifest.

    The signature binds ``(manifest_hash, result, cost_ms)``: the origin can prove
    the result came from the holder of ``executor_pub_hex`` and was not altered in
    transit, and that it is the result *for this manifest* (replaying it under a
    different manifest_hash invalidates the signature).
    """

    manifest_hash: str
    result: Any
    cost_ms: float
    executor_pub_hex: str
    signature_hex: str

    def _canonical_bytes(self) -> bytes:
        return _canonical_json(
            {
                "manifest_hash": self.manifest_hash,
                "result": self.result,
                "cost_ms": self.cost_ms,
            }
        )

    @property
    def result_hash(self) -> str:
        return hashlib.sha256(self._canonical_bytes()).hexdigest()

    @classmethod
    def create(
        cls,
        *,
        manifest_hash: str,
        result: Any,
        cost_ms: float,
        identity: NodeIdentity,
    ) -> SignedResult:
        unsigned = cls(
            manifest_hash=manifest_hash,
            result=result,
            cost_ms=float(cost_ms),
            executor_pub_hex=identity.public_key_hex,
            signature_hex="",
        )
        sig = identity.sign(unsigned._canonical_bytes())
        return cls(
            manifest_hash=manifest_hash,
            result=result,
            cost_ms=float(cost_ms),
            executor_pub_hex=identity.public_key_hex,
            signature_hex=sig.hex(),
        )

    def verify(self) -> bool:
        """True iff the Ed25519 signature is valid. fail-closed on any error."""
        try:
            sig = bytes.fromhex(self.signature_hex)
        except ValueError:
            return False
        return NodeIdentity.verify_with_public_hex(
            self._canonical_bytes(), sig, self.executor_pub_hex
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest_hash": self.manifest_hash,
            "result": self.result,
            "cost_ms": self.cost_ms,
            "executor_pub_hex": self.executor_pub_hex,
            "signature_hex": self.signature_hex,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SignedResult:
        """Reconstruct from :meth:`to_dict`. fail-closed: malformed input raises."""
        return cls(
            manifest_hash=str(d["manifest_hash"]),
            result=d["result"],
            cost_ms=float(d["cost_ms"]),
            executor_pub_hex=str(d["executor_pub_hex"]),
            signature_hex=str(d["signature_hex"]),
        )


# ----------------------------------------------------------------------
# SPEC-MESH-03 origin intake: verify a returned result, feed the coordinator
# ----------------------------------------------------------------------


def ingest_result(
    coord: "SpeculativeMeshCoordinator",
    signed_manifest: SignedManifest,
    signed_result: SignedResult,
    *,
    enforce_dispatched_peer: bool = True,
) -> bool:
    """Origin-side intake of a peer's :class:`SignedResult` (fail-closed).

    Verifies, in order, then caches via ``coord.submit_result``:

    1. the result's own Ed25519 signature (provenance / not tampered in transit);
    2. that it binds to ``signed_manifest`` (no cross-manifest replay);
    3. **(default) that it came from the peer the origin actually dispatched to** —
       ``signed_result.executor_pub_hex`` is mapped to a ``peer:`` node id and required
       to equal ``coord.expected_executor(manifest_hash)``. This closes the
       cache-poisoning gap where a peer that was *never* dispatched to could observe
       the public ``manifest_hash``, sign its own result, and have it accepted.
       A missing pending entry (already pulled/unknown) or a mismatch is rejected.

    Any rejection increments ``coord.metrics.signature_rejections`` and returns False.
    Set ``enforce_dispatched_peer=False`` **only** for an open-relay topology that
    intentionally accepts a validly-signed result from any peer.

    NOTE (SPEC-MESH-11): this proves authenticity + dispatched-peer provenance, not
    *correctness*. A Byzantine but legitimately-dispatched peer can still return a
    wrong answer; gate adoption behind a cross-check before promoting a speculative
    result to a confirmed task.
    """
    if not signed_result.verify():
        coord.metrics.signature_rejections += 1
        return False
    if signed_result.manifest_hash != signed_manifest.manifest_hash:
        coord.metrics.signature_rejections += 1
        return False
    if enforce_dispatched_peer:
        target = coord.expected_executor(signed_manifest.manifest_hash)
        try:
            executor_node_id: str | None = NodeIdentity.node_id_from_public_hex(
                signed_result.executor_pub_hex
            )
        except ValueError:
            executor_node_id = None
        if target is None or executor_node_id is None or executor_node_id != target:
            coord.metrics.signature_rejections += 1
            return False
    return coord.submit_result(
        signed_manifest, signed_result.result, cost_ms=signed_result.cost_ms
    )


# ----------------------------------------------------------------------
# SPEC-MESH-02: transports
# ----------------------------------------------------------------------


class MeshTransport(ABC):
    """Origin-side transport that ships a signed manifest to a peer endpoint."""

    @abstractmethod
    def send(self, endpoint: str, signed: SignedManifest) -> None:
        """Deliver ``signed`` to ``endpoint``. MUST NOT block the caller and MUST
        NOT raise (best-effort; failures are the transport's own concern)."""


@dataclass
class _HttpMetrics:
    sent: int = 0
    send_errors: int = 0


class HttpMeshTransport(MeshTransport):
    """Non-blocking HTTP transport (stdlib ``urllib``) over a background pool.

    ``send`` submits the POST to a thread pool and returns immediately so the
    origin's main inference is never stalled by mesh latency (SPEC-MESH-04). A
    failed POST is counted (``metrics.send_errors``) and swallowed — the origin
    will simply miss and compute locally.

    Parameters
    ----------
    timeout:
        Per-request HTTP timeout in seconds.
    executor:
        Optional :class:`concurrent.futures.Executor`. Defaults to a small
        :class:`ThreadPoolExecutor`. Pass a synchronous executor in tests.
    post_fn:
        Optional ``(url, payload_dict) -> None`` override for the actual POST
        (tests inject a fake; default uses ``urllib``).
    max_workers:
        Worker count for the default thread pool.
    """

    def __init__(
        self,
        *,
        timeout: int = _DEFAULT_TIMEOUT,
        executor: Executor | None = None,
        post_fn: Any = None,
        max_workers: int = 4,
    ) -> None:
        self._timeout = timeout
        self._owns_pool = executor is None
        self._pool: Executor = executor or ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="spec-dispatch"
        )
        self._post_fn = post_fn or self._urllib_post
        self._lock = threading.Lock()
        self.metrics = _HttpMetrics()

    def send(self, endpoint: str, signed: SignedManifest) -> None:
        url = endpoint.rstrip("/") + _DISPATCH_PATH
        payload = signed.to_dict()
        self._pool.submit(self._send_blocking, url, payload)

    def _send_blocking(self, url: str, payload: dict[str, Any]) -> None:
        try:
            self._post_fn(url, payload)
        except Exception:
            with self._lock:
                self.metrics.send_errors += 1
            return
        with self._lock:
            self.metrics.sent += 1

    def _urllib_post(self, url: str, payload: dict[str, Any]) -> None:
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        # nosec B310 — url is built from a registry endpoint (validated on register).
        with urllib.request.urlopen(req, timeout=self._timeout):  # noqa: S310
            pass

    def close(self) -> None:
        if self._owns_pool:
            self._pool.shutdown(wait=True)

    def __enter__(self) -> "HttpMeshTransport":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def make_mesh_dispatch_fn(
    registry: "NodeRegistry",
    transport: MeshTransport,
) -> Any:
    """Build a ``dispatch_fn`` for :class:`SpeculativeMeshCoordinator` (SPEC-MESH-02).

    The returned ``(target_node_id, signed) -> None`` resolves the peer's endpoint
    from ``registry`` and hands the manifest to ``transport``. A peer that is no
    longer registered (TTL-evicted / deregistered) is a no-op: the speculation is
    simply never sent and the origin will compute locally on miss. The callable
    never raises — the coordinator calls it inside ``dispatch`` and a raise there
    would stall the origin's main loop.
    """

    def dispatch_fn(target_node_id: str, signed: SignedManifest) -> None:
        try:
            entry = registry.get(target_node_id)
            if entry is None:
                return
            transport.send(entry.endpoint, signed)
        except Exception:
            # best-effort: never propagate into the origin's dispatch path.
            return

    return dispatch_fn


# ----------------------------------------------------------------------
# In-process loopback mesh (tests + single-host multi-peer, no sockets)
# ----------------------------------------------------------------------


@dataclass
class LoopbackMesh(MeshTransport):
    """In-process mesh: routes dispatched manifests to registered executors and
    delivers signed results back to origin coordinators — with real signing and
    verification but no sockets.

    Useful for deterministic tests and single-host multi-peer simulation (closer
    to reality than the pure latency model in ``bench.py``).

    Two timing modes:

    * ``immediate=True`` (default): ``send`` executes the peer and delivers the
      result synchronously → a subsequent ``pull`` is a hit (models a peer fast
      enough to finish before the origin reaches the branch).
    * ``immediate=False``: ``send`` enqueues the work; call :meth:`deliver_pending`
      to run executors and deliver results. Lets a test ``pull`` *before* delivery
      to exercise the miss / fast-fallback path.
    """

    immediate: bool = True
    _executors: dict[str, Any] = field(default_factory=dict)   # node_id -> SpeculativeExecutor
    _origins: dict[str, Any] = field(default_factory=dict)     # origin_node_id -> coordinator
    _queue: list[tuple[str, SignedManifest]] = field(default_factory=list)
    undeliverable: int = 0

    def register_executor(self, node_id: str, executor: Any) -> None:
        self._executors[node_id] = executor

    def register_origin(self, node_id: str, coord: Any) -> None:
        self._origins[node_id] = coord

    def send(self, endpoint: str, signed: SignedManifest) -> None:
        # In loopback, the coordinator dispatch_fn passes node_id as "endpoint"
        # (see make_loopback_dispatch_fn); no URL is built.
        if self.immediate:
            self._deliver_one(endpoint, signed)
        else:
            self._queue.append((endpoint, signed))

    def deliver_pending(self) -> int:
        """Run all queued speculations and deliver results. Returns count delivered."""
        pending = self._queue
        self._queue = []
        delivered = 0
        for node_id, signed in pending:
            if self._deliver_one(node_id, signed):
                delivered += 1
        return delivered

    def _deliver_one(self, node_id: str, signed: SignedManifest) -> bool:
        executor = self._executors.get(node_id)
        if executor is None:
            self.undeliverable += 1
            return False
        signed_result = executor.handle_signed(signed)
        if signed_result is None:
            return False
        origin = self._origins.get(signed.manifest.origin_node_id)
        if origin is None:
            self.undeliverable += 1
            return False
        return ingest_result(
            origin, signed, signed_result, expected_pub_hex=executor.public_key_hex
        )


def make_loopback_dispatch_fn(mesh: LoopbackMesh) -> Any:
    """``dispatch_fn`` that routes to a :class:`LoopbackMesh` by node_id (tests)."""

    def dispatch_fn(target_node_id: str, signed: SignedManifest) -> None:
        mesh.send(target_node_id, signed)

    return dispatch_fn


__all__ = [
    "HttpMeshTransport",
    "LoopbackMesh",
    "MeshTransport",
    "SignedResult",
    "ingest_result",
    "make_loopback_dispatch_fn",
    "make_mesh_dispatch_fn",
]
