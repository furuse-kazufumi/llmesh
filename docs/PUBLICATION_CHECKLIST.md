# LLMesh — Publication Checklist

A pre-publication punch-list across the venues most likely to be relevant.
Items are ordered roughly by sequence: do GitHub first (canonical source),
then PyPI (machine consumers), then write-ups (humans).

> Nothing in this checklist is automated. Every step should be a deliberate
> decision by a maintainer.

---

## A. GitHub repository

### A.1 Repository hygiene

- [ ] Repository name decided (working title: `llmesh`).
- [ ] Default branch is `main`; old branches pruned or archived.
- [ ] `LICENSE` (Apache-2.0) committed at repo root.
- [ ] `README.md` matches the launch tagline ("Secure Local LLM Swarm over MCP").
- [ ] `SECURITY.md` describes private vulnerability reporting and forbidden patterns.
- [ ] `.github/ISSUE_TEMPLATE/{bug_report,security_hardening,feature_request}.md` present.
- [ ] `.github/pull_request_template.md` present and includes the security checklist.
- [ ] `docs/ROADMAP.md` is up to date with current P1/P2/P3.
- [ ] `docs/DEMO.md` runs against a clean checkout.
- [ ] `SESSION_SUMMARY_2026-05-05.md` is annotated as a historical snapshot
      (test counts in that file are NOT the current truth).
- [ ] `.gitignore` excludes `nodes/`, `certs/`, `config/*.bin`, `*.jsonl`, `__pycache__/`, `.pytest_cache/`.

### A.2 Secrets / artefacts review

Before the first push, grep the working tree:

- [ ] No `*.key`, `*.pem`, `*.crt` committed (except sample/demo CA fixtures clearly marked).
- [ ] No `node.key.bin`, `trusted_peers.json`, or audit JSONL committed.
- [ ] No `LLMESH_AUDIT_HMAC_KEY=` literals in source/tests/CI.
- [ ] No real Ollama / llama.cpp endpoints with internal IPs.
- [ ] `git log` and `git stash list` checked for accidental secret commits.

```bash
# Quick local sanity check
grep -RIn --exclude-dir={.git,__pycache__,.pytest_cache,certs,nodes,config} \
    -E 'BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY|sk-ant-|ghp_|AKIA[0-9A-Z]{16}' .
```

### A.3 CI / status

- [ ] `.github/workflows/ci.yml` runs `pytest` + Bandit on push/PR.
- [ ] `.github/workflows/security.yml` runs Bandit (project config) + Semgrep python + command-injection rulesets.
- [ ] First run on `main` is green before announcing.
- [ ] (Optional) Branch protection: require CI green + 1 review for `main`.

### A.4 Discoverability

- [ ] Repository description ≤ 350 chars summarising LLMesh.
- [ ] Topic tags: `llm`, `mcp`, `security`, `p2p`, `ed25519`, `local-llm`,
      `ollama`, `llama-cpp`, `swarm`, `python`.
- [ ] Pinned issue or `discussions` thread for "where do I start?" questions.

---

## B. PyPI (optional — only if shipping a published package)

- [ ] Package name reserved (`llmesh` or alternative if taken).
- [ ] `pyproject.toml` `[project]` block has: `name`, `version`, `description`, `readme`,
      `requires-python`, `license = "Apache-2.0"`, `authors`, `urls`.
- [ ] `urls.Homepage` and `urls.Source` point at the public repo.
- [ ] `MANIFEST.in` (or `[tool.hatch]` includes) ships `LICENSE`, `README.md`, schemas.
- [ ] `python -m build` produces both sdist and wheel without warnings.
- [ ] `twine check dist/*` is clean.
- [ ] First upload to TestPyPI and successful install in a fresh venv.
- [ ] Final upload to PyPI uses a project-scoped API token, **not** an account password.

---

## C. Write-ups (Qiita / Zenn / Medium / blog)

A staged announcement is fine — short post first, deep dive later.

### Short launch post

- [ ] One-line tagline (matches README).
- [ ] Two screenshots / asciinema casts: `pytest` green, `docker compose up` healthy.
- [ ] Threat-model TL;DR: what LLMesh defends against and what it does **not**.
- [ ] Link to repo + `docs/DEMO.md`.

### Long-form / Qiita / Zenn

- [ ] Why local-LLM swarms need security primitives (motivation).
- [ ] Walk through `OutputValidator` 7-stage gate with a concrete failing input per stage.
- [ ] Explain the canonical signed-string design (manifest, request, announce)
      and why each field is in the canonical.
- [ ] Discuss the gossip / TOFU trade-off and how to opt out.
- [ ] Roadmap call-out: P1 hardening items + how readers can contribute.

---

## D. LinkedIn / X / Mastodon

Keep this short and link-driven; it is a discovery channel, not docs.

- [ ] Single paragraph hook (≤ 600 chars): tagline + the most surprising
      design choice (e.g. "every LLM response goes through 7 fail-closed gates").
- [ ] Repo link.
- [ ] One image: architecture diagram or audit-trace JSONL screenshot.
- [ ] Hashtags: `#LLM #LocalLLM #MCP #Security #Python` (subset).

---

## E. Whitepaper / formal write-up (later)

A whitepaper makes sense once P1 hardening lands and a real multi-PC
deployment has been used in anger. Suggested skeleton:

- [ ] Abstract (≤ 250 words).
- [ ] Threat model: actors, assumptions, scope (trusted-LAN; not public-Internet).
- [ ] System overview: Ed25519 identity, did:llmesh:1:, MCP gateway, audit chain.
- [ ] Cryptographic primitives in use and why (Ed25519, X25519/HKDF, AES-GCM, HMAC-SHA256).
- [ ] Failure-mode analysis: what happens if X is compromised (firewall regex evaded,
      OSV unreachable, rendezvous spoofed, peer key leaked, audit key leaked).
- [ ] Comparison table vs naive MCP swarms / ad-hoc local LLM stacks.
- [ ] Limitations + future work (matches `docs/ROADMAP.md`).
- [ ] References.

---

## F. Pre-launch dry run

Run this in order, on a clean checkout, on a clean machine if possible:

1. [ ] `git clone <repo> && cd llmesh`
2. [ ] `pip install -e ".[dev]"`
3. [ ] `python -m pytest` → 526 passed
4. [ ] `python -m bandit -r llmesh/ -ll` → 0 High / 0 Critical
5. [ ] `docker compose -f docker-compose.poc.yml up --build` → all 5 nodes healthy
6. [ ] Run the two e2e scenarios from `docs/DEMO.md`.
7. [ ] `docker compose down` cleans up.
8. [ ] Re-read `README.md` end-to-end and click every internal link.

If any step fails, the launch is not ready.
