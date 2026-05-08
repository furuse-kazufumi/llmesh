"""Tests for llmesh.config.industrial_config and LLMeshTomlConfig integration."""
from __future__ import annotations

import pytest

from llmesh.config.industrial_config import (
    IndustrialConfig,
    SUPPORTED_PROTOCOLS,
    SUPPORTED_DEVICE_TYPES,
    SUPPORTED_ANALYSIS_METHODS,
    NETWORK_POLICIES,
)
from llmesh.config.toml_config import LLMeshTomlConfig


class TestIndustrialConfigDefaults:
    def test_empty_defaults(self):
        cfg = IndustrialConfig()
        assert cfg.domain == ""
        assert cfg.device_types == []
        assert cfg.protocols == []
        assert cfg.analysis_methods == []
        assert cfg.network_policy == "local_only"
        assert cfg.data_retention_days == 90
        assert cfg.unit_space_dir == "unit_spaces"

    def test_not_configured_when_empty(self):
        assert not IndustrialConfig().is_configured()

    def test_configured_when_domain_and_protocols_set(self):
        cfg = IndustrialConfig(domain="manufacturing", protocols=["modbus"])
        assert cfg.is_configured()

    def test_not_configured_missing_protocols(self):
        cfg = IndustrialConfig(domain="manufacturing")
        assert not cfg.is_configured()

    def test_not_configured_missing_domain(self):
        cfg = IndustrialConfig(protocols=["modbus"])
        assert not cfg.is_configured()


class TestIndustrialConfigFromDict:
    def test_full_dict(self):
        d = {
            "domain": "manufacturing",
            "device_types": ["smt_machine", "aoi"],
            "protocols": ["modbus", "serial"],
            "analysis_methods": ["mt_method", "spc"],
            "network_policy": "local_only",
            "data_retention_days": 180,
            "unit_space_dir": "/data/unit_spaces",
        }
        cfg = IndustrialConfig.from_dict(d)
        assert cfg.domain == "manufacturing"
        assert cfg.device_types == ["smt_machine", "aoi"]
        assert cfg.protocols == ["modbus", "serial"]
        assert cfg.analysis_methods == ["mt_method", "spc"]
        assert cfg.network_policy == "local_only"
        assert cfg.data_retention_days == 180
        assert cfg.unit_space_dir == "/data/unit_spaces"

    def test_empty_dict_gives_defaults(self):
        cfg = IndustrialConfig.from_dict({})
        assert cfg.domain == ""
        assert cfg.protocols == []
        assert cfg.data_retention_days == 90

    def test_unknown_keys_ignored(self):
        cfg = IndustrialConfig.from_dict({"domain": "manufacturing", "future_key": "ignored"})
        assert cfg.domain == "manufacturing"

    def test_string_coercion(self):
        cfg = IndustrialConfig.from_dict({"data_retention_days": "365"})
        assert cfg.data_retention_days == 365


class TestIndustrialConfigToDict:
    def test_roundtrip(self):
        cfg = IndustrialConfig(
            domain="manufacturing",
            protocols=["modbus", "opcua"],
            device_types=["smt_machine"],
            analysis_methods=["mt_method"],
            network_policy="local_only",
            data_retention_days=60,
            unit_space_dir="spaces/",
        )
        d = cfg.to_dict()
        cfg2 = IndustrialConfig.from_dict(d)
        assert cfg2.domain == cfg.domain
        assert cfg2.protocols == cfg.protocols
        assert cfg2.data_retention_days == cfg.data_retention_days


class TestIndustrialConfigAccessors:
    def test_uses_protocol(self):
        cfg = IndustrialConfig(protocols=["modbus", "serial"])
        assert cfg.uses_protocol("modbus")
        assert not cfg.uses_protocol("mqtt")

    def test_uses_analysis(self):
        cfg = IndustrialConfig(analysis_methods=["mt_method"])
        assert cfg.uses_analysis("mt_method")
        assert not cfg.uses_analysis("spc")


class TestVocabularySets:
    def test_modbus_in_protocols(self):
        assert "modbus" in SUPPORTED_PROTOCOLS

    def test_smt_machine_in_devices(self):
        assert "smt_machine" in SUPPORTED_DEVICE_TYPES

    def test_aoi_in_devices(self):
        assert "aoi" in SUPPORTED_DEVICE_TYPES

    def test_mt_method_in_analysis(self):
        assert "mt_method" in SUPPORTED_ANALYSIS_METHODS

    def test_local_only_in_network_policies(self):
        assert "local_only" in NETWORK_POLICIES

    def test_mcp3d_in_protocols(self):
        assert "mcp3d" in SUPPORTED_PROTOCOLS


class TestTomlConfigIndustrialIntegration:
    def test_load_defaults_has_industrial(self):
        cfg = LLMeshTomlConfig()
        assert isinstance(cfg.industrial, IndustrialConfig)
        assert not cfg.industrial.is_configured()

    def test_from_dict_with_industrial_section(self):
        raw = {
            "node": {"id": "node-01", "data_level": 0},
            "adapters": {},
            "industrial": {
                "domain": "manufacturing",
                "protocols": ["modbus"],
                "device_types": ["smt_machine"],
                "analysis_methods": ["mt_method"],
            },
        }
        cfg = LLMeshTomlConfig._from_dict(raw)
        assert cfg.industrial.domain == "manufacturing"
        assert cfg.industrial.protocols == ["modbus"]
        assert cfg.industrial.is_configured()

    def test_to_dict_omits_industrial_when_empty(self):
        cfg = LLMeshTomlConfig()
        d = cfg.to_dict()
        assert "industrial" not in d

    def test_to_dict_includes_industrial_when_configured(self):
        import dataclasses
        from llmesh.config.industrial_config import IndustrialConfig
        base = LLMeshTomlConfig()
        ind = IndustrialConfig(domain="manufacturing", protocols=["modbus"])
        cfg = dataclasses.replace(base, industrial=ind)
        d = cfg.to_dict()
        assert "industrial" in d
        assert d["industrial"]["domain"] == "manufacturing"

    def test_from_file_missing_returns_defaults(self, tmp_path):
        cfg = LLMeshTomlConfig.load(tmp_path / "nonexistent.toml")
        assert not cfg.industrial.is_configured()

    def test_from_file_with_industrial_section(self, tmp_path):
        toml_file = tmp_path / "llmesh.toml"
        toml_file.write_text(
            '[industrial]\ndomain = "manufacturing"\nprotocols = ["modbus"]\n',
            encoding="utf-8",
        )
        cfg = LLMeshTomlConfig.load(toml_file)
        assert cfg.industrial.domain == "manufacturing"
        assert cfg.industrial.uses_protocol("modbus")
