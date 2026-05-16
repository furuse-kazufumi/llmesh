"""llmesh.skills — Skill chunk replication (RFC Phase 3).

`SkillChunk` is a signed, Merkle-verified unit of knowledge intended to be
replicated between FullSense peers. See
`docs/llmesh_p2p_phase3_skill_chunk_rfc.md` (in the llive repo) for the
design contract.

This module currently provides the **core data model** (Phase 3.1).
Storage (Phase 3.2) and HTTP transport (Phase 3.3) are TBD.
"""

from llmesh.skills.chunk import (
    SCHEMA_VERSION,
    SkillChunk,
    SkillChunkError,
)
from llmesh.skills.merkle import compute_merkle_root, merkle_proof, verify_merkle_proof

__all__ = [
    "SCHEMA_VERSION",
    "SkillChunk",
    "SkillChunkError",
    "compute_merkle_root",
    "merkle_proof",
    "verify_merkle_proof",
]
