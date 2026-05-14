"""Tests for llmesh.security.clock — NTP clock-sync enforcement."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from llmesh.security.clock import ClockDriftError, check_clock_sync


# ---------------------------------------------------------------------------
# ClockDriftError
# ---------------------------------------------------------------------------

class TestClockDriftError:
    def test_message_contains_drift(self):
        err = ClockDriftError(15.3, 10.0)
        assert "15.30" in str(err)
        assert "10.00" in str(err)

    def test_attributes(self):
        err = ClockDriftError(5.0, 3.0)
        assert err.drift == 5.0
        assert err.max_drift == 3.0

    def test_is_runtime_error(self):
        assert isinstance(ClockDriftError(1.0, 0.5), RuntimeError)


# ---------------------------------------------------------------------------
# check_clock_sync — happy path
# ---------------------------------------------------------------------------

class TestCheckClockSyncHappy:
    def _mock_response(self, offset: float) -> MagicMock:
        resp = MagicMock()
        resp.offset = offset
        return resp

    def test_returns_drift_on_success(self):
        with patch("ntplib.NTPClient") as mock_cls:
            mock_cls.return_value.request.return_value = self._mock_response(0.5)
            drift = check_clock_sync(servers=["ntp.example"], max_drift_s=10.0, timeout_s=1.0)
        assert abs(drift - 0.5) < 1e-6

    def test_negative_offset_is_absolute(self):
        with patch("ntplib.NTPClient") as mock_cls:
            mock_cls.return_value.request.return_value = self._mock_response(-3.0)
            drift = check_clock_sync(servers=["ntp.example"], max_drift_s=10.0, timeout_s=1.0)
        assert abs(drift - 3.0) < 1e-6

    def test_zero_drift_ok(self):
        with patch("ntplib.NTPClient") as mock_cls:
            mock_cls.return_value.request.return_value = self._mock_response(0.0)
            drift = check_clock_sync(servers=["ntp.example"], max_drift_s=10.0, timeout_s=1.0)
        assert drift == 0.0

    def test_drift_exactly_at_threshold_ok(self):
        with patch("ntplib.NTPClient") as mock_cls:
            mock_cls.return_value.request.return_value = self._mock_response(10.0)
            # 10.0 == 10.0 threshold → NOT exceeded (strict >)
            drift = check_clock_sync(servers=["ntp.example"], max_drift_s=10.0, timeout_s=1.0)
        assert abs(drift - 10.0) < 1e-6


# ---------------------------------------------------------------------------
# check_clock_sync — error cases
# ---------------------------------------------------------------------------

class TestCheckClockSyncErrors:
    def _mock_response(self, offset: float) -> MagicMock:
        resp = MagicMock()
        resp.offset = offset
        return resp

    def test_raises_clock_drift_error_when_exceeded(self):
        with patch("ntplib.NTPClient") as mock_cls:
            mock_cls.return_value.request.return_value = self._mock_response(20.0)
            with pytest.raises(ClockDriftError) as exc_info:
                check_clock_sync(servers=["ntp.example"], max_drift_s=10.0, timeout_s=1.0)
        assert exc_info.value.drift == 20.0
        assert exc_info.value.max_drift == 10.0

    def test_raises_runtime_error_when_all_unreachable(self):
        with patch("ntplib.NTPClient") as mock_cls:
            mock_cls.return_value.request.side_effect = OSError("timeout")
            with pytest.raises(RuntimeError, match="all servers unreachable"):
                check_clock_sync(servers=["a.example", "b.example"], max_drift_s=10.0, timeout_s=1.0)

    def test_falls_back_to_second_server(self):
        responses = [OSError("bad"), self._mock_response(1.0)]

        def _side_effect(server, **_kw):
            return responses.pop(0)

        with patch("ntplib.NTPClient") as mock_cls:
            mock_cls.return_value.request.side_effect = _side_effect
            drift = check_clock_sync(
                servers=["bad.example", "good.example"],
                max_drift_s=10.0,
                timeout_s=1.0,
            )
        assert abs(drift - 1.0) < 1e-6

    def test_skips_empty_server_entries(self):
        with patch("ntplib.NTPClient") as mock_cls:
            mock_cls.return_value.request.return_value = self._mock_response(0.1)
            drift = check_clock_sync(
                servers=["", "  ", "ntp.example"],
                max_drift_s=10.0,
                timeout_s=1.0,
            )
        assert abs(drift - 0.1) < 1e-6
        mock_cls.return_value.request.assert_called_once()

    def test_raises_import_error_when_ntplib_missing(self):
        import sys
        original = sys.modules.get("ntplib")
        sys.modules["ntplib"] = None  # type: ignore[assignment]
        try:
            with pytest.raises(ImportError, match="ntplib"):
                check_clock_sync(servers=["x"], max_drift_s=5.0, timeout_s=1.0)
        finally:
            if original is None:
                sys.modules.pop("ntplib", None)
            else:
                sys.modules["ntplib"] = original


# ---------------------------------------------------------------------------
# check_clock_sync — env var defaults
# ---------------------------------------------------------------------------

class TestCheckClockSyncEnvVars:
    def _mock_response(self, offset: float) -> MagicMock:
        resp = MagicMock()
        resp.offset = offset
        return resp

    def test_env_max_drift_used(self, monkeypatch):
        monkeypatch.setenv("LLMESH_MAX_CLOCK_DRIFT_S", "2")
        monkeypatch.setenv("LLMESH_NTP_SERVERS", "ntp.example")
        with patch("ntplib.NTPClient") as mock_cls:
            mock_cls.return_value.request.return_value = self._mock_response(3.0)
            with pytest.raises(ClockDriftError) as exc_info:
                check_clock_sync(timeout_s=1.0)
        assert exc_info.value.max_drift == 2.0

    def test_explicit_params_override_env(self, monkeypatch):
        monkeypatch.setenv("LLMESH_MAX_CLOCK_DRIFT_S", "1")
        with patch("ntplib.NTPClient") as mock_cls:
            mock_cls.return_value.request.return_value = self._mock_response(0.5)
            drift = check_clock_sync(
                servers=["ntp.example"], max_drift_s=10.0, timeout_s=1.0
            )
        assert abs(drift - 0.5) < 1e-6
