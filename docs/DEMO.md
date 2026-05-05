# LLMesh — Reproducible Demo

This walkthrough demonstrates the LLMesh PoC end to end on a single host.
It covers:

1. Install + run the test suite
2. Boot the 5-node Docker Compose PoC
3. **Scenario A:** L0 public-safe task (happy path)
4. **Scenario B:** L3 secret-code prompt (firewall block)
5. Inspect the audit trace
6. Tear down

> **Status legend**
> - ✅ = command exists and is exercised by the test suite
> - 🧪 = command works against a running node (manual)
> - 🛠 = planned / not yet wired into a CLI; covered by tests for now

All paths are relative to the repository root.

---

## Prerequisites

| Tool | Version | Purpose |
|---|---|---|
| Python | 3.11+ | Runtime |
| Docker + Compose | v3.9+ | 5-node PoC |
| Ollama or llama-server | latest | LLM backend (only needed for live LLM calls — Scenarios A/B below run via tests without a model loaded) |

---

## 1. Install and run the test suite ✅

```bash
pip install -e ".[dev]"
python -m pytest
# expected: 526 passed in ~4s
```

Optional security scan:

```bash
python -m bandit -r llmesh/ -ll
# expected: 0 High / 0 Critical (medium-severity B310 / B104 are scheme-audit / allowlist false-positives)
```

---

## 2. Boot the 5-node PoC 🧪

```bash
docker compose -f docker-compose.poc.yml up --build
```

Topology (from `docker-compose.poc.yml`):

| Container | Host port → container port | Role | Hardening |
|---|---|---|---|
| `llmesh-node-a` | 8001 → 8000 | `generate_code` | `cap_drop:[ALL]`, `read_only`, `tmpfs:/tmp:noexec`, `no-new-privileges` |
| `llmesh-node-b` | 8002 → 8000 | `generate_tests` | same |
| `llmesh-node-c` | 8003 → 8000 | `review_code` | same |
| `llmesh-node-d` | 8004 → 8000 | `critique_output` | same |
| `llmesh-orchestrator` | 8005 → 8000 | orchestrator | same |

The compose file uses `internal: true` so containers cannot reach the public
Internet. The OSV-backed SCA Gate therefore returns `sca_network_error`
(fail-closed) for any tool call carrying `dependencies_added` unless you
provide an OSV proxy on the internal network. See [`SETUP.md` §6](../SETUP.md).

Health check (from the host):

```bash
curl http://localhost:8001/health
# {"status":"ok","tools":["critique_output","generate_code","generate_tests","review_code"]}
```

---

## 3. Scenario A — L0 public-safe task ✅

This is the happy-path scenario covered by
[`tests/e2e/test_public_safe_task.py`](../tests/e2e/test_public_safe_task.py).

Run the test directly:

```bash
python -m pytest tests/e2e/test_public_safe_task.py -vv
```

What it verifies:

1. A clean prompt ("Implement a bounded retry utility in Python.") passes the
   `PromptFirewall` (Layer 1 + Layer 2).
2. The MCP node returns a response that satisfies all 7 stages of
   `OutputValidator` (size, JSON, schema, nonce echo, UUIDv4 task_id,
   server-side nonce store, SCA gate).
3. `LocalSynthesizer` consumes the validated outputs.
4. `AuditTrace` records `output_validated` events with `policy_decision="ALLOW"`,
   and `verify_chain()` confirms the HMAC chain is intact.

Manual equivalent against a live node (🧪):

```bash
TASK_ID=$(python -c 'import uuid; print(uuid.uuid4())')
NONCE=$(python -c 'import secrets; print(secrets.token_hex(16))')

curl -X POST http://localhost:8001/tools/generate_code \
  -H "Content-Type: application/json" \
  -d "{
    \"task_id\": \"$TASK_ID\",
    \"caller_nonce\": \"$NONCE\",
    \"prompt\": \"Implement a bounded retry utility in Python.\",
    \"language\": \"python\"
  }"
```

> Requires a reachable Ollama / llama-server backend. The MCP node will reply
> with the validated tool output JSON, including `task_id` and `caller_nonce_echo`
> matching what you sent.

---

## 4. Scenario B — L3 secret-code prompt blocked ✅

Covered by [`tests/e2e/test_secret_code_blocked.py`](../tests/e2e/test_secret_code_blocked.py).

```bash
python -m pytest tests/e2e/test_secret_code_blocked.py -vv
```

What it verifies:

1. Prompts containing AWS access keys, Anthropic / OpenAI API keys, GitHub
   tokens, or PEM private-key headers are blocked by `PromptFirewall` Layer 1.
2. `OutputValidator.validate()` is **never** called when the firewall blocks
   (defence-in-depth invariant).
3. `firewall.wrap()` returns a `ClassifiedPayload` with
   `policy_decision="BLOCK"` and `level=DataLevel.L4`.
4. `AuditTrace.log()` records the block event with **only** `prompt_sha256` —
   the prompt body itself is verified absent from the JSONL log.
5. `AuditTrace.verify_chain()` confirms the HMAC chain remains intact.

Manual equivalent (🧪 — only against a live node):

```bash
TASK_ID=$(python -c 'import uuid; print(uuid.uuid4())')
NONCE=$(python -c 'import secrets; print(secrets.token_hex(16))')

curl -X POST http://localhost:8001/tools/generate_code \
  -H "Content-Type: application/json" \
  -d "{
    \"task_id\": \"$TASK_ID\",
    \"caller_nonce\": \"$NONCE\",
    \"prompt\": \"AKIAIOSFODNN7EXAMPLE is my key\",
    \"language\": \"python\"
  }"
# expected HTTP 422 with body: {"detail":"firewall_blocked:layer1_secret_detected:aws_access_key"}
```

---

## 5. Inspect the audit trace 🧪

To enable per-node audit logging, set two environment variables before starting
the node (these are picked up at module load in `llmesh/mcp/server.py`):

```bash
export LLMESH_AUDIT_LOG_PATH=/abs/path/to/audit.jsonl
export LLMESH_AUDIT_HMAC_KEY=$(python -c 'import secrets; print(secrets.token_hex(32))')
```

> The HMAC key must be valid hex. Use **64 hex chars (32 bytes)** as the
> standard size. Do **not** commit the key to source control. Treat it like
> a private key — anyone with it can forge audit entries.

After running some tool calls, inspect the JSONL log:

```bash
head -n 3 "$LLMESH_AUDIT_LOG_PATH"
```

Each entry contains: `seq_no`, `event_type`, `node_id`, `task_id`,
`policy_decision`, `output_sha256`, `timestamp`, `entry_hmac` and (for
data_level ≥ 3) `prompt_sha256`. The prompt body is **never** present.

Verify the chain:

```python
from llmesh.audit import AuditTrace
import os

ok = AuditTrace.verify_chain(
    os.environ["LLMESH_AUDIT_LOG_PATH"],
    bytes.fromhex(os.environ["LLMESH_AUDIT_HMAC_KEY"]),
)
print("chain ok:", ok)
```

A return of `True` means every entry's HMAC chains correctly to the previous
one and `seq_no` is contiguous. Any tampering, deletion, or reordering
returns `False`.

> **Multi-worker note (P1-2).** `AuditTrace` currently uses a process-local
> `threading.Lock`. Running multiple uvicorn workers against the **same**
> audit log will currently corrupt the chain. Run a single writer per file,
> or wait for `fcntl.flock` integration (see `docs/ROADMAP.md` P1-2).

---

## 6. Tear down

```bash
docker compose -f docker-compose.poc.yml down
```

If you set audit env vars, unset them so they don't leak into other shells:

```bash
unset LLMESH_AUDIT_LOG_PATH LLMESH_AUDIT_HMAC_KEY
```

---

## What this demo deliberately does NOT show

- **Multi-PC peering** — see `PEERING.md` for the TLS / TOFU / rendezvous flow.
- **Phase 2 AES-256-GCM endpoint encryption** — implemented (`llmesh/discovery/encrypted_announce.py`)
  but not yet wired into the rendezvous server. See ROADMAP P1-6.
- **Live SCA gating against the OSV API** — the PoC compose blocks egress.
  Run a single node against your normal network (no compose, no
  `internal: true`) to exercise the OSV path manually.
