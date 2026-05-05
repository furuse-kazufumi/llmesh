---
name: Bug report
about: Something is broken or behaves differently from the documentation.
title: "[bug] "
labels: bug
assignees: ''
---

## Summary

A short, clear description of the bug.

## Reproduction

Minimal steps to reproduce. Prefer a copy-pasteable command or test file.

```bash
# example
python -m pytest tests/test_<module>.py::TestX::test_y -vv
```

Include any non-default environment variables (`LLMESH_BACKEND`, `LLMESH_AUDIT_LOG_PATH`, etc.) — but **never paste secrets, HMAC keys, or private keys**.

## Expected behaviour

What you expected to happen, citing the relevant doc section if applicable
(`README.md`, `ARCHITECTURE.md`, `SETUP.md`, `PEERING.md`).

## Actual behaviour

What actually happened. Include the relevant excerpt from stderr / log output. **Redact any secrets first.**

## Environment

- Python version (`python --version`):
- OS / architecture:
- LLMesh commit / version:
- LLM backend (`ollama` / `llamacpp`) and model:
- Running mode: single node / Docker Compose PoC / multi-PC peering

## Additional context

- Does the failure reproduce on a clean checkout?
- Does `pytest` (full suite) still pass?
- If a security-impacting bug, please use the **Security Hardening** template
  or contact maintainers privately per `SECURITY.md` instead of opening a public issue.
