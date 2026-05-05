# LLMesh

**Secure Local LLM Swarm over MCP** — a security-first peer-to-peer mesh for collaborative local LLM workflows.

LLMesh lets multiple local LLM nodes (Ollama, llama.cpp) cooperate on coding tasks — code generation, test generation, security review, output critique — over signed MCP (Model Context Protocol) calls, with a fail-closed firewall, schema-validated tool I/O, append-only audit trail, and SCA dependency gating built in from the start.

> **Status (local review):** 526 tests passing · 0 failures · 0 Critical · 0 High security findings.
> **Maturity:** research / PoC. Designed for **trusted LAN or single-operator multi-PC setups**. Not yet hardened for untrusted public Internet deployment.

---

## Why LLMesh

Most local-LLM "swarms" are built for convenience first and security later. LLMesh inverts that: every cross-node interaction is signature-bound, every tool response goes through a 7-stage validator, every secret-pattern in a prompt is fail-closed-blocked, and every accepted decision is logged into an HMAC-chained append-only audit trace.

You bring the GPUs and the models. LLMesh provides the trust boundary, the protocol, and the guardrails.

---

## Highlights

- **Ed25519 node identity** with stable `peer:` IDs and W3C-style `did:llmesh:1:` decentralized identifiers.
- **Signed Capability Manifests** with TTL — peers advertise tools, subnets, and policies under a verifiable signature.
- **TOFU + signed P2P discovery** via a rendezvous server (`POST /announce`) and gossip pull (`GET /registry/peers`). Canonical signatures bind `node_id`, `endpoint`, timestamp, public key, and DID together so MITM key substitution cannot succeed.
- **Request-level auth** with Ed25519 signatures over `METHOD\nPATH\nNODE_ID\nTS\nBODY_SHA256`, ±30s freshness, and a per-(node, nonce) replay store.
- **Fail-closed Prompt Firewall** with two layers (regex secret detection, structural checks) — any exception in the pipeline returns L4/BLOCK.
- **OutputValidator 7-stage gate** — size cap → JSON-only parse → JSON Schema → nonce echo → `task_id` UUIDv4 → server-side replay store → SCA Gate (OSV CRITICAL/HIGH ⇒ block).
- **HMAC append-only AuditTrace** — sequential JSONL entries chained by HMAC-SHA256. Prompt bodies for L3/L4 are never stored, only their SHA-256.
- **Container hardening** — `--network=none` sandbox profile, `cap_drop:[ALL]`, `read_only`, `tmpfs:noexec`, `no-new-privileges`, non-root UID 65532/65533.
- **Zero unsafe patterns by policy** — no `shell=True`, no `pickle`, no unsafe `yaml.load`, no `eval`/`exec`, no SQL string concatenation. Enforced via Bandit + Semgrep in CI.

---

## Security-First Design Principles

1. **Fail-closed, not fail-open.** Every guard component returns BLOCK on any unhandled exception.
2. **Untrusted-by-default.** LLM responses, peer manifests, and rendezvous announcements are treated as untrusted until a validator clears them.
3. **No secret bodies in audit logs.** L3/L4 prompts are recorded as SHA-256 only.
4. **Defence in depth.** Transport TLS + request signing + body hashing + nonce store + manifest TTL + SCA gate are layered, not alternative.
5. **Explicit trust transitions.** Initial peer addition is TOFU (manual fingerprint check). Gossip propagation is opt-out for operators who want strict control.

---

## Quick Start (single host)

For users:

```bash
pip install llmesh-mcp
```

For local development from a cloned repository:

```bash
# 1. Install development dependencies
pip install -e ".[dev]"

# 2. Run the test suite
python -m pytest          # → 526 passed

# 3. Optional: static security scan
python -m bandit -r llmesh/ -ll
```

Published package:

- PyPI: <https://pypi.org/project/llmesh-mcp/>
- GitHub Release: <https://github.com/furuse-kazufumi/llmesh/releases/tag/v0.1.0>
- Qiita launch article: <https://qiita.com/furuse-kazufumi/items/ac398349ec42e40913f1>
- LinkedIn launch post: <https://www.linkedin.com/feed/update/urn:li:share:7457372822668230657/>

### Running a single MCP node

```bash
uvicorn llmesh.mcp.server:app --host 127.0.0.1 --port 8001
```

Endpoints exposed: `POST /tools/{generate_code|generate_tests|review_code|critique_output}`, `GET /health`, `GET /identity`, plus the `/registry/*` peer discovery API.

See [`SETUP.md`](SETUP.md) for full Ollama/llama.cpp configuration and environment variables (`LLMESH_BACKEND`, `LLMESH_AUDIT_LOG_PATH`, `LLMESH_AUDIT_HMAC_KEY`, etc.).

---

## 5-Node PoC Demo (Docker Compose)

```bash
docker compose -f docker-compose.poc.yml up --build
```

This brings up four worker nodes (`generate_code`, `generate_tests`, `review_code`, `critique_output`) plus an orchestrator on a hardened internal network. See [`docs/DEMO.md`](docs/DEMO.md) for the full reproducible flow including the L0 happy-path and the L3 secret-block scenario.

> The PoC compose uses `internal: true` so containers cannot reach the public Internet. The OSV-backed SCA Gate therefore returns `sca_network_error` (fail-closed) unless you provide an OSV proxy on the internal network — see [`SETUP.md` §6](SETUP.md).

---

## Multi-PC Setup

For two or more physical machines on a LAN, see [`PEERING.md`](PEERING.md). It covers:

- Self-CA TLS bootstrap (`scripts/gen_certs.py`)
- TOFU peer addition with fingerprint verification
- Optional rendezvous server lookup by `node_id` or `did:llmesh:1:`
- Gossip auto-propagation (60-second pull interval)
- Threat-model table (eavesdropping, replay, impersonation, gossip pollution)

---

## Documentation Map

| File | Contents |
|---|---|
| [`README.md`](README.md) | This file — entry point, status, quick start |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Module map, OutputValidator 7-stage gate, dataflow, DataLevel taxonomy |
| [`SETUP.md`](SETUP.md) | Install, single-node, Docker Compose PoC, troubleshooting |
| [`PEERING.md`](PEERING.md) | Multi-PC, TLS, TOFU, rendezvous, gossip, threat model |
| [`SECURITY.md`](SECURITY.md) | Vulnerability reporting, forbidden-pattern policy, fail-closed contract |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | P0 baseline → P1 hardening → P2 hygiene → P3 community |
| [`docs/DEMO.md`](docs/DEMO.md) | Reproducible 5-node demo flow |
| [`docs/PUBLICATION_CHECKLIST.md`](docs/PUBLICATION_CHECKLIST.md) | Pre-publication tasks across GitHub / PyPI / write-up venues |
| [`docs/LAUNCH_KIT.md`](docs/LAUNCH_KIT.md) | GitHub / PyPI / Qiita / LinkedIn launch copy and publication plan |
| [`SESSION_SUMMARY_2026-05-05.md`](SESSION_SUMMARY_2026-05-05.md) | Historical snapshot of an earlier development session |

---

## Project Status

| Metric | Value |
|---|---|
| Tests | **526 passed / 0 failed** (local) |
| Critical findings | 0 |
| High findings | 0 |
| Medium findings | 5 (operational hardening — see ROADMAP P1) |
| Bandit (medium+) | 11 issues, all B310 / B104 false positives |
| Forbidden patterns (`shell=True`, `pickle`, `yaml.load` unsafe, `marshal`, `eval`, `exec`, `os.system`, SQL concat) | 0 in source |

---

## Contributing

Issues and pull requests are welcome. Please use the templates in `.github/ISSUE_TEMPLATE/` (bug, security hardening, feature) and `.github/pull_request_template.md`. For security-impacting changes, prefer the **Security Hardening** issue template and reference the relevant ROADMAP item.

For coordinated disclosure, see [`SECURITY.md`](SECURITY.md) — please do **not** open a public issue for vulnerabilities.

---

## Disclaimer

LLMesh is a research / proof-of-concept implementation. The current design targets:

- A trusted LAN, or
- A small set of explicitly TOFU-verified peers under a single operator.

It is **not** ready for deployment on the open Internet against arbitrary peers. Notably:

- Per-node rate limiting is implemented but global QoS / abuse mitigation is not.
- The NonceStore is in-memory only; a node restart re-opens a brief replay window.
- Gossip uses transitive trust by design — see PEERING.md for opt-out guidance.
- The Phase 2 AES-256-GCM endpoint encryption path is built but not yet wired into the rendezvous flow.

Use accordingly.

---

## License

Apache License 2.0 — see [`LICENSE`](LICENSE).

Copyright 2026 Kazufumi Furuse.
