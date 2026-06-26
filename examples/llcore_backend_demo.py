# SPDX-License-Identifier: Apache-2.0
"""See it run: llmesh serving FullSense **llcore**'s on-prem CPU model as a backend.

This is a watchable demo of the llmesh ↔ llcore integration (``llmesh/llm/llcore_backend.py``).
It constructs the backend, checks ``health()`` (which really loads the model), and runs a few
chat round-trips so you can SEE llmesh → llcore → a CPU-only small LLM answering.

Run (Windows / Git Bash, from anywhere):

    py -3.11 D:/projects/llmesh/examples/llcore_backend_demo.py

Prerequisites (all already present on this machine, 2026-06-26):
  - Python 3.11, ``pip install torch transformers safetensors`` (transformers 5.x verified)
  - A local Apache model dir, default ``D:/models/Qwen2.5-0.5B-Instruct``
    (override with ``--model <path-or-HF-id>``; an HF id downloads ~1GB on first run)
  - llcore importable: this script adds ``D:/projects/llcore/src`` to ``sys.path`` for the demo.
"""
from __future__ import annotations

import argparse
import sys
import time

# Make llmesh and llcore importable when run as a standalone script.
sys.path.insert(0, "D:/projects/llmesh")
sys.path.insert(0, "D:/projects/llcore/src")

from llmesh.llm.backend import LLMBackend  # noqa: E402
from llmesh.llm.llcore_backend import LlcoreBackend  # noqa: E402


def _ensure_utf8_stdout() -> None:
    """Windows 既定の cp932 console で日本語/↔/— を print してもクラッシュさせない。

    Windows console の既定 encoding は cp932 (Shift-JIS 派生) で、``↔`` (U+2194) や
    em-dash ``—`` (U+2014) を ``print`` すると ``UnicodeEncodeError`` で即死する。
    stdout を UTF-8 に貼り替えて fail-safe にする (llmesh.cli.* と同じ helper)。
    """
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, ValueError):  # pragma: no cover
        pass


def main() -> int:
    _ensure_utf8_stdout()
    ap = argparse.ArgumentParser(description="llmesh ↔ llcore backend demo (watchable)")
    ap.add_argument("--model", default="D:/models/Qwen2.5-0.5B-Instruct",
                    help="local model dir or HF id (HF id downloads on first run)")
    ap.add_argument("--max-new-tokens", type=int, default=32)
    args = ap.parse_args()

    print("=" * 64)
    print("llmesh ↔ llcore 連携デモ — llmesh が llcore のオンプレ CPU LLM を配信")
    print("=" * 64)
    backend = LlcoreBackend(model_id=args.model, max_new_tokens=args.max_new_tokens)
    print(f"backend class      : {type(backend).__name__}")
    print(f"is llmesh LLMBackend: {isinstance(backend, LLMBackend)}")

    print("\n[health] llcore とモデルを実際にロード中（初回は数十秒）...", flush=True)
    t0 = time.perf_counter()
    healthy = backend.health()
    print(f"[health] -> {healthy}  ({time.perf_counter() - t0:.1f}s)")
    if not healthy:
        print("llcore/torch/transformers が見つからないか、モデルをロードできません（fail-closed）。")
        return 1

    questions = [
        "日本の首都はどこ？一言で。",
        "3 たす 5 は？数字だけで。",
        "水の化学式は？",
    ]
    print("\n[chat] llmesh backend 経由で llcore に質問します:")
    for q in questions:
        t0 = time.perf_counter()
        answer = backend.chat("あなたは簡潔に答える日本語アシスタントです。", q)
        print(f"  Q: {q}")
        print(f"  A: {answer.strip()!r}   ({time.perf_counter() - t0:.1f}s)")
    print("\n[done] llmesh → llcore → CPU LLM の round-trip が動作しました。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
