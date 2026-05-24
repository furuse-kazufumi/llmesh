"""SpeculativeExecutor — peer-side speculative execution (SPEC-MESH-03).

A mesh peer receives a signed :class:`~llmesh.speculative.manifest.SignedManifest`
predicting a branch the origin is likely to explore, runs it speculatively at
**low priority**, and returns a :class:`~llmesh.speculative.transport.SignedResult`
so the origin can verify *which* peer produced the result (provenance) and that it
was not tampered with in transit.

Trust model (fail-closed, see CLAUDE.md MCP 規約 "外部入力は untrusted"):

* The incoming manifest's Ed25519 signature is verified **before** any work runs.
  A manifest that does not verify is rejected and never executed.
* An optional ``allowed_origins`` allow-list restricts which origins this peer will
  burn compute for (open mesh = ``None``; closed mesh = explicit pubkey set).
* ``run_fn`` is run inside a guard: an exception is counted (``exec_errors``) and
  swallowed — a poisoned branch must never crash the peer.

Honest disclosure: this layer wires *mechanism*, not OS scheduling. The manifest's
``priority`` (``<= 0`` by convention) is a hint the **host task queue** is expected
to honour; the executor itself does not preempt the host's confirmed work. The
measured ``cost_ms`` (wall-clock of ``run_fn``) feeds SPEC-MESH-07 disclosure.

    ident = NodeIdentity.generate()
    ex = SpeculativeExecutor(ident, run_fn=lambda branch: {"answer": branch["n"] * 2})
    signed_result = ex.handle_signed(signed_manifest)   # None if rejected
    assert signed_result.verify()
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from time import perf_counter
from typing import Any

from .manifest import SignedManifest

# branch dict -> opaque result payload (JSON-serialisable).
RunFn = Callable[[dict[str, Any]], Any]


@dataclass
class ExecutorMetrics:
    """Honest-disclosure counters for the peer executor."""

    received: int = 0
    rejected_signature: int = 0   # manifest Ed25519 verify failed (fail-closed)
    rejected_origin: int = 0      # origin not in allow-list (fail-closed)
    executed: int = 0             # run_fn completed and a result was signed
    exec_errors: int = 0          # run_fn raised; counted, never propagated
    busy_ms: float = 0.0          # total measured run_fn wall-clock

    def to_dict(self) -> dict[str, Any]:
        return {
            "received": self.received,
            "rejected_signature": self.rejected_signature,
            "rejected_origin": self.rejected_origin,
            "executed": self.executed,
            "exec_errors": self.exec_errors,
            "busy_ms": self.busy_ms,
        }


class SpeculativeExecutor:
    """Peer-side handler that verifies, runs, and signs speculative branches.

    Parameters
    ----------
    identity:
        This peer's Ed25519 identity. Every returned result is signed with it so
        the origin can attribute the result to this peer (provenance).
    run_fn:
        ``(branch_dict) -> result`` callable performing the speculative work. It
        should be pure-ish and side-effect free (speculation may be discarded).
    allowed_origins:
        Optional iterable of origin public-key hex strings this peer will execute
        for. ``None`` (default) accepts any **validly signed** manifest (open mesh);
        a non-empty set turns the peer into a closed allow-list (fail-closed).
    clock:
        Injectable ``() -> float`` seconds clock for deterministic cost tests
        (defaults to :func:`time.perf_counter`).
    """

    def __init__(
        self,
        identity: Any,
        run_fn: RunFn,
        *,
        allowed_origins: Iterable[str] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._identity = identity
        self._run = run_fn
        self._allowed: frozenset[str] | None = (
            frozenset(allowed_origins) if allowed_origins is not None else None
        )
        self._clock = clock or perf_counter
        self.metrics = ExecutorMetrics()

    @property
    def public_key_hex(self) -> str:
        return self._identity.public_key_hex

    # ------------------------------------------------------------------

    def handle_signed(self, signed: SignedManifest) -> "SignedResult | None":
        """Verify, run, and sign one speculative branch.

        Returns a :class:`SignedResult` on success, or ``None`` when the manifest
        is rejected (bad signature / disallowed origin) or ``run_fn`` raises.
        """
        # Local import: transport imports manifest, executor imports transport-free
        # types here only to avoid a heavyweight top-level cycle.
        from .transport import SignedResult

        self.metrics.received += 1

        # 1. fail-closed signature verification — never run unverified work.
        if not signed.verify():
            self.metrics.rejected_signature += 1
            return None

        # 2. optional closed-mesh allow-list.
        if self._allowed is not None and signed.origin_pub_hex not in self._allowed:
            self.metrics.rejected_origin += 1
            return None

        # 3. run speculatively, guarded — a poisoned branch must not crash the peer.
        t0 = self._clock()
        try:
            result = self._run(dict(signed.manifest.branch))
        except Exception:
            self.metrics.exec_errors += 1
            return None
        cost_ms = max(0.0, (self._clock() - t0) * 1000.0)
        self.metrics.busy_ms += cost_ms
        self.metrics.executed += 1

        # 4. sign the result (provenance + tamper-evidence on the return path).
        return SignedResult.create(
            manifest_hash=signed.manifest_hash,
            result=result,
            cost_ms=cost_ms,
            identity=self._identity,
        )

    def handle_payload(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        """Wire entry point: decode → :meth:`handle_signed` → encode.

        ``payload`` is the JSON body of an incoming ``/speculative/dispatch`` POST
        (a :meth:`SignedManifest.to_dict`). Returns the result dict to send back,
        or ``None`` on rejection. Malformed payloads are rejected fail-closed.
        """
        try:
            signed = SignedManifest.from_dict(payload)
        except (KeyError, TypeError, ValueError):
            self.metrics.received += 1
            self.metrics.rejected_signature += 1
            return None
        signed_result = self.handle_signed(signed)
        return signed_result.to_dict() if signed_result is not None else None


__all__ = ["ExecutorMetrics", "RunFn", "SpeculativeExecutor"]
