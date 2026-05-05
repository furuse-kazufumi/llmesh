---
name: Feature request
about: Propose a new capability or extension to LLMesh.
title: "[feature] "
labels: enhancement
assignees: ''
---

## Motivation

What problem does this solve? Who benefits? Be concrete about the use case
(e.g. "operators running 3+ peers across machines need a CLI to bulk-revoke
trusted peers" rather than "improve UX").

## Proposal

Describe the feature. If it's an API surface, sketch the function signatures /
endpoint shapes. If it's a config flag, name it (`LLMESH_*` env vars are
preferred over CLI flags for server-side knobs).

## Alternatives considered

Why this design over the obvious alternatives?

## Compatibility & security implications

- [ ] Does this change canonical signed strings (request signing, manifest, announce)?
- [ ] Does this widen the auth bypass list, or expose new public endpoints?
- [ ] Does this introduce a new fail-open path?
- [ ] Does this require new dependencies? If yes, why is the stdlib insufficient?
- [ ] Does it interact with the SCA Gate, NonceStore, or AuditTrace?

If any of the above are "yes," please also fill in the **Security hardening**
template's failure-modes section, or split this into a hardening issue.

## Acceptance criteria

Bullet-pointed checklist of what "done" looks like.
Include test coverage expectations.
