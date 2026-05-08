"""PointCloud — lightweight 3D point cloud type for LLMesh Industrial (v1.7.0).

Stores N points as (x, y, z) float32 tuples in metres.  Uses only stdlib
struct — no numpy dependency for the core type.

Wire format: 12 bytes per point, little-endian IEEE 754 float32 (x, y, z).

Usage::

    pc = PointCloud([(0.1, 0.2, 1.5), (0.3, -0.1, 2.0)])
    raw = pc.to_bytes()
    pc2 = PointCloud.from_bytes(raw)
    stats = pc2.stats()  # count, x/y/z_range, centroid

Security invariants
-------------------
- No shell=True, eval, exec, pickle anywhere.
- from_bytes truncates to complete 12-byte records only.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from collections.abc import Iterable

# Optional Rust acceleration (~10× faster).  When `llmesh_rust` is
# importable it transparently replaces the encode/decode hot paths;
# otherwise the pure-stdlib implementation is used.
try:
    import llmesh_rust as _rust    # type: ignore[import-not-found]
    _RUST_AVAILABLE = True
except ImportError:
    _rust = None                   # type: ignore[assignment]
    _RUST_AVAILABLE = False


@dataclass
class PointCloud:
    """N × 3 float32 point cloud (metres)."""

    points: list[tuple[float, float, float]]

    # ------------------------------------------------------------------
    # Factories
    # ------------------------------------------------------------------

    @classmethod
    def from_bytes(cls, data: bytes) -> PointCloud:
        """Decode a byte string produced by :meth:`to_bytes`."""
        if _RUST_AVAILABLE:
            return cls(points=_rust.pc_from_bytes(bytes(data)))
        n = len(data) // 12
        pts: list[tuple[float, float, float]] = [
            struct.unpack_from("<fff", data, i * 12) for i in range(n)  # type: ignore[misc]
        ]
        return cls(points=pts)

    @classmethod
    def from_iterable(cls, it: Iterable[tuple[float, float, float]]) -> PointCloud:
        """Create from any iterable of (x, y, z) tuples."""
        return cls(points=list(it))

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_bytes(self) -> bytes:
        """Encode to little-endian float32 triples."""
        if _RUST_AVAILABLE:
            return _rust.pc_to_bytes(self.points)
        buf = bytearray(len(self.points) * 12)
        for i, (x, y, z) in enumerate(self.points):
            struct.pack_into("<fff", buf, i * 12, float(x), float(y), float(z))
        return bytes(buf)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def count(self) -> int:
        return len(self.points)

    def stats(self) -> dict[str, object]:
        """Return basic statistics (pure-stdlib, no numpy)."""
        if not self.points:
            return {"count": 0}
        xs = [p[0] for p in self.points]
        ys = [p[1] for p in self.points]
        zs = [p[2] for p in self.points]
        cx = sum(xs) / len(xs)
        cy = sum(ys) / len(ys)
        cz = sum(zs) / len(zs)
        return {
            "count": len(self.points),
            "x_range": (min(xs), max(xs)),
            "y_range": (min(ys), max(ys)),
            "z_range": (min(zs), max(zs)),
            "centroid": (cx, cy, cz),
        }

    def __len__(self) -> int:
        return self.count
