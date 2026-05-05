# Security Policy

## Reporting Vulnerabilities

Report vulnerabilities by opening a GitHub Security Advisory (private).

## Forbidden Patterns

The following are forbidden in all LLMesh source code:

- `subprocess.run(..., shell=True)` — use list form only
- `pickle.loads()`, `marshal.loads()` — use `json.loads()` only
- `yaml.load()` — use `yaml.safe_load()` for task files only
- `eval()`, `exec()` — no dynamic code execution
- SQL string concatenation — use parameterized queries only

These are enforced by Bandit, Semgrep, and CI.

## Fail-closed Design

All security components (Firewall, OutputValidator, Manifest checker) must
return BLOCK on any unhandled exception. Never fail open.
