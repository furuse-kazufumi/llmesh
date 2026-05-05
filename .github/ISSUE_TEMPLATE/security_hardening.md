---
name: Security hardening
about: Non-vulnerability hardening proposal — defence-in-depth, threat-model gap, audit/compliance improvement.
title: "[hardening] "
labels: ["security", "hardening"]
assignees: ''
---

> **Important:** This template is for **non-confidential hardening proposals**.
> If you believe you have found an exploitable vulnerability, do **not** open a
> public issue. Follow the coordinated disclosure path described in
> [`SECURITY.md`](../../SECURITY.md) instead.

## Threat or weakness

What scenario or actor are we hardening against? Be concrete:
"a compromised peer can flood `trusted_peers.json` via gossip" beats
"gossip is risky."

## Affected component(s)

Module path(s) and line refs if known, e.g. `llmesh/auth/trusted_peers.py:95-115`.

## Current behaviour

Describe what the code does today and why it leaves the residual risk.
Cite `ARCHITECTURE.md` / `PEERING.md` / `SECURITY.md` sections if applicable.

## Proposed change

Sketch the design. Note whether it requires:

- [ ] New tests (unit / integration / e2e)
- [ ] Schema or canonical-string changes (breaks signature compat)
- [ ] Persistent storage (sqlite, file lock, etc.)
- [ ] Docs update in `ARCHITECTURE.md` / `SETUP.md` / `PEERING.md`
- [ ] Migration / backward-compat path

## Failure modes to verify

What must remain fail-closed under the new design? Examples:

- Firewall still BLOCKs on any unhandled exception.
- OutputValidator still rejects non-JSON, schema mismatches, nonce replay.
- AuditTrace still excludes L3/L4 prompt body.

## ROADMAP linkage

Reference the relevant `docs/ROADMAP.md` phase / item if this hardens an
already-tracked risk (e.g. P1: NonceStore persistence).
