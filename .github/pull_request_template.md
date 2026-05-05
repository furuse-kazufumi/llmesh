<!--
Thanks for contributing to LLMesh.
Please fill out the sections below. PRs missing the security checklist
will be asked to add it before review.
-->

## Summary

What does this PR change, and why? Link the issue(s) it addresses.

Closes #
Related: #

## Type

- [ ] Bug fix
- [ ] Security hardening (defence-in-depth, no behaviour change visible to clients)
- [ ] Feature / new capability
- [ ] Documentation only
- [ ] Refactor / cleanup (no behaviour change)
- [ ] Test-only change

## Test plan

How was this verified locally?

```bash
python -m pytest                          # → expected: all tests pass
python -m bandit -r llmesh/ -ll           # → expected: no new High/Critical
```

Add module-targeted commands if relevant, e.g.:

```bash
python -m pytest tests/test_<module>.py -vv
```

## Security checklist

- [ ] No new use of `shell=True`, `pickle`, unsafe `yaml.load`, `marshal`, `eval`, `exec`, `os.system`, or SQL string concatenation.
- [ ] No new fail-open paths in Firewall, OutputValidator, SCA Gate, or audit/sign verification.
- [ ] If a canonical signed string changed (`auth/signer.make_canonical`, rendezvous `_signed_message`, manifest `_signable_bytes`), backward compatibility / migration is documented.
- [ ] No L3/L4 prompt bodies are written to logs, audit, or error messages.
- [ ] No HMAC keys, private keys, tokens, or other secrets are added to source/tests/CI.
- [ ] Auth bypass list (`auth/verifier._BYPASS_PREFIXES`) is unchanged, or the change is justified in the description.

## Documentation

- [ ] `README.md` / `ARCHITECTURE.md` / `SETUP.md` / `PEERING.md` updated where behaviour or config surface changed.
- [ ] `docs/ROADMAP.md` updated if this resolves a tracked P1/P2/P3 item.
- [ ] Public-facing CLI / env var changes are documented.

## Breaking changes

- [ ] None.
- [ ] Yes — described above with migration notes.
