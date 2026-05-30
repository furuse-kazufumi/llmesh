# Security Policy — LLMesh v0.2.0

> **かみ砕いた説明（中学生レベル）**
>
> このページは「LLMesh をどう安全に守るか」のルールブックです。LLMesh は、たくさんのコンピュータ（ノード）が手をつないで、文章を作る AI に仕事を頼み合うしくみです。悪い人がなりすましたり、こっそりデータを盗み見たり、わざとたくさん仕事を投げてパンクさせたりしないよう、入り口ごとに「合言葉の確認」「中身の検査」「記録の保存」といった見張りを置いています。あやしいときは通さず止める（安全側に倒す）のが基本です。
>
> 用語の意味は [用語集（GLOSSARY.md）](GLOSSARY.md) を見てください。

## Reporting Vulnerabilities

Report vulnerabilities by opening a GitHub Security Advisory (private disclosure).
Do **not** open a public issue for security bugs.

---

## STRIDE Threat Model

LLMesh is a peer-to-peer LLM swarm in which nodes exchange MCP tool calls over
HTTP.  The threat model covers the HTTP boundary, the prompt pipeline, and the
node-to-node trust fabric.

### System actors

| Actor | Trust level |
|---|---|
| Local operator (runs the node) | Fully trusted |
| Peer node in `trusted_peers.json` | Verified by Ed25519 key |
| Unauthenticated caller | Untrusted — must prove identity |
| LLM backend (Ollama / llama.cpp) | Trusted for execution, untrusted for content |
| Rendezvous / gossip peers | Untrusted until key-verified |

---

### S — Spoofing

**Threat:** An attacker impersonates a trusted peer node to send tool calls that
bypass the firewall or extract privileged outputs.

**Controls:**

| Control | Location |
|---|---|
| Ed25519 request signing | `llmesh/auth/signer.py`, `verifier.py` |
| `TrustedPeers` allow-list with gossip TTL | `llmesh/auth/trusted_peers.py` |
| `X-Node-Id` header tied to signed keypair | `llmesh/mcp/server.py` |
| DID-key identity (`did:llmesh:1:z6Mk…`) | `llmesh/identity/node_id.py` |
| Capability manifest with Ed25519 + TTL | `llmesh/identity/manifest.py` |

**Residual risk:** DNS spoofing (not in scope; mitigated by pinning node endpoints).

---

### T — Tampering

**Threat:** An attacker modifies audit log entries, manifest fields, or prompt
content in transit to change the security record or bypass firewall rules.

**Controls:**

| Control | Location |
|---|---|
| HMAC-SHA256 chain on every audit log entry | `llmesh/audit/trace.py` |
| Explicit signed-field list in manifest (`_V1_SIGNED_FIELDS`) | `llmesh/identity/manifest.py` |
| Ed25519 manifest signature covers tools, models, privacy policy | `llmesh/identity/manifest.py` |
| TLS (operator-configured) for in-transit protection | `scripts/gen_certs.py` |
| `AuditTrace.verify_chain()` detects any entry mutation | `llmesh/audit/trace.py` |

**Residual risk:** Operator must configure TLS; plaintext HTTP is permitted in
LAN-only deployments with `allow_private=True`.

---

### R — Repudiation

**Threat:** A node denies having sent a malicious prompt or received an output,
making incident response impossible.

**Controls:**

| Control | Location |
|---|---|
| Append-only HMAC-chained audit log (every firewall decision, LLM call, error) | `llmesh/audit/trace.py` |
| SHA-256 of every prompt stored in audit (not the raw text) | `llmesh/privacy/firewall.py` |
| Nonce + task UUID recorded per request | `llmesh/mcp/server.py` |
| File-level locking prevents concurrent log truncation | `llmesh/audit/trace.py` |

**Residual risk:** Log is append-only but not write-once storage.
Operators must protect the log file with OS-level permissions.

---

### I — Information Disclosure

**Threat:** Sensitive data (secrets, PII, internal paths, proprietary code)
leaks to an untrusted LLM backend or across node boundaries.

**Controls:**

| Control | Location |
|---|---|
| Layer 0 — prompt injection detection (blocks exfil via instruction override) | `llmesh/privacy/firewall.py` |
| Layer 1 — secret pattern scanner (API keys, JWTs, PEM keys, tokens) | `llmesh/privacy/firewall.py` |
| Layer 1.5 — Microsoft Presidio PII detection (CC / SSN / IBAN / medical / personal — optional, v2.13+) | `llmesh/privacy/presidio_detector.py` |
| Layer 2 — structural classifier (absolute paths, internal imports) | `llmesh/privacy/firewall.py` |
| RAG retriever — re-runs the firewall on indexed documents and on each query (v2.13+) | `llmesh/rag/retriever.py` |
| L3 → PrivacySummarizer: raw text never reaches backend | `llmesh/privacy/summarizer.py`, `server.py` |
| L4 → hard BLOCK: prompt never reaches summarizer or backend | `llmesh/mcp/server.py` |
| Fail-closed: any pipeline exception → BLOCK (never fail-open) | `llmesh/privacy/firewall.py` |
| SHA-256 of prompt stored in audit (not plaintext) | `llmesh/audit/trace.py` |
| OutputValidator strips unexpected fields from LLM responses | `llmesh/mcp/validator.py` |
| SSRF prevention: EndpointValidator blocks private IPs, IMDS | `llmesh/security/endpoint_validator.py` |
| OWASP security response headers (X-Content-Type-Options, CSP, …) | `llmesh/mcp/server.py` |

**Residual risk:** Summarization uses a local LLM call; a prompt that reaches
the summarizer may still leak some structural information to that model.

---

### D — Denial of Service

**Threat:** An attacker floods a node with requests, submits oversized payloads,
or abuses fanout to exhaust resources across the swarm.

**Controls:**

| Control | Location |
|---|---|
| Per-node token-bucket rate limiter (10 req/s, burst 20) | `llmesh/security/rate_limiter.py`, `server.py` |
| Content-Length body size cap (64 KB) before JSON parse | `llmesh/mcp/server.py` |
| Firewall payload cap (16 KB) per prompt | `llmesh/privacy/firewall.py` |
| Nonce TTL (300 s) limits replay-flood window | `llmesh/mcp/nonce_store.py` |
| Circuit breaker on per-node fanout connections | `llmesh/routing/circuit_breaker.py` |
| `max_gossip_peers` cap on gossip propagation | `llmesh/auth/trusted_peers.py` |
| `Cache-Control: no-store` prevents response caching amplification | `llmesh/mcp/server.py` |

**Residual risk:** Rate limiting is per-node-ID header — a caller spoofing
`X-Node-Id` can rotate identities to bypass per-node limits.  IP-based limiting
or mutual TLS is recommended for production deployments.

---

### E — Elevation of Privilege

**Threat:** A peer node claims capabilities it was not granted, injects tool
names or parameters, or leverages prompt injection to execute arbitrary actions
on behalf of a higher-privilege node.

**Controls:**

| Control | Location |
|---|---|
| Layer 0 firewall blocks instruction-override injection before any execution | `llmesh/privacy/firewall.py` |
| `_ALLOWED_TOOLS` allow-list: only declared tool names are routable | `llmesh/mcp/server.py` |
| Tool schema validation (input / output JSON Schema) | `llmesh/mcp/validator.py` |
| Capability manifest scopes tools + privacy policy per node | `llmesh/identity/manifest.py` |
| Nonce uniqueness prevents replayed elevated requests | `llmesh/mcp/nonce_store.py`, `SqliteNonceStore` |
| `TrustedPeers` gossip TTL: stale elevated-trust records expire | `llmesh/auth/trusted_peers.py` |
| SCA gate blocks code generation that introduces known-CVE dependencies | `llmesh/mcp/sca_gate.py` |

**Residual risk:** The SCA gate queries OSV at code-generation time; a
zero-day vulnerability in a freshly published package will not be caught until
OSV indexes it.

---

## Forbidden Patterns

The following patterns are banned in all LLMesh source code and enforced by
Bandit, Semgrep, and CI:

| Pattern | Safe alternative |
|---|---|
| `subprocess.run(..., shell=True)` | List-form subprocess only |
| `pickle.loads()`, `marshal.loads()` | `json.loads()` only |
| `yaml.load()` | `yaml.safe_load()` only |
| `eval()`, `exec()` | No dynamic code execution |
| SQL string concatenation | Parameterized queries (`?` placeholders) |
| `os.system()` | List-form `subprocess.run()` |
| Hardcoded secrets / credentials | Environment variables only |
| `np.load(..., allow_pickle=True)` on untrusted files | Pickle-free serialisation (UTF-8 JSON payload + `np.uint8` buffer) — see `llmesh/rag/numpy_store.py` v2.16+ |
| Unbounded `resp.read()` from external HTTP | Pass an explicit byte cap and raise on overflow — see `OllamaEmbedder` v2.16+ |

---

## Fail-Closed Design

Every security component must return **BLOCK** (not raise, not return ALLOW) on
any unhandled exception:

- `PromptFirewall.classify()` → `_FAIL_CLOSED` sentinel on exception
- `PrivacySummarizer` failure in server → `422 l3_summarization_failed_closed`
- `OutputValidator` failure → `502 llm_output_invalid`
- `SqliteNonceStore` DB failure → request rejected
- `AuditTrace` write failure → exception propagated (request not silently logged)

**Never fail open.**

---

## CI Enforcement

| Check | Workflow |
|---|---|
| Pytest (coverage >= 80 %) | `.github/workflows/ci.yml` |
| Bandit medium+ severity | `.github/workflows/ci.yml` |
| Bandit all severities (project config) | `.github/workflows/security.yml` |
| Semgrep python + command-injection rules | `.github/workflows/security.yml` |
