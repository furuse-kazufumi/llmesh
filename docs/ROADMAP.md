# LLMesh Roadmap

This roadmap reflects the state of the codebase after the Round 3 internal
review (526 tests passing, 0 Critical, 0 High). It is organised by phase,
not by calendar date.

Issue titles below are intended to be opened verbatim under the
**Security hardening** or **Feature request** templates in `.github/ISSUE_TEMPLATE/`.

---

## P0 — Completed Baseline (current)

The following are implemented, tested, and documented:

- **Identity & trust**
  - Ed25519 `NodeIdentity` + `did:llmesh:1:` derivation with multicodec `0xed01` + base58btc
  - Signed `CapabilityManifest` with TTL and explicit signature verification
  - W3C-style DID resolver (`identity/resolver.py`)
  - X25519 ECDH (Ed25519 → Curve25519 birational map) with HKDF-SHA256
- **P2P discovery**
  - Rendezvous server (`POST /announce`, `GET /lookup/{node_id}`)
  - Canonical signed bytes bind `node_id | endpoint | timestamp | public_key_hex | did`
  - `EndpointValidator` wired into rendezvous `announce` and `/registry/register`
  - `GossipClient` (60s pull, manifest signature verification on ingest)
  - TOFU `TrustedPeers` with atomic JSON persistence
- **Request authentication**
  - Ed25519 request signer/verifier middleware
  - Canonical = `METHOD\nPATH\nNODE_ID\nTS_MS\nBODY_SHA256` (body bound)
  - ±30s freshness; missing headers → 401; bad sig → 403
  - `/registry/peers` is **trusted-peer only** (no longer in bypass list)
- **MCP protocol**
  - Schemas for `generate_code`, `generate_tests`, `review_code`, `critique_output`
  - `OutputValidator` 7-stage gate: size → JSON → schema → nonce echo → UUIDv4 → server NonceStore → SCA Gate
  - `task_id` validated via both regex (schema) and `uuid.UUID().version == 4`
  - SCA Gate via OSV `/v1/querybatch`, CRITICAL/HIGH ⇒ block, network error ⇒ fail-closed
- **Audit & forensics**
  - `AuditTrace` append-only HMAC-SHA256 chained JSONL
  - L3/L4 prompts: `prompt_sha256` only, never the body
  - Wired into `mcp/server.py`, `PromptFirewall`, `OutputValidator`
  - `verify_chain()` API + e2e tests verifying chain integrity and body exclusion
- **Privacy & firewall**
  - `PromptFirewall` Layer 1 (12 secret regex patterns) and Layer 2 (path/import/size)
  - Fail-closed: any unhandled exception ⇒ L4/BLOCK
  - `PrivacySummarizer` (L3/L4 → L1) with secret masking and code-signature extraction
- **Sandboxing & deployment**
  - 5-node `docker-compose.poc.yml` with `cap_drop:[ALL]`, `read_only`, `tmpfs:noexec`, `no-new-privileges`, internal network
  - Standalone sandbox profile (`docker/sandbox/`) with `--network=none`, `--env-file /dev/null`, seccomp JSON
- **CI**
  - `.github/workflows/ci.yml` (pytest + coverage ≥80% + Bandit medium+)
  - `.github/workflows/security.yml` (Bandit project config + Semgrep python + command-injection rulesets)

### P0 quality gates

- `pytest`: **526 passed / 0 failed**
- Bandit: 0 High / 0 Critical (11 medium are B310 / B104 false-positives)
- Forbidden patterns (`shell=True`, `pickle`, unsafe `yaml.load`, `marshal`, `eval`, `exec`, `os.system`, SQL string concat) in source: **0**

---

## P1 — Operational Hardening (next)

Critical for moving past PoC into "trusted multi-PC operator" use cases.

| # | Issue title | Module(s) |
|---|---|---|
| P1-1 | NonceStore: persist across restarts (sqlite) or enforce grace window on cold start | `llmesh/mcp/nonce_store.py`, `llmesh/mcp/server.py` |
| P1-2 | Audit log: add `fcntl.flock` to support multi-worker uvicorn safely | `llmesh/audit/trace.py` |
| P1-3 | TrustedPeers: cap `trusted_peers.json` size and add per-introducer TTL for gossip-added entries | `llmesh/auth/trusted_peers.py`, `llmesh/discovery/gossip.py` |
| P1-4 | CapabilityManifest: replace `__dict__`-based `_signable_bytes` with explicit schema_version-aware field list | `llmesh/identity/manifest.py` |
| P1-5 | Wire `PromptFirewall → PrivacySummarizer → LLMBackend` pipeline for L3+ inputs in `mcp/server.py` | `llmesh/mcp/server.py`, `llmesh/privacy/summarizer.py` |
| P1-6 | AES-GCM endpoint encryption: require non-zero salt or migrate to ChaCha20-Poly1305 in design notes; do not enable Phase 2 production until decided | `llmesh/discovery/encrypted_announce.py`, `PEERING.md` |

Acceptance: each issue must add tests + update docs (`ARCHITECTURE.md` /
`SETUP.md` / `PEERING.md` as applicable) and must not introduce a new
fail-open path.

---

## P2 — Hygiene & Research

Lower-impact but worth doing before broader publication.

| # | Issue title | Module(s) |
|---|---|---|
| P2-1 | Drop unused `base58>=2.1` runtime dependency | `pyproject.toml` |
| P2-2 | Add `# noqa: S310` to all `urllib.request.urlopen` call sites to silence Bandit B310 noise | various |
| P2-3 | Tighten password regex in `firewall.py` to avoid false-positive on benign technical text | `llmesh/privacy/firewall.py` |
| P2-4 | Per-node global QoS / abuse mitigation beyond per-(node, endpoint) token bucket | `llmesh/security/rate_limiter.py` |
| P2-5 | Provide an OSV-mirror sidecar image so `internal: true` networks can still run the SCA Gate | `docker-compose.poc.yml`, `SETUP.md` |
| P2-6 | Optional contribution-tracking telemetry for the Code Development Subnet | `llmesh/routing/contribution.py` |

---

## P3 — Documentation & Community

These are publication-prep items rather than code changes.

| # | Issue title |
|---|---|
| P3-1 | Annotate `SESSION_SUMMARY_2026-05-05.md` as a historical snapshot to avoid 448 vs 526 confusion |
| P3-2 | Whitepaper draft: design rationale, threat model, comparison with naive MCP swarms |
| P3-3 | "Run LLMesh in 60 seconds" demo article (long-form Qiita / Zenn post) referencing `docs/DEMO.md` |
| P3-4 | Architecture diagram source files (PlantUML / Mermaid) committed under `docs/diagrams/` |
| P3-5 | Issue triage guide: which template to use for which symptom |
| P3-6 | CONTRIBUTING.md with a one-page security checklist for new contributors |

---

## Out of scope (for now)

- Public-Internet untrusted-peer deployment hardening (would require accepting
  per-call cost: PoW-style anti-spam, reputation, slashing).
- TEE / attested-compute integration (placeholder field `supports_tee: false`
  exists in `CapabilityManifest`).
- Non-coding subnets (vision, tool-using agents). Code Development Subnet
  is the only first-class subnet today.

---

## How to propose a roadmap change

Open an issue using **Feature request** or **Security hardening** and tag it
with the relevant phase prefix (`P1`, `P2`, `P3`). PRs that close a roadmap
item should reference its `P*-N` ID in the description.
