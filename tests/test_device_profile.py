"""Tests for llmesh.protocol.device_profile — DeviceProfile / NANO profile."""
from __future__ import annotations

import os

import pytest

from llmesh.protocol.device_profile import (
    DeviceProfile,
    PayloadTooLargeError,
    ProfileType,
    ProtocolNotAllowedError,
)


# ---------------------------------------------------------------------------
# FULL profile
# ---------------------------------------------------------------------------

class TestFullProfile:
    def test_default_is_full(self) -> None:
        p = DeviceProfile()
        assert p.profile_type == ProfileType.FULL
        assert p.is_nano is False

    def test_full_factory(self) -> None:
        p = DeviceProfile.full()
        assert p.profile_type == ProfileType.FULL

    def test_full_no_payload_limit(self) -> None:
        p = DeviceProfile.full()
        assert p.max_payload is None
        p.check_payload(10 * 1024 * 1024)  # 10 MiB — must not raise

    def test_full_all_protocols_allowed(self) -> None:
        p = DeviceProfile.full()
        for proto in ("http", "tcp", "tcp_stream", "udp"):
            p.check_protocol(proto)  # must not raise

    def test_full_signing_always_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLMESH_NANO_NO_CRYPTO", "1")
        p = DeviceProfile.full()
        assert p.signing_enabled is True
        assert p.no_crypto is False


# ---------------------------------------------------------------------------
# NANO profile
# ---------------------------------------------------------------------------

class TestNanoProfile:
    def test_nano_factory(self) -> None:
        p = DeviceProfile.nano()
        assert p.profile_type == ProfileType.NANO
        assert p.is_nano is True

    def test_nano_payload_limit_1kb(self) -> None:
        p = DeviceProfile.nano()
        assert p.max_payload == 1024

    def test_nano_payload_within_limit_ok(self) -> None:
        p = DeviceProfile.nano()
        p.check_payload(1024)  # exact limit — must not raise

    def test_nano_payload_exceeds_limit_raises(self) -> None:
        p = DeviceProfile.nano()
        with pytest.raises(PayloadTooLargeError, match="1025"):
            p.check_payload(1025)

    def test_nano_only_udp_allowed(self) -> None:
        p = DeviceProfile.nano()
        p.check_protocol("udp")  # must not raise

    def test_nano_tcp_not_allowed(self) -> None:
        p = DeviceProfile.nano()
        with pytest.raises(ProtocolNotAllowedError, match="tcp"):
            p.check_protocol("tcp")

    def test_nano_tcp_stream_not_allowed(self) -> None:
        p = DeviceProfile.nano()
        with pytest.raises(ProtocolNotAllowedError):
            p.check_protocol("tcp_stream")

    def test_nano_http_not_allowed(self) -> None:
        p = DeviceProfile.nano()
        with pytest.raises(ProtocolNotAllowedError):
            p.check_protocol("http")

    def test_nano_signing_enabled_by_default(self) -> None:
        p = DeviceProfile.nano(no_crypto=False)
        assert p.signing_enabled is True

    def test_nano_no_crypto_disables_signing(self) -> None:
        p = DeviceProfile.nano(no_crypto=True)
        assert p.signing_enabled is False
        assert p.no_crypto is True

    def test_nano_no_crypto_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLMESH_NANO_NO_CRYPTO", "1")
        p = DeviceProfile.nano()
        assert p.no_crypto is True
        assert p.signing_enabled is False

    def test_nano_no_crypto_env_absent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("LLMESH_NANO_NO_CRYPTO", raising=False)
        p = DeviceProfile.nano()
        assert p.signing_enabled is True

    def test_nano_explicit_no_crypto_overrides_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLMESH_NANO_NO_CRYPTO", "1")
        p = DeviceProfile.nano(no_crypto=False)
        assert p.signing_enabled is True


# ---------------------------------------------------------------------------
# check_payload / check_protocol edge cases
# ---------------------------------------------------------------------------

class TestGuardHelpers:
    def test_check_payload_zero_always_ok(self) -> None:
        DeviceProfile.nano().check_payload(0)

    def test_check_protocol_unknown_raises_for_nano(self) -> None:
        with pytest.raises(ProtocolNotAllowedError):
            DeviceProfile.nano().check_protocol("grpc")

    def test_check_protocol_unknown_raises_for_full(self) -> None:
        with pytest.raises(ProtocolNotAllowedError):
            DeviceProfile.full().check_protocol("grpc")
