"""gen_synthetic_dataset.py — Reproducible synthetic data generator.

Produces drop-folder-ready files for testing AoiAdapter,
DepthCameraAdapter, and EventCameraAdapter without external datasets.
All output is deterministic (fixed seed) so paper experiments reproduce
exactly across machines.

Usage::

    python tools/gen_synthetic_dataset.py --type aoi   --count 100 --out tests/_synth/aoi/
    python tools/gen_synthetic_dataset.py --type depth --count 50  --out tests/_synth/depth/
    python tools/gen_synthetic_dataset.py --type dvs   --count 200 --out tests/_synth/dvs/

Each invocation is *idempotent*: re-running with the same seed and count
produces byte-identical files.

Output formats
--------------
**AOI**: `<i>.jpg` (minimal valid JPEG marker bytes + payload) +
`<i>.aoi.json` sidecar (defects / result / board_id).

**Depth**: `<i>.depth.bin` — 4-byte uint32 width + 4-byte uint32 height +
width*height float32 little-endian metres.

**DVS**: `<i>.dvs.bin` — 9-byte records (uint16 x, uint16 y, uint32 t_us,
uint8 polarity).
"""
from __future__ import annotations

import argparse
import json
import random
import struct
from pathlib import Path

# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

_DEFAULT_SEED = 42

# AOI synthesis: defective ratio drives label distribution
_AOI_NG_RATIO = 0.30                 # 30% of generated boards are NG
_AOI_DEFECT_LABELS = ("scratch", "void", "short", "open", "misalignment")
_AOI_BBOX_RANGE = (16, 240)          # bbox coordinate range for 256×256 image

# Depth synthesis: indoor scene ranges (metres)
_DEPTH_DEFAULT_WIDTH = 64
_DEPTH_DEFAULT_HEIGHT = 48
_DEPTH_MIN_M = 0.40                  # nearest plausible reading
_DEPTH_MAX_M = 4.50                  # farthest plausible reading

# DVS synthesis: event rate and frame timing
_DVS_EVENTS_PER_BATCH_MIN = 256
_DVS_EVENTS_PER_BATCH_MAX = 4096
_DVS_RES_X = 640
_DVS_RES_Y = 480
_DVS_T_WINDOW_US = 50_000            # 50 ms per batch

# JPEG SOI/EOI markers — produces a parseable but minimal "image"
_JPEG_SOI = b"\xff\xd8"
_JPEG_EOI = b"\xff\xd9"


# ---------------------------------------------------------------------------
# AOI generator
# ---------------------------------------------------------------------------

def _gen_aoi(rng: random.Random, idx: int) -> tuple[bytes, dict]:
    """Return (jpeg_bytes, sidecar_dict) for AOI inspection sample *idx*."""
    is_ng = rng.random() < _AOI_NG_RATIO
    defects: list[dict] = []
    if is_ng:
        # 1–3 defects per NG board
        for _ in range(rng.randint(1, 3)):
            x = rng.randint(*_AOI_BBOX_RANGE)
            y = rng.randint(*_AOI_BBOX_RANGE)
            w = rng.randint(4, 32)
            h = rng.randint(4, 32)
            defects.append({
                "label": rng.choice(_AOI_DEFECT_LABELS),
                "confidence": round(rng.uniform(0.7, 0.99), 3),
                "bbox": [x, y, w, h],
            })

    sidecar = {
        "result": "ng" if is_ng else "ok",
        "board_id": f"BOARD-{idx:05d}",
        "defects": defects,
    }

    # Minimal JPEG-like payload: SOI + 64 bytes of payload + EOI.
    # Real consumers should use Pillow; this synthesizer aims to exercise
    # the LLMesh AoiAdapter file-watching + sidecar pipeline only.
    payload = bytes(rng.randint(0, 255) for _ in range(64))
    return _JPEG_SOI + payload + _JPEG_EOI, sidecar


# ---------------------------------------------------------------------------
# Depth generator
# ---------------------------------------------------------------------------

def _gen_depth(rng: random.Random, idx: int,
               width: int = _DEPTH_DEFAULT_WIDTH,
               height: int = _DEPTH_DEFAULT_HEIGHT) -> bytes:
    """Return raw bytes for a synthetic depth.bin frame."""
    header = struct.pack("<II", width, height)
    pixels = bytearray()
    # Synthesize a slowly varying gradient with random per-pixel noise.
    base = rng.uniform(_DEPTH_MIN_M, _DEPTH_MAX_M / 2)
    for row in range(height):
        for col in range(width):
            d = base + (row + col) * 0.01 + rng.gauss(0, 0.02)
            if d < _DEPTH_MIN_M or d > _DEPTH_MAX_M:
                d = 0.0   # invalid pixel
            pixels.extend(struct.pack("<f", d))
    return header + bytes(pixels)


# ---------------------------------------------------------------------------
# DVS generator
# ---------------------------------------------------------------------------

def _gen_dvs(rng: random.Random, idx: int) -> bytes:
    """Return raw bytes for one synthetic .dvs.bin batch."""
    n = rng.randint(_DVS_EVENTS_PER_BATCH_MIN, _DVS_EVENTS_PER_BATCH_MAX)
    out = bytearray(n * 9)

    # Burst of events sorted by timestamp (DVS hardware emits monotonically)
    base_t = idx * _DVS_T_WINDOW_US
    timestamps = sorted(rng.randrange(0, _DVS_T_WINDOW_US) for _ in range(n))

    for i, t_offset in enumerate(timestamps):
        x = rng.randrange(0, _DVS_RES_X)
        y = rng.randrange(0, _DVS_RES_Y)
        t_us = (base_t + t_offset) & 0xFFFF_FFFF
        polarity = 1 if rng.random() < 0.5 else 0
        struct.pack_into("<HHIb", out, i * 9, x, y, t_us, polarity)
    return bytes(out)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def _run(kind: str, count: int, out_dir: Path, seed: int) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    written = 0

    for i in range(count):
        if kind == "aoi":
            jpeg, sidecar = _gen_aoi(rng, i)
            (out_dir / f"frame_{i:05d}.jpg").write_bytes(jpeg)
            (out_dir / f"frame_{i:05d}.aoi.json").write_text(
                json.dumps(sidecar, ensure_ascii=False, indent=2)
            )
        elif kind == "depth":
            data = _gen_depth(rng, i)
            (out_dir / f"frame_{i:05d}.depth.bin").write_bytes(data)
        elif kind == "dvs":
            data = _gen_dvs(rng, i)
            (out_dir / f"batch_{i:05d}.dvs.bin").write_bytes(data)
        else:
            raise ValueError(f"unknown kind: {kind}")
        written += 1

    return written


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--type", required=True, choices=("aoi", "depth", "dvs"))
    p.add_argument("--count", type=int, default=10)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--seed", type=int, default=_DEFAULT_SEED)
    args = p.parse_args()

    n = _run(args.type, args.count, args.out, args.seed)
    print(f"wrote {n} {args.type} samples → {args.out}")


if __name__ == "__main__":
    main()
