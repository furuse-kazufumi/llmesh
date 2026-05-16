"""Merkle tree helpers for skill chunk integrity (RFC Phase 3).

Uses SHA-256 throughout. Tree shape:
  * leaves are hashes of consecutive ``chunk_size`` byte slices of the body
  * internal nodes hash pairs ``sha256(left || right)``
  * odd nodes carry forward (duplicate-last convention — same as Bitcoin)
"""
from __future__ import annotations

import hashlib
from collections.abc import Iterable

DEFAULT_CHUNK_SIZE = 4096


def _leaves(body: bytes, chunk_size: int) -> list[bytes]:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if not body:
        return [hashlib.sha256(b"").digest()]
    return [
        hashlib.sha256(body[i : i + chunk_size]).digest()
        for i in range(0, len(body), chunk_size)
    ]


def _step_up(level: Iterable[bytes]) -> list[bytes]:
    items = list(level)
    if len(items) % 2 == 1:
        items.append(items[-1])  # duplicate-last
    return [
        hashlib.sha256(items[i] + items[i + 1]).digest()
        for i in range(0, len(items), 2)
    ]


def compute_merkle_root(body: bytes, chunk_size: int = DEFAULT_CHUNK_SIZE) -> str:
    """Return the Merkle root as hex SHA-256."""
    level = _leaves(body, chunk_size)
    while len(level) > 1:
        level = _step_up(level)
    return level[0].hex()


def _all_levels(body: bytes, chunk_size: int) -> list[list[bytes]]:
    levels = [_leaves(body, chunk_size)]
    while len(levels[-1]) > 1:
        levels.append(_step_up(levels[-1]))
    return levels


def merkle_proof(body: bytes, leaf_index: int, chunk_size: int = DEFAULT_CHUNK_SIZE) -> list[tuple[str, str]]:
    """Return the audit path for the leaf at ``leaf_index``.

    Each step is ``(direction, sibling_hex)`` where direction is ``"L"`` if
    the sibling is on the left of the current node (i.e. we are the right
    half), else ``"R"``.
    """
    levels = _all_levels(body, chunk_size)
    if leaf_index < 0 or leaf_index >= len(levels[0]):
        raise IndexError(f"leaf_index out of range: {leaf_index}")
    proof: list[tuple[str, str]] = []
    idx = leaf_index
    for level in levels[:-1]:
        items = level if len(level) % 2 == 0 else level + [level[-1]]
        if idx % 2 == 0:
            sibling = items[idx + 1]
            proof.append(("R", sibling.hex()))
        else:
            sibling = items[idx - 1]
            proof.append(("L", sibling.hex()))
        idx //= 2
    return proof


def verify_merkle_proof(leaf_hash_hex: str, proof: list[tuple[str, str]], root_hex: str) -> bool:
    """Verify a leaf hash against an expected root using an audit path."""
    cur = bytes.fromhex(leaf_hash_hex)
    for direction, sibling_hex in proof:
        sibling = bytes.fromhex(sibling_hex)
        if direction == "L":
            cur = hashlib.sha256(sibling + cur).digest()
        elif direction == "R":
            cur = hashlib.sha256(cur + sibling).digest()
        else:
            raise ValueError(f"bad direction: {direction!r}")
    return cur.hex() == root_hex


__all__ = ["DEFAULT_CHUNK_SIZE", "compute_merkle_root", "merkle_proof", "verify_merkle_proof"]
