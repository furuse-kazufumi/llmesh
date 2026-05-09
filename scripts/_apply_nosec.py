"""One-shot: append `# nosec` annotations with rationale to bandit-flagged lines.

Run once locally then delete. Idempotent: only appends when not already present.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (relative_path, 1-indexed_line, nosec_suffix)
ANNOTATIONS: list[tuple[str, int, str]] = [
    # B310: urllib.urlopen — URL is constructed internally or validated upstream;
    # response body is bounded by read_capped or a hard byte ceiling. Schemes
    # restricted to http/https by builders. See docs/SECURITY.md.
    ("llmesh/discovery/gossip.py", 103,
     "# nosec B310 - peer URL is signed/verified upstream; response capped."),
    ("llmesh/llm/anthropic_backend.py", 152,
     "# nosec B310 - https://api.anthropic.com hardcoded; response size capped."),
    ("llmesh/llm/llamacpp.py", 65,
     "# nosec B310 - server URL controlled by operator; response capped."),
    ("llmesh/llm/llamacpp.py", 105,
     "# nosec B310 - server URL controlled by operator; response capped."),
    ("llmesh/llm/ollama.py", 50,
     "# nosec B310 - Ollama URL controlled by operator; response capped."),
    ("llmesh/llm/ollama.py", 85,
     "# nosec B310 - Ollama URL controlled by operator; response capped."),
    ("llmesh/llm/openai_compatible.py", 207,
     "# nosec B310 - base_url validated by builder; https-only; response capped."),
    ("llmesh/mcp/sca_gate.py", 151,
     "# nosec B310 - osv.dev URL hardcoded; response capped."),
    ("llmesh/orchestrator/node_client.py", 180,
     "# nosec B310 - peer URL verified via Capability Manifest; response capped."),
    ("llmesh/privacy/image_summarizer.py", 133,
     "# nosec B310 - VLM endpoint controlled by operator; response capped."),
    ("llmesh/protocol/http_adapter.py", 137,
     "# nosec B310 - target URL validated upstream; response capped."),
    ("llmesh/rag/embedder.py", 155,
     "# nosec B310 - embedding endpoint controlled by operator; response capped."),
    ("llmesh/rendezvous/client.py", 70,
     "# nosec B310 - rendezvous URL is operator-configured; response capped."),
    ("llmesh/rendezvous/client.py", 108,
     "# nosec B310 - rendezvous URL is operator-configured; response capped."),

    # B613: bidi characters intentionally embedded in firewall regex to *detect*
    # them in user input — removing them would disable the L0 detector.
    ("llmesh/privacy/firewall.py", 98,
     "# nosec B613 - bidi chars intentionally present to detect them in input."),

    # B402 / B321: ftp_adapter implements the FTP protocol on purpose; risks are
    # documented in docs/SECURITY.md and the adapter is opt-in via [ftp] extra.
    ("llmesh/protocol/ftp_adapter.py", 312,
     "# nosec B402 - FTP support is the documented purpose of this adapter."),
    ("llmesh/protocol/ftp_adapter.py", 329,
     "# nosec B321 - FTP support is the documented purpose of this adapter."),

    # B507 (AutoAddPolicy): LLMesh nodes authenticate each other via the
    # Capability Manifest (Ed25519 pkey-only auth, sentinel cmd, no password) —
    # the SSH host key fingerprint is intentionally not pinned because mesh
    # nodes are ephemeral and trust is established at the manifest layer.
    ("llmesh/protocol/sftp_adapter.py", 445,
     "# nosec B507 - peer trust established via Capability Manifest, not host keys."),
    ("llmesh/protocol/ssh_adapter.py", 286,
     "# nosec B507 - peer trust established via Capability Manifest, not host keys."),

    # B601 (paramiko shell injection): the command is a fixed sentinel constant
    # (_SENTINEL_CMD); no user input flows into exec_command.
    ("llmesh/protocol/ssh_adapter.py", 297,
     "# nosec B601 - command is the fixed _SENTINEL_CMD constant; no user input."),

    # B608 (SQL injection): the interpolated value is `_TERMINAL_EVENTS`, a
    # module-level tuple of literal event-type strings — not user input.
    ("llmesh/timeline/store.py", 190,
     "# nosec B608 - interpolated value is _TERMINAL_EVENTS literal tuple."),
]


def append_nosec(path: Path, lineno: int, suffix: str) -> bool:
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")
    idx = lineno - 1
    if idx < 0 or idx >= len(lines):
        print(f"SKIP {path}:{lineno} — out of range")
        return False
    line = lines[idx]
    if "# nosec" in line:
        print(f"SKIP {path}:{lineno} — already annotated")
        return False
    rstripped = line.rstrip()
    sep = "  " if rstripped else ""
    lines[idx] = rstripped + sep + suffix
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"OK   {path}:{lineno}")
    return True


def main() -> None:
    for rel, lineno, suffix in ANNOTATIONS:
        p = ROOT / rel
        if not p.exists():
            print(f"MISS {rel}")
            continue
        append_nosec(p, lineno, suffix)


if __name__ == "__main__":
    main()
