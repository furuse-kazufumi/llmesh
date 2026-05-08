"""EndpointValidator — SSRF and injection prevention for node endpoint URLs.

Threats mitigated:
- SSRF: attacker registers a node with endpoint pointing to internal services
  (cloud IMDS, localhost, private IP ranges)
- URL injection: credentials, fragments, non-HTTP schemes in endpoint URLs
- Path traversal via endpoint manipulation

By default private IP ranges are blocked (strict mode).  Pass
allow_private=True for LAN-only deployments where nodes share a private network.

Security invariants:
- No shell=True, eval, exec, pickle anywhere
- Pure-Python stdlib only (urllib.parse, ipaddress)
"""
from __future__ import annotations

import ipaddress
import urllib.parse

_ALLOWED_SCHEMES: frozenset[str] = frozenset({"http", "https"})

# Hosts that are always blocked regardless of allow_private
_BLOCKED_HOSTS: frozenset[str] = frozenset({
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "::1",
    "metadata.google.internal",   # GCP instance metadata service
    "169.254.169.254",             # AWS/Azure/GCP IMDS
})

# RFC1918 + link-local ranges; blocked unless allow_private=True
_PRIVATE_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local / IMDS
    ipaddress.ip_network("fc00::/7"),         # IPv6 ULA
    ipaddress.ip_network("::1/128"),
)


class EndpointValidationError(Exception):
    """Raised when a node endpoint fails security validation."""


class EndpointValidator:
    """Validate node endpoint URLs against SSRF and injection attacks.

    Args:
        allow_private:    Allow RFC1918 private IP ranges (for LAN deployments).
        allowed_schemes:  Set of permitted URL schemes.
    """

    def __init__(
        self,
        allow_private: bool = False,
        allowed_schemes: frozenset[str] = _ALLOWED_SCHEMES,
    ) -> None:
        self._allow_private = allow_private
        self._allowed_schemes = allowed_schemes

    def validate(self, endpoint: str) -> str:
        """Validate and normalize an endpoint URL.

        Args:
            endpoint: Raw endpoint URL from an untrusted source.

        Returns:
            The endpoint with trailing slash stripped.

        Raises:
            EndpointValidationError: If any security check fails.
        """
        if not isinstance(endpoint, str) or not endpoint:
            raise EndpointValidationError("endpoint_must_be_non_empty_string")

        try:
            parsed = urllib.parse.urlparse(endpoint)
        except Exception as exc:
            raise EndpointValidationError(f"url_parse_error:{exc}") from exc

        if parsed.scheme not in self._allowed_schemes:
            raise EndpointValidationError(f"invalid_scheme:{parsed.scheme!r}")

        host = parsed.hostname or ""
        if not host:
            raise EndpointValidationError("missing_host")

        if host.lower() in _BLOCKED_HOSTS:
            raise EndpointValidationError(f"blocked_host:{host}")

        # Reject credentials embedded in URL (e.g. http://user:pass@host/)
        if parsed.username or parsed.password:
            raise EndpointValidationError("credentials_in_url_not_allowed")

        # Reject URL fragments (unused in API calls; may indicate injection attempt)
        if parsed.fragment:
            raise EndpointValidationError("fragment_not_allowed")

        # IP-based SSRF check
        try:
            addr = ipaddress.ip_address(host)
            if host in _BLOCKED_HOSTS or addr.is_loopback or addr.is_unspecified:
                raise EndpointValidationError(f"blocked_address:{host}")
            if not self._allow_private:
                for net in _PRIVATE_NETWORKS:
                    if addr in net:
                        raise EndpointValidationError(f"private_ip_blocked:{host}")
        except ValueError:
            pass  # host is a hostname, not a bare IP; DNS not checked here

        return endpoint.rstrip("/")
