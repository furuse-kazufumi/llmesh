"""Speculative execution across the llmesh P2P mesh ("thought relay").

While a node runs its main inference, it predicts the next branches it is likely
to explore, signs them with its Ed25519 key, and dispatches them to *idle* mesh
peers for speculative execution. When the node reaches a branch it pulls the
result from the mesh (cache hit) instead of recomputing locally (cache miss).
Branch prediction (Phase 1) lives in the inference engine; this package owns the
mesh dispatch (Phase 2) and result collection (Phase 3).

    from llmesh.speculative import (
        SpeculativeManifest, SpeculativeMeshCoordinator, IdleNode,
    )

    coord = SpeculativeMeshCoordinator(origin_identity)
    m = SpeculativeManifest.new(origin_node_id=origin_identity.node_id, branch={...})
    signed = coord.dispatch(m, idle_nodes=[IdleNode("peer:B", cpu_load=0.1)])
    coord.submit_result(signed, result={...}, cost_ms=120.0)   # sig verified
    hit, value = coord.pull(signed.manifest_hash)
    print(coord.disclosure())                                  # honest disclosure

Honest disclosure is mandatory: speculative execution only wins when the hit
rate is high *and* a mesh round-trip beats local VRAM swap (LAN, not WAN).
"""
from __future__ import annotations

from .coordinator import IdleNode, SpeculativeMeshCoordinator, SpeculativeMetrics
from .manifest import (
    SignatureError,
    SignedManifest,
    SpeculativeManifest,
    sign_manifest,
)

__all__ = [
    "IdleNode",
    "SignatureError",
    "SignedManifest",
    "SpeculativeManifest",
    "SpeculativeMeshCoordinator",
    "SpeculativeMetrics",
    "sign_manifest",
]
