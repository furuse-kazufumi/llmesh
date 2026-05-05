"""Tests for llmesh.security.endpoint_validator — EndpointValidator."""
from __future__ import annotations

import pytest

from llmesh.security.endpoint_validator import EndpointValidator, EndpointValidationError


class TestEndpointValidatorAccepted:
    def setup_method(self):
        self.v = EndpointValidator(allow_private=True)   # allow_private for clean URL tests

    def test_http_public_ip(self):
        result = self.v.validate("http://203.0.113.5:8080")
        assert result == "http://203.0.113.5:8080"

    def test_https_hostname(self):
        result = self.v.validate("https://node.example.com:8443/")
        assert result == "https://node.example.com:8443"   # trailing slash stripped

    def test_trailing_slash_stripped(self):
        result = self.v.validate("http://10.0.0.1:9000/")
        assert result == "http://10.0.0.1:9000"

    def test_private_ip_allowed_when_flag_set(self):
        result = self.v.validate("http://192.168.1.50:8080")
        assert result == "http://192.168.1.50:8080"


class TestEndpointValidatorBlocked:
    def setup_method(self):
        self.v = EndpointValidator(allow_private=False)

    def test_empty_string_raises(self):
        with pytest.raises(EndpointValidationError, match="endpoint_must_be_non_empty_string"):
            self.v.validate("")

    def test_none_raises(self):
        with pytest.raises(EndpointValidationError):
            self.v.validate(None)  # type: ignore[arg-type]

    def test_invalid_scheme_ftp(self):
        with pytest.raises(EndpointValidationError, match="invalid_scheme"):
            self.v.validate("ftp://node.example.com")

    def test_invalid_scheme_file(self):
        with pytest.raises(EndpointValidationError, match="invalid_scheme"):
            self.v.validate("file:///etc/passwd")

    def test_localhost_blocked(self):
        with pytest.raises(EndpointValidationError, match="blocked_host"):
            self.v.validate("http://localhost:8080")

    def test_loopback_ip_blocked(self):
        with pytest.raises(EndpointValidationError):
            self.v.validate("http://127.0.0.1:8080")

    def test_all_zeros_blocked(self):
        with pytest.raises(EndpointValidationError):
            self.v.validate("http://0.0.0.0:8080")

    def test_cloud_imds_blocked(self):
        with pytest.raises(EndpointValidationError):
            self.v.validate("http://169.254.169.254/latest/meta-data/")

    def test_private_ip_blocked_by_default(self):
        with pytest.raises(EndpointValidationError, match="private_ip_blocked"):
            self.v.validate("http://192.168.1.50:8080")

    def test_private_10_block_blocked(self):
        with pytest.raises(EndpointValidationError, match="private_ip_blocked"):
            self.v.validate("http://10.0.0.1:8080")

    def test_private_172_block_blocked(self):
        with pytest.raises(EndpointValidationError, match="private_ip_blocked"):
            self.v.validate("http://172.16.0.1:8080")

    def test_credentials_in_url_blocked(self):
        with pytest.raises(EndpointValidationError, match="credentials_in_url"):
            self.v.validate("http://user:pass@203.0.113.5:8080")

    def test_fragment_blocked(self):
        with pytest.raises(EndpointValidationError, match="fragment_not_allowed"):
            self.v.validate("http://203.0.113.5:8080#section")

    def test_gcp_metadata_blocked(self):
        with pytest.raises(EndpointValidationError, match="blocked_host"):
            self.v.validate("http://metadata.google.internal/computeMetadata/v1/")


class TestEndpointValidatorCustomSchemes:
    def test_only_https_allowed(self):
        v = EndpointValidator(allowed_schemes=frozenset({"https"}))
        with pytest.raises(EndpointValidationError, match="invalid_scheme"):
            v.validate("http://203.0.113.5:8080")
        result = v.validate("https://203.0.113.5:8443")
        assert "https" in result
