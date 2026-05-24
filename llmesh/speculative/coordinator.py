"""SpeculativeMeshCoordinator — speculative execution across the P2P mesh.

CPU branch prediction, lifted to the **agent level**. While a node runs its main
inference, predicted future branches are signed and dispatched to *idle* mesh
peers for speculative execution. When the node reaches that branch it pulls the
result from the mesh (cache hit) instead of recomputing it (cache miss). Misses
and never-pulled speculations are pure waste — so the value of the whole scheme
hinges on **honest disclosure of the hit rate and wasted compute**.

This module owns the llmesh-side mechanism (the idea's Phase 2 + Phase 3):

* **Phase 2 — dispatch**: pick the least-loaded idle peer (LAN-first), sign the
  manifest (Ed25519, see :mod:`llmesh.speculative.manifest`), send it as a
  low-priority ``speculative=true`` task.
* **Phase 3 — collect**: peers ``submit_result`` (signature verified, fail-closed);
  the origin ``pull(manifest_hash)`` gets a hit or a miss; unpulled work is
  discarded and counted as waste.

Phase 1 (predicting *which* branches) belongs to the inference engine (llive's
MetaMutation) and is out of scope here — the coordinator consumes ready-made
:class:`SpeculativeManifest` objects.

Honest-disclosure caveats (see :class:`SpeculativeMetrics`):

* The scheme only wins when a mesh round-trip beats local VRAM swap latency —
  true on a LAN (μs–ms), usually false over a WAN (``wan_dispatches`` is tracked
  precisely because WAN dispatch is expected to *lose*).
* A low hit rate or high ``wasted_compute_ms`` means the mesh burned energy for
  nothing. Do not claim a speedup without the per-run numbers.

    coord = SpeculativeMeshCoordinator(origin_identity)
    signed = coord.dispatch(manifest, idle_nodes=[IdleNode("peer:B", cpu_load=0.1)])
    # ... peer B executes, returns a result ...
    coord.submit_result(signed, result={"answer": 42}, cost_ms=120.0)
    hit, value = coord.pull(signed.manifest_hash)   # (True, {"answer": 42})
    print(coord.disclosure())
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .manifest import SignedManifest, SpeculativeManifest, sign_manifest

# 注入される時計 (ms)。テストの決定性確保のため差し替え可能。
ClockMsFn = Callable[[], int]
# manifest を実際に送る transport。None なら record only (PoC / 単体テスト)。
DispatchFn = Callable[[str, SignedManifest], None]


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass(frozen=True)
class IdleNode:
    """A candidate peer for speculative work, with a coarse load snapshot.

    ``load_score`` (lower = more idle) ranks candidates. VRAM is a hard filter
    (a peer without enough free VRAM cannot run the branch at all).
    """

    node_id: str
    pending_tasks: int = 0
    cpu_load: float = 0.0          # 0..1
    vram_free_mb: float = 0.0
    is_lan: bool = True

    def load_score(self) -> float:
        # pending work dominates; cpu_load breaks ties between similarly queued peers.
        return float(self.pending_tasks) + float(self.cpu_load)


@dataclass
class SpeculativeMetrics:
    """Honest-disclosure counters for the speculative-mesh loop.

    A speculation is only a *win* when it is dispatched, executed in time, and
    pulled (``hits``). Everything else is overhead the mesh paid:

    * ``misses`` — origin reached the branch but no ready result existed
      (speculation too slow / not dispatched) → had to compute locally.
    * ``wasted`` — a dispatched/ready speculation was never used (superseded or
      discarded). ``wasted_compute_ms`` is the executor time thrown away.
    * ``wan_dispatches`` — dispatched to a non-LAN peer, where the round-trip is
      expected to be *slower* than local swap (likely net-negative).
    * ``signature_rejections`` — results that failed Ed25519 verification
      (fail-closed; never trusted).
    """

    dispatched: int = 0
    hits: int = 0
    misses: int = 0
    wasted: int = 0
    wan_dispatches: int = 0
    signature_rejections: int = 0
    no_idle_node: int = 0          # dispatch attempts with no eligible peer
    wasted_compute_ms: float = 0.0
    used_compute_ms: float = 0.0   # executor time that produced a pulled hit

    @property
    def pulls(self) -> int:
        return self.hits + self.misses

    @property
    def hit_rate(self) -> float | None:
        return (self.hits / self.pulls) if self.pulls else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "dispatched": self.dispatched,
            "hits": self.hits,
            "misses": self.misses,
            "wasted": self.wasted,
            "wan_dispatches": self.wan_dispatches,
            "signature_rejections": self.signature_rejections,
            "no_idle_node": self.no_idle_node,
            "wasted_compute_ms": self.wasted_compute_ms,
            "used_compute_ms": self.used_compute_ms,
            "pulls": self.pulls,
            "hit_rate": self.hit_rate,
        }


# pending speculation の状態遷移。
_DISPATCHED = "dispatched"   # 投入済、結果待ち
_READY = "ready"             # 実行ノードから署名検証済の結果が届いた
_PULLED = "pulled"           # origin が回収済 (hit)
_WASTED = "wasted"           # 未使用のまま破棄 / 期限切れ


@dataclass
class _Pending:
    signed: SignedManifest
    target_node_id: str
    dispatched_at_ms: int
    state: str = _DISPATCHED
    result: Any = None
    cost_ms: float = 0.0


class SpeculativeMeshCoordinator:
    """Origin-side coordinator for signed speculative execution over the mesh.

    Parameters
    ----------
    identity:
        Origin node's Ed25519 identity. Used to sign every dispatched manifest.
    dispatch_fn:
        Optional transport callback ``(target_node_id, signed) -> None`` that
        actually ships the speculative task. ``None`` records only (PoC).
    require_lan:
        When True (default), only LAN peers are eligible — WAN round-trips
        usually lose to local swap. Set False to allow WAN (counted separately).
    max_load_score:
        Peers above this load score are not considered idle.
    clock_ms:
        Injectable millisecond clock for deterministic tests.
    """

    def __init__(
        self,
        identity: Any,
        *,
        dispatch_fn: DispatchFn | None = None,
        require_lan: bool = True,
        max_load_score: float = 1.0,
        clock_ms: ClockMsFn | None = None,
    ) -> None:
        self._identity = identity
        self._dispatch_fn = dispatch_fn
        self._require_lan = bool(require_lan)
        self._max_load_score = float(max_load_score)
        self._clock = clock_ms or _now_ms
        self._pending: dict[str, _Pending] = {}
        self.metrics = SpeculativeMetrics()

    # ------------------------------------------------------------------
    # Phase 2 — dispatch
    # ------------------------------------------------------------------

    def select_idle_node(
        self,
        idle_nodes: list[IdleNode],
        *,
        min_vram_mb: float = 0.0,
    ) -> IdleNode | None:
        """Pick the least-loaded eligible peer (LAN-first), or None."""
        eligible = [
            n
            for n in idle_nodes
            if (n.is_lan or not self._require_lan)
            and n.load_score() <= self._max_load_score
            and n.vram_free_mb >= min_vram_mb
        ]
        if not eligible:
            return None
        # LAN peers preferred over WAN even if a WAN peer is marginally idler.
        return min(eligible, key=lambda n: (not n.is_lan, n.load_score()))

    def dispatch(
        self,
        manifest: SpeculativeManifest,
        idle_nodes: list[IdleNode],
        *,
        min_vram_mb: float = 0.0,
    ) -> SignedManifest | None:
        """Sign ``manifest`` and dispatch it to the idlest eligible peer.

        Returns the :class:`SignedManifest` that was sent, or ``None`` if no
        eligible idle peer exists (recorded as ``no_idle_node``).
        """
        target = self.select_idle_node(idle_nodes, min_vram_mb=min_vram_mb)
        if target is None:
            self.metrics.no_idle_node += 1
            return None

        signed = sign_manifest(manifest, self._identity, speculative=True)
        self._pending[signed.manifest_hash] = _Pending(
            signed=signed,
            target_node_id=target.node_id,
            dispatched_at_ms=self._clock(),
        )
        self.metrics.dispatched += 1
        if not target.is_lan:
            self.metrics.wan_dispatches += 1
        if self._dispatch_fn is not None:
            self._dispatch_fn(target.node_id, signed)
        return signed

    # ------------------------------------------------------------------
    # Phase 3 — collect
    # ------------------------------------------------------------------

    def submit_result(
        self, signed: SignedManifest, result: Any, *, cost_ms: float = 0.0
    ) -> bool:
        """Cache a speculative result for a pending manifest (trusted-internal sink).

        This verifies the **origin's own manifest signature** (fail-closed: a result
        whose *manifest* signature does not validate is rejected and counted) — it
        does **not** authenticate ``result``/``cost_ms`` themselves. Those arrive as
        plain arguments and are cached as-is.

        Therefore: **never feed an untrusted peer's return directly to
        ``submit_result``.** Route untrusted results through
        :func:`llmesh.speculative.transport.ingest_result`, which verifies the peer's
        :class:`~llmesh.speculative.transport.SignedResult` signature and binds it to
        the dispatched peer before calling this method. Direct callers (bench / tests /
        in-process trusted producers) may use it with already-trusted results.

        Returns True iff the result was accepted into the cache.
        """
        if not signed.verify():
            self.metrics.signature_rejections += 1
            return False

        entry = self._pending.get(signed.manifest_hash)
        if entry is None:
            # Unknown / already-cleaned manifest: the work no longer helps.
            self.metrics.wasted_compute_ms += float(cost_ms)
            return False
        if entry.state in (_PULLED, _WASTED):
            # Result arrived too late to be useful — pure waste.
            self.metrics.wasted_compute_ms += float(cost_ms)
            return False

        entry.result = result
        entry.cost_ms = float(cost_ms)
        entry.state = _READY
        return True

    def pull(self, manifest_hash: str) -> tuple[bool, Any]:
        """Origin reached the branch: fetch the speculative result.

        Returns ``(True, result)`` on a cache hit, or ``(False, None)`` on a
        miss (no ready result — caller must compute locally). A dispatched but
        not-yet-ready speculation is marked wasted on miss (its late result will
        be discarded).
        """
        entry = self._pending.get(manifest_hash)
        if entry is not None and entry.state == _READY:
            entry.state = _PULLED
            self.metrics.hits += 1
            self.metrics.used_compute_ms += entry.cost_ms
            return True, entry.result

        self.metrics.misses += 1
        if entry is not None and entry.state == _DISPATCHED:
            # Speculation was too slow; any late result is now useless.
            entry.state = _WASTED
            self.metrics.wasted += 1
        return False, None

    def pull_or_compute(self, manifest_hash: str, local_fn: Callable[[], Any]) -> Any:
        """fast-fallback (SPEC-MESH-04): hit を返す、無ければ**即**ローカル計算。

        投機の完了を待たない — ``pull`` が miss を返したら ``local_fn`` を**その場で**
        呼んで結果を返す。timeout 待ちや inflight 投機の join は一切しない (待つと
        break-even が崩れる)。これが速度経路の唯一の入口になるよう設計されている
        (origin の本筋推論が投機の遅延に縛られない = 後付け不可の最高優先要件)。

        遅れて届いた投機結果は ``submit_result`` 側で wasted 計上され、安全に破棄される。
        """
        hit, value = self.pull(manifest_hash)
        if hit:
            return value
        return local_fn()

    def expected_executor(self, manifest_hash: str) -> str | None:
        """node_id of the peer this manifest was dispatched to (if still pending).

        Lets the origin-side result intake bind an incoming result to the peer it
        actually dispatched to (a peer that was never dispatched to must not be able
        to inject a result for this branch). Returns ``None`` if the manifest is not
        pending (already pulled/wasted/unknown) — intake treats that as fail-closed.
        """
        entry = self._pending.get(manifest_hash)
        return entry.target_node_id if entry is not None else None

    def discard_unpulled(self) -> int:
        """Mark every still-pending/ready speculation as wasted.

        Call at end-of-episode to account for predictions that never got pulled
        (branch not taken). Ready-but-unused results add to ``wasted_compute_ms``.
        Returns the number of speculations discarded.
        """
        discarded = 0
        for entry in self._pending.values():
            if entry.state in (_DISPATCHED, _READY):
                if entry.state == _READY:
                    self.metrics.wasted_compute_ms += entry.cost_ms
                entry.state = _WASTED
                self.metrics.wasted += 1
                discarded += 1
        return discarded

    @property
    def inflight(self) -> int:
        """Speculations dispatched but neither pulled nor discarded."""
        return sum(
            1 for e in self._pending.values() if e.state in (_DISPATCHED, _READY)
        )

    # ------------------------------------------------------------------
    # Honest disclosure
    # ------------------------------------------------------------------

    def disclosure(self) -> dict[str, Any]:
        """Time-series-friendly snapshot for ``docs/perf_comparison/`` tracking.

        The efficacy of speculative mesh execution is **unproven**: it only pays
        off when ``hit_rate`` is high *and* the mesh round-trip beats local swap.
        Treat ``wasted_compute_ms`` / ``wan_dispatches`` as the energy cost to
        weigh against any latency win, and suspect a "win" until the breakdown
        holds up.
        """
        d = self.metrics.to_dict()
        d["inflight"] = self.inflight
        d["require_lan"] = self._require_lan
        d["honest_note"] = (
            "speculative mesh は hit_rate が高く、かつ mesh 往復 < ローカル swap の "
            "ときのみ得。WAN (wan_dispatches) は負ける前提。wasted_compute_ms は "
            "投機外れの環境負荷。速くなったと主張する前に内訳を疑うこと。"
        )
        return d


__all__ = [
    "IdleNode",
    "SpeculativeMeshCoordinator",
    "SpeculativeMetrics",
]
