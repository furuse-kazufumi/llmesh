"""Tests for sca_gate and OutputValidator SCA integration (step 7)."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from llmesh.mcp import OutputValidator, ValidationError
from llmesh.mcp.sca_gate import (
    CveHit,
    OsvQueryError,
    _parse_dep,
    _severity_from_vuln,
    check_dependencies,
)

NONCE = "a" * 32
VALID_TASK_ID = "12345678-1234-4234-89ab-123456789abc"

# ---------------------------------------------------------------------------
# _parse_dep
# ---------------------------------------------------------------------------

class TestParseDep:
    def test_pinned_equals(self):
        assert _parse_dep("requests==2.28.0") == ("requests", "2.28.0")

    def test_pinned_at(self):
        assert _parse_dep("lodash@4.17.21") == ("lodash", "4.17.21")

    def test_pinned_colon(self):
        assert _parse_dep("serde:1.0.130") == ("serde", "1.0.130")

    def test_range_spec_drops_version(self):
        name, ver = _parse_dep("requests>=2.28.0")
        assert name == "requests"
        assert ver == ""

    def test_name_only(self):
        assert _parse_dep("requests") == ("requests", "")

    def test_strips_whitespace(self):
        name, _ = _parse_dep("  requests==2.0.0  ")
        assert name == "requests"


# ---------------------------------------------------------------------------
# _severity_from_vuln
# ---------------------------------------------------------------------------

class TestSeverityFromVuln:
    def test_database_specific_critical(self):
        v = {"database_specific": {"severity": "CRITICAL"}}
        assert _severity_from_vuln(v) == "CRITICAL"

    def test_database_specific_high(self):
        v = {"database_specific": {"severity": "HIGH"}}
        assert _severity_from_vuln(v) == "HIGH"

    def test_cvss_score_critical(self):
        v = {"severity": [{"type": "CVSS_V3", "score": "9.8"}]}
        assert _severity_from_vuln(v) == "CRITICAL"

    def test_cvss_score_high(self):
        v = {"severity": [{"type": "CVSS_V3", "score": "7.5"}]}
        assert _severity_from_vuln(v) == "HIGH"

    def test_cvss_score_medium(self):
        v = {"severity": [{"type": "CVSS_V3", "score": "5.3"}]}
        assert _severity_from_vuln(v) == "MEDIUM"

    def test_cvss_score_low(self):
        v = {"severity": [{"type": "CVSS_V3", "score": "2.0"}]}
        assert _severity_from_vuln(v) == "LOW"

    def test_no_severity_returns_unknown(self):
        assert _severity_from_vuln({}) == "UNKNOWN"

    def test_database_specific_takes_precedence(self):
        # database_specific wins over severity array
        v = {
            "database_specific": {"severity": "LOW"},
            "severity": [{"type": "CVSS_V3", "score": "9.8"}],
        }
        assert _severity_from_vuln(v) == "LOW"


# ---------------------------------------------------------------------------
# check_dependencies — happy path (mocked OSV)
# ---------------------------------------------------------------------------

def _osv_response(results: list[dict]) -> bytes:
    return json.dumps({"results": results}).encode()


class TestCheckDependencies:
    def test_empty_deps_returns_empty(self):
        hits = check_dependencies([], "python")
        assert hits == []

    def test_unknown_language_returns_empty(self):
        # c/cpp have no OSV ecosystem → skip
        hits = check_dependencies(["openssl==3.0.0"], "c")
        assert hits == []

    def test_clean_dep_returns_empty(self):
        clean = _osv_response([{"vulns": []}])
        with patch("llmesh.mcp.sca_gate._post_osv", return_value=[{"vulns": []}]):
            hits = check_dependencies(["requests==2.31.0"], "python")
        assert hits == []

    def test_vulnerable_dep_returns_hit(self):
        vuln = {
            "id": "GHSA-xxxx-yyyy-zzzz",
            "database_specific": {"severity": "CRITICAL"},
        }
        with patch("llmesh.mcp.sca_gate._post_osv", return_value=[{"vulns": [vuln]}]):
            hits = check_dependencies(["requests==2.28.0"], "python")
        assert len(hits) == 1
        assert hits[0].vuln_id == "GHSA-xxxx-yyyy-zzzz"
        assert hits[0].severity == "CRITICAL"
        assert hits[0].dep == "requests==2.28.0"

    def test_multiple_deps_multiple_hits(self):
        vuln_a = {"id": "CVE-2023-0001", "database_specific": {"severity": "HIGH"}}
        vuln_b = {"id": "CVE-2023-0002", "database_specific": {"severity": "MEDIUM"}}
        with patch(
            "llmesh.mcp.sca_gate._post_osv",
            return_value=[{"vulns": [vuln_a]}, {"vulns": [vuln_b]}],
        ):
            hits = check_dependencies(["pkgA==1.0", "pkgB==2.0"], "python")
        assert len(hits) == 2
        severities = {h.severity for h in hits}
        assert severities == {"HIGH", "MEDIUM"}

    def test_network_error_raises_osv_query_error(self):
        with patch(
            "llmesh.mcp.sca_gate._post_osv",
            side_effect=OsvQueryError("connection refused"),
        ):
            with pytest.raises(OsvQueryError):
                check_dependencies(["requests==2.28.0"], "python")

    def test_npm_ecosystem(self):
        vuln = {"id": "GHSA-npm-1234", "database_specific": {"severity": "HIGH"}}
        with patch("llmesh.mcp.sca_gate._post_osv", return_value=[{"vulns": [vuln]}]) as mock_post:
            check_dependencies(["lodash@4.17.20"], "typescript")
            call_args = mock_post.call_args[0][0]
            assert call_args[0]["package"]["ecosystem"] == "npm"

    def test_range_spec_omits_version_in_query(self):
        with patch("llmesh.mcp.sca_gate._post_osv", return_value=[{"vulns": []}]) as mock_post:
            check_dependencies(["requests>=2.0.0"], "python")
            call_args = mock_post.call_args[0][0]
            assert "version" not in call_args[0]


# ---------------------------------------------------------------------------
# OutputValidator SCA integration (step 7)
# ---------------------------------------------------------------------------

def _base_generate_code(deps: list[str] = None, language: str = "python") -> dict:
    return {
        "task_id": VALID_TASK_ID,
        "code": "pass",
        "language": language,
        "explanation": "ok",
        "dependencies_added": deps if deps is not None else [],
        "generated_files": [],
        "cve_scan_requested": False,
        "caller_nonce_echo": NONCE,
    }


def _base_generate_tests(deps: list[str] = None) -> dict:
    return {
        "task_id": VALID_TASK_ID,
        "tests_code": "def test_it(): pass",
        "test_framework": "pytest",
        "test_count": 1,
        "dependencies_added": deps if deps is not None else [],
        "generated_files": [],
        "caller_nonce_echo": NONCE,
    }


class TestValidatorScaGate:
    V = OutputValidator()

    def test_no_deps_skips_sca(self):
        """Empty dependencies_added must not call OSV."""
        with patch("llmesh.mcp.sca_gate._post_osv") as mock_post:
            self.V.validate(json.dumps(_base_generate_code([])), "generate_code", NONCE)
        mock_post.assert_not_called()

    def test_clean_dep_passes(self):
        with patch("llmesh.mcp.sca_gate._post_osv", return_value=[{"vulns": []}]):
            result = self.V.validate(
                json.dumps(_base_generate_code(["requests==2.31.0"])), "generate_code", NONCE
            )
        assert result["language"] == "python"

    def test_critical_dep_blocked(self):
        vuln = {"id": "GHSA-xxxx", "database_specific": {"severity": "CRITICAL"}}
        with patch("llmesh.mcp.sca_gate._post_osv", return_value=[{"vulns": [vuln]}]):
            with pytest.raises(ValidationError, match="sca_blocked"):
                self.V.validate(
                    json.dumps(_base_generate_code(["evil-pkg==1.0"])), "generate_code", NONCE
                )

    def test_high_dep_blocked(self):
        vuln = {"id": "CVE-2023-9999", "database_specific": {"severity": "HIGH"}}
        with patch("llmesh.mcp.sca_gate._post_osv", return_value=[{"vulns": [vuln]}]):
            with pytest.raises(ValidationError, match="sca_blocked"):
                self.V.validate(
                    json.dumps(_base_generate_code(["bad-pkg==2.0"])), "generate_code", NONCE
                )

    def test_medium_dep_passes(self):
        """MEDIUM severity must not block."""
        vuln = {"id": "CVE-2023-0001", "database_specific": {"severity": "MEDIUM"}}
        with patch("llmesh.mcp.sca_gate._post_osv", return_value=[{"vulns": [vuln]}]):
            result = self.V.validate(
                json.dumps(_base_generate_code(["meh-pkg==1.0"])), "generate_code", NONCE
            )
        assert result is not None

    def test_network_error_raises_validation_error(self):
        with patch(
            "llmesh.mcp.sca_gate._post_osv",
            side_effect=OsvQueryError("timeout"),
        ):
            with pytest.raises(ValidationError, match="sca_network_error"):
                self.V.validate(
                    json.dumps(_base_generate_code(["some-pkg==1.0"])), "generate_code", NONCE
                )

    def test_generate_tests_critical_dep_blocked(self):
        """SCA gate also fires for generate_tests tool."""
        vuln = {"id": "GHSA-test", "database_specific": {"severity": "CRITICAL"}}
        with patch("llmesh.mcp.sca_gate._post_osv", return_value=[{"vulns": [vuln]}]):
            with pytest.raises(ValidationError, match="sca_blocked"):
                self.V.validate(
                    json.dumps(_base_generate_tests(["evil-test-pkg==9.9"])),
                    "generate_tests",
                    NONCE,
                )

    def test_generate_tests_no_deps_skips_sca(self):
        with patch("llmesh.mcp.sca_gate._post_osv") as mock_post:
            self.V.validate(json.dumps(_base_generate_tests([])), "generate_tests", NONCE)
        mock_post.assert_not_called()

    def test_c_language_skips_sca(self):
        """C/C++ has no OSV ecosystem — SCA must be skipped entirely."""
        payload = _base_generate_code(["openssl==3.0.0"], language="c")
        with patch("llmesh.mcp.sca_gate._post_osv") as mock_post:
            self.V.validate(json.dumps(payload), "generate_code", NONCE)
        mock_post.assert_not_called()

    def test_error_message_includes_vuln_id_and_severity(self):
        vuln = {"id": "GHSA-detail-check", "database_specific": {"severity": "HIGH"}}
        with patch("llmesh.mcp.sca_gate._post_osv", return_value=[{"vulns": [vuln]}]):
            with pytest.raises(ValidationError) as exc_info:
                self.V.validate(
                    json.dumps(_base_generate_code(["pkg==1.0"])), "generate_code", NONCE
                )
        msg = str(exc_info.value)
        assert "GHSA-detail-check" in msg
        assert "HIGH" in msg


class TestOsvProxyEnvVar:
    """LLMESH_OSV_URL env var routes SCA Gate through the osv-proxy sidecar."""

    def test_default_url_is_public_osv(self):
        import importlib
        import llmesh.mcp.sca_gate as gate
        # Without env var override the default must point to the public API
        importlib.reload(gate)
        assert "api.osv.dev" in gate._OSV_BATCH_URL

    def test_env_var_overrides_url(self, monkeypatch):
        import importlib
        import llmesh.mcp.sca_gate as gate
        monkeypatch.setenv("LLMESH_OSV_URL", "http://osv-proxy:8080/v1/querybatch")
        importlib.reload(gate)
        assert gate._OSV_BATCH_URL == "http://osv-proxy:8080/v1/querybatch"
        # cleanup: reload without the env var so other tests are not affected
        monkeypatch.delenv("LLMESH_OSV_URL", raising=False)
        importlib.reload(gate)
