"""llmesh.skills — Skill chunk replication (RFC Phase 3).

`SkillChunk` is a signed, Merkle-verified unit of knowledge intended to be
replicated between FullSense peers. See
`docs/llmesh_p2p_phase3_skill_chunk_rfc.md` (in the llive repo) for the
design contract.

Provides:
  * Phase 3.1 — core data model (SkillChunk + Merkle helpers + Ed25519)
  * Phase 3.2 — SkillReplica (LRU + popularity, SQLite-backed)

HTTP transport (Phase 3.3+) is TBD.
"""

from llmesh.skills.chunk import (
    SCHEMA_VERSION,
    SkillChunk,
    SkillChunkError,
)
from llmesh.skills.merkle import compute_merkle_root, merkle_proof, verify_merkle_proof
from llmesh.skills.replica import EvictionResult, SkillReplica
from llmesh.skills.sync import (
    GossipScheduler,
    HTTPTransport,
    PeerProvider,
    PolicyDecision,
    PullPolicyCheck,
    SkillSyncClient,
    SkillSyncError,
    SyncResult,
    UrllibTransport,
)

__all__ = [
    "SCHEMA_VERSION",
    "EvictionResult",
    "GossipScheduler",
    "HTTPTransport",
    "PeerProvider",
    "PolicyDecision",
    "PullPolicyCheck",
    "SkillChunk",
    "SkillChunkError",
    "SkillReplica",
    "SkillSyncClient",
    "SkillSyncError",
    "SyncResult",
    "UrllibTransport",
    "compute_merkle_root",
    "merkle_proof",
    "verify_merkle_proof",
]
