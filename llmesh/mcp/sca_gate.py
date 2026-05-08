"""SCA gate — OSV dependency vulnerability check for dependencies_added payloads.

Queries https://api.osv.dev/v1/querybatch for each dependency declared by a
remote node.  Returns a list of CveHit records; the caller (OutputValidator)
decides whether to block based on severity.

Network errors are surfaced via OsvQueryError so the validator can apply
its fail-closed/fail-open policy.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

# Allow Docker deployments to route OSV queries through the osv-proxy sidecar.
# Default falls back to the public API for bare-metal / dev use.
_OSV_BATCH_URL = os.environ.get(
    "LLMESH_OSV_URL", "https://api.osv.dev/v1/querybatch"
)
_DEFAULT_TIMEOUT = 10  # seconds

# Map LLMesh language tag → OSV ecosystem
_LANG_ECOSYSTEM: dict[str, str] = {
    "python":     "PyPI",
    "typescript": "npm",
    "go":         "Go",
    "rust":       "crates.io",
    "java":       "Maven",
}

# Infer ecosystem from test framework name (used when language field is absent)
_FRAMEWORK_ECOSYSTEM: dict[str, str] = {
    "pytest":    "PyPI",
    "unittest":  "PyPI",
    "nose":      "PyPI",
    "nose2":     "PyPI",
    "jest":      "npm",
    "mocha":     "npm",
    "vitest":    "npm",
    "jasmine":   "npm",
    "karma":     "npm",
    "junit":     "Maven",
    "testng":    "Maven",
    "cargo":     "crates.io",
    "go test":   "Go",
}

# Severity labels considered blocking
BLOCKING_SEVERITIES: frozenset[str] = frozenset({"CRITICAL", "HIGH"})

# Parse "name==version", "name>=version", "name@version", "name:version", "name"
_DEP_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9_\-\.]+)"
    r"(?:[=@:><~^]+(?P<version>[A-Za-z0-9_\-\.]+))?$"
)


class OsvQueryError(Exception):
    """Raised when the OSV API call fails (network / HTTP error)."""


@dataclass(frozen=True)
class CveHit:
    dep: str
    vuln_id: str
    severity: str  # "CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"


def _parse_dep(dep: str) -> tuple[str, str]:
    """Return (name, version) from a dependency string.

    Version is empty string when absent or when the specifier is a range
    (ranges are not pinned, so we query by name only for safety).
    """
    dep = dep.strip()
    m = _DEP_RE.match(dep)
    if not m:
        return dep, ""
    name = m.group("name")
    raw_ver = m.group("version") or ""
    # Accept version only for pinned specifiers (== or @)
    is_pinned = re.match(r"^[A-Za-z0-9_\-\.]+(?:==|@|:)", dep)
    version = raw_ver if is_pinned else ""
    return name, version


def _severity_from_vuln(vuln: dict) -> str:
    """Extract highest severity label from an OSV vulnerability object."""
    # 1. Try database_specific.severity (GitHub-style: "CRITICAL", "HIGH", …)
    db_sev = (vuln.get("database_specific") or {}).get("severity", "")
    if isinstance(db_sev, str) and db_sev.upper() in {"CRITICAL", "HIGH", "MEDIUM", "LOW"}:
        return db_sev.upper()

    # 2. Try severity[] array — CVSS_V3 vector or numeric score
    for entry in vuln.get("severity") or []:
        score_raw = entry.get("score", "")
        try:
            score = float(score_raw)
        except (TypeError, ValueError):
            # score_raw may be a CVSS vector string — extract base score via regex
            m = re.search(r"(\d+\.\d+)$", str(score_raw))
            score = float(m.group(1)) if m else -1.0
        if score >= 9.0:
            return "CRITICAL"
        if score >= 7.0:
            return "HIGH"
        if score >= 4.0:
            return "MEDIUM"
        if score >= 0:
            return "LOW"

    return "UNKNOWN"


def _build_queries(deps: list[str], ecosystem: str) -> list[tuple[str, dict]]:
    """Return list of (dep_str, osv_query_dict) pairs."""
    queries: list[tuple[str, dict]] = []
    for dep in deps:
        name, version = _parse_dep(dep)
        if not name:
            continue
        q: dict = {"package": {"name": name, "ecosystem": ecosystem}}
        if version:
            q["version"] = version
        queries.append((dep, q))
    return queries


def _post_osv(query_dicts: list[dict], timeout: int) -> list[dict]:
    """POST to OSV querybatch and return the 'results' list.

    Raises OsvQueryError on any network / HTTP / JSON failure.
    """
    body = json.dumps({"queries": query_dicts}).encode()
    req = urllib.request.Request(
        _OSV_BATCH_URL,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    from llmesh.security.http_limits import (
        DEFAULT_HTTP_ADAPTER_BYTES,
        ResponseTooLargeError,
        read_capped,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = read_capped(resp, max_bytes=DEFAULT_HTTP_ADAPTER_BYTES)
    except ResponseTooLargeError as exc:
        raise OsvQueryError(f"osv_response_too_large:{exc.cap}") from exc
    except urllib.error.URLError as exc:
        raise OsvQueryError(f"osv_network_error:{exc}") from exc

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OsvQueryError(f"osv_json_error:{exc}") from exc

    results = parsed.get("results")
    if not isinstance(results, list):
        raise OsvQueryError("osv_unexpected_response_shape")
    return results


def check_dependencies(
    deps: list[str],
    language: str = "",
    *,
    framework: str = "",
    timeout: int = _DEFAULT_TIMEOUT,
) -> list[CveHit]:
    """Query OSV for CVEs in *deps*.  Returns a (possibly empty) list of CveHit.

    Raises OsvQueryError on network failure (caller applies fail-open/closed policy).
    Falls back to *framework* name when *language* yields no known ecosystem.
    Skips if neither resolves (e.g. c, cpp).
    """
    if not deps:
        return []

    ecosystem = _LANG_ECOSYSTEM.get(language.lower(), "")
    if not ecosystem and framework:
        ecosystem = _FRAMEWORK_ECOSYSTEM.get(framework.lower().strip(), "")
    if not ecosystem:
        return []  # no ecosystem → skip (c, cpp, unknown)

    pairs = _build_queries(deps, ecosystem)
    if not pairs:
        return []

    dep_strs = [p[0] for p in pairs]
    query_dicts = [p[1] for p in pairs]

    results = _post_osv(query_dicts, timeout)

    hits: list[CveHit] = []
    for dep_str, result in zip(dep_strs, results):
        for vuln in result.get("vulns") or []:
            vuln_id = vuln.get("id", "UNKNOWN")
            severity = _severity_from_vuln(vuln)
            hits.append(CveHit(dep=dep_str, vuln_id=vuln_id, severity=severity))
    return hits
