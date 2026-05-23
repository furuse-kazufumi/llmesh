"""Typed diff / patch over llrepr Documents — the "prediction error" primitive.

A diff between two llrepr documents is a list of JSON-Patch-style operations
(RFC 6902 subset: ``add`` / ``remove`` / ``replace``) over the canonical node
tree. This is the *typed diff-stream* the predictive-coding push uses: instead of
re-sending a whole representation, only the **difference (the prediction error)**
travels — the smaller the diff, the better the speculation was.

Round-trip guarantee::

    ops = diff_documents(a, b)
    apply_patch(a, ops).to_dict() == b.to_dict()

PoC scope: lists are compared index-wise (not LCS-aligned). That is sufficient
for predictive-coding push, where the speculative and confirmed representations
share structure and differ mostly in leaf values.
"""
from __future__ import annotations

import copy
from typing import Any

from .model import Document, LlreprValidationError

Op = dict[str, Any]


# ---------------------------------------------------------------------------
# JSON Pointer helpers (RFC 6901)
# ---------------------------------------------------------------------------

def _escape(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def _unescape(token: str) -> str:
    return token.replace("~1", "/").replace("~0", "~")


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------

def _diff(a: Any, b: Any, path: str, ops: list[Op]) -> None:
    if a == b:
        return

    if isinstance(a, dict) and isinstance(b, dict):
        for key in a:
            child = f"{path}/{_escape(str(key))}"
            if key not in b:
                ops.append({"op": "remove", "path": child})
            else:
                _diff(a[key], b[key], child, ops)
        for key in b:
            if key not in a:
                ops.append({"op": "add", "path": f"{path}/{_escape(str(key))}", "value": b[key]})
        return

    if isinstance(a, list) and isinstance(b, list):
        common = min(len(a), len(b))
        for i in range(common):
            _diff(a[i], b[i], f"{path}/{i}", ops)
        # Surplus in `a`: remove from the tail first so earlier indices stay valid.
        for i in range(len(a) - 1, common - 1, -1):
            ops.append({"op": "remove", "path": f"{path}/{i}"})
        # Surplus in `b`: append in order.
        for i in range(common, len(b)):
            ops.append({"op": "add", "path": f"{path}/{i}", "value": b[i]})
        return

    # Leaf value or type mismatch → replace.
    ops.append({"op": "replace", "path": path, "value": b})


def diff_dicts(a: dict[str, Any], b: dict[str, Any]) -> list[Op]:
    """Structural JSON-Patch-style diff between two JSON-compatible dicts."""
    ops: list[Op] = []
    _diff(a, b, "", ops)
    return ops


def diff_documents(a: Document, b: Document) -> list[Op]:
    """Typed diff between two llrepr documents (the prediction error)."""
    return diff_dicts(a.to_dict(), b.to_dict())


# ---------------------------------------------------------------------------
# Patch
# ---------------------------------------------------------------------------

def _tokens(path: str) -> list[str]:
    if not path:
        return []
    return [_unescape(t) for t in path.split("/")[1:]]


def apply_ops(base: Any, ops: list[Op]) -> Any:
    """Apply a list of diff ops to a JSON-compatible structure (deep-copied)."""
    doc = copy.deepcopy(base)
    for op in ops:
        kind = op["op"]
        tokens = _tokens(op["path"])

        if not tokens:
            if kind == "replace":
                doc = copy.deepcopy(op["value"])
                continue
            raise LlreprValidationError(f"cannot {kind} at document root")

        parent = doc
        for tok in tokens[:-1]:
            parent = parent[int(tok)] if isinstance(parent, list) else parent[tok]

        last = tokens[-1]
        if isinstance(parent, list):
            idx = len(parent) if last == "-" else int(last)
            if kind == "add":
                parent.insert(idx, copy.deepcopy(op["value"]))
            elif kind == "remove":
                parent.pop(idx)
            elif kind == "replace":
                parent[idx] = copy.deepcopy(op["value"])
            else:
                raise LlreprValidationError(f"unknown op: {kind}")
        else:
            if kind in ("add", "replace"):
                parent[last] = copy.deepcopy(op["value"])
            elif kind == "remove":
                del parent[last]
            else:
                raise LlreprValidationError(f"unknown op: {kind}")
    return doc


def apply_patch(a: Document, ops: list[Op]) -> Document:
    """Apply a typed diff to document *a*, returning the reconstructed document."""
    return Document.from_dict(apply_ops(a.to_dict(), ops))


# ---------------------------------------------------------------------------
# Prediction-error metric
# ---------------------------------------------------------------------------

def prediction_error(ops: list[Op]) -> int:
    """Magnitude of the prediction error = number of diff operations.

    0 means the speculation matched the confirmed representation exactly (best
    case: only an empty diff travels). Larger = the speculation was further off.
    """
    return len(ops)
