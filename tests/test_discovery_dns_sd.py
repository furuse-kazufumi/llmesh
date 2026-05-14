"""Tests for DnsSdAnnouncer — DNS-SD v2 mDNS service announcement."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llmesh.discovery.dns_sd import (
    DnsSdAnnouncer,
    DnsSdConfig,
    _capability_hash,
    _build_service_info,
    _SERVICE_TYPE,
    _SCHEMA_VERSION,
)


# ---------------------------------------------------------------------------
# Unit: DnsSdConfig
# ---------------------------------------------------------------------------

class TestDnsSdConfig:
    def test_default_data_levels(self):
        cfg = DnsSdConfig(node_id="n1", did="did:key:z1", host="127.0.0.1", port=8080)
        assert cfg.data_levels_accepted == [0, 1, 2]

    def test_default_ttl(self):
        cfg = DnsSdConfig(node_id="n1", did="did:key:z1", host="127.0.0.1", port=8080)
        assert cfg.ttl == 60

    def test_custom_values(self):
        cfg = DnsSdConfig(
            node_id="n2",
            did="did:key:z2",
            host="192.168.1.1",
            port=9090,
            data_levels_accepted=[0, 1],
            ttl=120,
        )
        assert cfg.port == 9090
        assert cfg.data_levels_accepted == [0, 1]
        assert cfg.ttl == 120


# ---------------------------------------------------------------------------
# Unit: _capability_hash
# ---------------------------------------------------------------------------

class TestCapabilityHash:
    def test_deterministic(self):
        manifest = {"tools": ["echo"], "version": "1"}
        assert _capability_hash(manifest) == _capability_hash(manifest)

    def test_different_manifests_differ(self):
        a = _capability_hash({"tools": ["a"]})
        b = _capability_hash({"tools": ["b"]})
        assert a != b

    def test_key_order_insensitive(self):
        m1 = {"a": 1, "b": 2}
        m2 = {"b": 2, "a": 1}
        assert _capability_hash(m1) == _capability_hash(m2)

    def test_truncated_to_16_chars(self):
        h = _capability_hash({})
        assert len(h) == 16


# ---------------------------------------------------------------------------
# Unit: _build_service_info
# ---------------------------------------------------------------------------

class TestBuildServiceInfo:
    def test_returns_service_info(self):
        from zeroconf import ServiceInfo
        cfg = DnsSdConfig(
            node_id="mynode",
            did="did:key:abc",
            host="127.0.0.1",
            port=8080,
        )
        info = _build_service_info(cfg, _SERVICE_TYPE, 8080)
        assert isinstance(info, ServiceInfo)

    def test_txt_contains_schema_version(self):
        cfg = DnsSdConfig(node_id="n", did="did:key:x", host="127.0.0.1", port=8080)
        info = _build_service_info(cfg, _SERVICE_TYPE, 8080)
        props = info.properties
        assert props.get(b"schema_version") == _SCHEMA_VERSION.encode()

    def test_txt_contains_node_id(self):
        cfg = DnsSdConfig(node_id="mynode", did="did:key:x", host="127.0.0.1", port=8080)
        info = _build_service_info(cfg, _SERVICE_TYPE, 8080)
        assert info.properties.get(b"node_id") == b"mynode"

    def test_txt_contains_did(self):
        cfg = DnsSdConfig(node_id="n", did="did:key:zzz", host="127.0.0.1", port=8080)
        info = _build_service_info(cfg, _SERVICE_TYPE, 8080)
        assert info.properties.get(b"did") == b"did:key:zzz"

    def test_txt_contains_data_levels(self):
        cfg = DnsSdConfig(
            node_id="n", did="d", host="127.0.0.1", port=8080,
            data_levels_accepted=[0, 2],
        )
        info = _build_service_info(cfg, _SERVICE_TYPE, 8080)
        assert info.properties.get(b"data_levels_accepted") == b"0,2"

    def test_capability_hash_in_txt(self):
        manifest = {"tools": ["echo"]}
        cfg = DnsSdConfig(
            node_id="n", did="d", host="127.0.0.1", port=8080,
            capability_manifest=manifest,
        )
        info = _build_service_info(cfg, _SERVICE_TYPE, 8080)
        expected = _capability_hash(manifest).encode()
        assert info.properties.get(b"capability_hash") == expected

    def test_port_set_correctly(self):
        cfg = DnsSdConfig(node_id="n", did="d", host="127.0.0.1", port=8080)
        info = _build_service_info(cfg, _SERVICE_TYPE, 9999)
        assert info.port == 9999


# ---------------------------------------------------------------------------
# Unit: DnsSdAnnouncer — missing zeroconf
# ---------------------------------------------------------------------------

class TestDnsSdAnnouncerImportError:
    def test_raises_on_import_when_unavailable(self, monkeypatch):
        import llmesh.discovery.dns_sd as dns_module
        monkeypatch.setattr(dns_module, "_ZEROCONF_AVAILABLE", False)
        with pytest.raises(ImportError, match="zeroconf"):
            DnsSdAnnouncer(DnsSdConfig(
                node_id="n", did="d", host="127.0.0.1", port=8080
            ))


# ---------------------------------------------------------------------------
# Integration: DnsSdAnnouncer start/stop with mocked zeroconf
# ---------------------------------------------------------------------------

def _make_mock_azc():
    azc = MagicMock()
    azc.async_register_service = AsyncMock()
    azc.async_unregister_service = AsyncMock()
    azc.async_close = AsyncMock()
    return azc


class TestDnsSdAnnouncerLifecycle:
    @pytest.mark.asyncio
    async def test_is_running_after_start(self):
        cfg = DnsSdConfig(node_id="n1", did="did:key:z", host="127.0.0.1", port=8080)
        announcer = DnsSdAnnouncer(cfg)
        mock_azc = _make_mock_azc()
        with patch("llmesh.discovery.dns_sd.AsyncZeroconf", return_value=mock_azc):
            await announcer.start()
            assert announcer.is_running is True
        await announcer.stop()

    @pytest.mark.asyncio
    async def test_not_running_after_stop(self):
        cfg = DnsSdConfig(node_id="n1", did="did:key:z", host="127.0.0.1", port=8080)
        announcer = DnsSdAnnouncer(cfg)
        mock_azc = _make_mock_azc()
        with patch("llmesh.discovery.dns_sd.AsyncZeroconf", return_value=mock_azc):
            await announcer.start()
            await announcer.stop()
        assert announcer.is_running is False

    @pytest.mark.asyncio
    async def test_registers_primary_service(self):
        cfg = DnsSdConfig(node_id="n1", did="did:key:z", host="127.0.0.1", port=8080)
        announcer = DnsSdAnnouncer(cfg)
        mock_azc = _make_mock_azc()
        with patch("llmesh.discovery.dns_sd.AsyncZeroconf", return_value=mock_azc):
            await announcer.start()
            await announcer.stop()
        mock_azc.async_register_service.assert_called()

    @pytest.mark.asyncio
    async def test_extra_protocols_registered(self):
        cfg = DnsSdConfig(
            node_id="n1",
            did="did:key:z",
            host="127.0.0.1",
            port=8080,
            extra_protocols=[
                {"protocol": "ssh", "port": 2222},
                {"protocol": "ftp", "port": 2121},
            ],
        )
        announcer = DnsSdAnnouncer(cfg)
        mock_azc = _make_mock_azc()
        with patch("llmesh.discovery.dns_sd.AsyncZeroconf", return_value=mock_azc):
            await announcer.start()
            await announcer.stop()
        # 3 registrations: primary + ssh + ftp
        assert mock_azc.async_register_service.call_count == 3

    @pytest.mark.asyncio
    async def test_extra_protocol_missing_port_skipped(self):
        cfg = DnsSdConfig(
            node_id="n1",
            did="did:key:z",
            host="127.0.0.1",
            port=8080,
            extra_protocols=[{"protocol": "ssh"}],  # no port
        )
        announcer = DnsSdAnnouncer(cfg)
        mock_azc = _make_mock_azc()
        with patch("llmesh.discovery.dns_sd.AsyncZeroconf", return_value=mock_azc):
            await announcer.start()
            await announcer.stop()
        # only primary registered
        assert mock_azc.async_register_service.call_count == 1

    @pytest.mark.asyncio
    async def test_stop_idempotent(self):
        cfg = DnsSdConfig(node_id="n1", did="d", host="127.0.0.1", port=8080)
        announcer = DnsSdAnnouncer(cfg)
        await announcer.stop()  # must not raise

    @pytest.mark.asyncio
    async def test_update_manifest_rerenders(self):
        cfg = DnsSdConfig(
            node_id="n1", did="d", host="127.0.0.1", port=8080,
            capability_manifest={"tools": ["old"]},
        )
        announcer = DnsSdAnnouncer(cfg)
        mock_azc = _make_mock_azc()
        with patch("llmesh.discovery.dns_sd.AsyncZeroconf", return_value=mock_azc):
            await announcer.start()
            await announcer.update_manifest({"tools": ["new"]})
            await announcer.stop()
        assert cfg.capability_manifest == {"tools": ["new"]}

    @pytest.mark.asyncio
    async def test_unregister_called_on_stop(self):
        cfg = DnsSdConfig(node_id="n1", did="d", host="127.0.0.1", port=8080)
        announcer = DnsSdAnnouncer(cfg)
        mock_azc = _make_mock_azc()
        with patch("llmesh.discovery.dns_sd.AsyncZeroconf", return_value=mock_azc):
            await announcer.start()
            await announcer.stop()
        mock_azc.async_unregister_service.assert_called()
        mock_azc.async_close.assert_called_once()
