"""Tests for LLMeshTomlConfig (v1.0.0 unified TOML configuration)."""
from __future__ import annotations

import textwrap
from pathlib import Path


from llmesh.config.toml_config import (
    AdapterConfig,
    CircuitBreakerConfig,
    LLMeshTomlConfig,
    SecurityConfig,
)


# ---------------------------------------------------------------------------
# AdapterConfig
# ---------------------------------------------------------------------------

class TestAdapterConfig:
    def test_defaults(self):
        cfg = AdapterConfig()
        assert cfg.host == "0.0.0.0"
        assert cfg.port == 0
        assert cfg.enabled is True
        assert cfg.extra == {}

    def test_from_dict_full(self):
        cfg = AdapterConfig.from_dict({"host": "127.0.0.1", "port": 8080, "enabled": False, "tls": True})
        assert cfg.host == "127.0.0.1"
        assert cfg.port == 8080
        assert cfg.enabled is False
        assert cfg.extra == {"tls": True}

    def test_from_dict_minimal(self):
        cfg = AdapterConfig.from_dict({})
        assert cfg.host == "0.0.0.0"
        assert cfg.port == 0
        assert cfg.enabled is True

    def test_from_dict_port_coerced(self):
        cfg = AdapterConfig.from_dict({"port": "2222"})
        assert cfg.port == 2222


# ---------------------------------------------------------------------------
# SecurityConfig
# ---------------------------------------------------------------------------

class TestSecurityConfig:
    def test_defaults(self):
        s = SecurityConfig()
        assert "pool.ntp.org" in s.ntp_servers
        assert s.max_clock_drift_s == 10
        assert s.rate_limit_rate == 10.0
        assert s.rate_limit_burst == 20.0

    def test_from_dict(self):
        s = SecurityConfig.from_dict({
            "ntp_servers": ["ntp.example.com"],
            "max_clock_drift_s": 5,
            "rate_limit_rate": 3.0,
            "rate_limit_burst": 6.0,
        })
        assert s.ntp_servers == ["ntp.example.com"]
        assert s.max_clock_drift_s == 5
        assert s.rate_limit_rate == 3.0


# ---------------------------------------------------------------------------
# CircuitBreakerConfig
# ---------------------------------------------------------------------------

class TestCircuitBreakerConfig:
    def test_defaults(self):
        cb = CircuitBreakerConfig()
        assert cb.failure_threshold == 3
        assert cb.recovery_timeout == 60.0

    def test_from_dict(self):
        cb = CircuitBreakerConfig.from_dict({"failure_threshold": 5, "recovery_timeout": 30.0})
        assert cb.failure_threshold == 5
        assert cb.recovery_timeout == 30.0


# ---------------------------------------------------------------------------
# LLMeshTomlConfig.load — file absent → env-var fallback
# ---------------------------------------------------------------------------

class TestLLMeshTomlConfigEnvFallback:
    def test_returns_defaults_when_no_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("LLMESH_NODE_ID", raising=False)
        monkeypatch.delenv("LLMESH_DATA_LEVEL", raising=False)
        cfg = LLMeshTomlConfig.load()
        assert cfg.node_id == ""
        assert cfg.data_level == 0

    def test_reads_node_id_from_env(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("LLMESH_NODE_ID", "env-node-1")
        cfg = LLMeshTomlConfig.load()
        assert cfg.node_id == "env-node-1"

    def test_reads_data_level_from_env(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("LLMESH_DATA_LEVEL", "2")
        cfg = LLMeshTomlConfig.load()
        assert cfg.data_level == 2


# ---------------------------------------------------------------------------
# LLMeshTomlConfig.load — valid TOML file
# ---------------------------------------------------------------------------

class TestLLMeshTomlConfigLoad:
    def _write_toml(self, path: Path, content: str) -> Path:
        p = path / "llmesh.toml"
        p.write_text(textwrap.dedent(content), encoding="utf-8")
        return p

    def test_loads_node_section(self, tmp_path):
        self._write_toml(tmp_path, """
            [node]
            id = "node-abc"
            data_level = 1
        """)
        cfg = LLMeshTomlConfig.load(tmp_path / "llmesh.toml")
        assert cfg.node_id == "node-abc"
        assert cfg.data_level == 1

    def test_loads_enabled_adapters(self, tmp_path):
        self._write_toml(tmp_path, """
            [adapters]
            enabled = ["http", "tcp"]
        """)
        cfg = LLMeshTomlConfig.load(tmp_path / "llmesh.toml")
        assert cfg.enabled_adapters == ["http", "tcp"]

    def test_loads_adapter_sub_sections(self, tmp_path):
        self._write_toml(tmp_path, """
            [adapters.http]
            host = "127.0.0.1"
            port = 9090
        """)
        cfg = LLMeshTomlConfig.load(tmp_path / "llmesh.toml")
        http = cfg.adapter("http")
        assert http.host == "127.0.0.1"
        assert http.port == 9090

    def test_loads_security_section(self, tmp_path):
        self._write_toml(tmp_path, """
            [security]
            max_clock_drift_s = 5
            rate_limit_rate = 3.0
        """)
        cfg = LLMeshTomlConfig.load(tmp_path / "llmesh.toml")
        assert cfg.security.max_clock_drift_s == 5
        assert cfg.security.rate_limit_rate == 3.0

    def test_loads_circuit_breaker_section(self, tmp_path):
        self._write_toml(tmp_path, """
            [circuit_breaker]
            failure_threshold = 7
            recovery_timeout = 45.0
        """)
        cfg = LLMeshTomlConfig.load(tmp_path / "llmesh.toml")
        assert cfg.circuit_breaker.failure_threshold == 7
        assert cfg.circuit_breaker.recovery_timeout == 45.0

    def test_unknown_keys_preserved_in_extra(self, tmp_path):
        self._write_toml(tmp_path, """
            [custom_plugin]
            foo = "bar"
        """)
        cfg = LLMeshTomlConfig.load(tmp_path / "llmesh.toml")
        assert cfg._extra.get("custom_plugin") == {"foo": "bar"}

    def test_invalid_toml_falls_back_to_defaults(self, tmp_path, monkeypatch):
        p = tmp_path / "llmesh.toml"
        p.write_text("this is not valid toml ===", encoding="utf-8")
        monkeypatch.delenv("LLMESH_NODE_ID", raising=False)
        cfg = LLMeshTomlConfig.load(p)
        assert cfg.node_id == ""


# ---------------------------------------------------------------------------
# LLMeshTomlConfig accessors
# ---------------------------------------------------------------------------

class TestLLMeshTomlConfigAccessors:
    def test_adapter_returns_default_for_unknown(self):
        cfg = LLMeshTomlConfig()
        ac = cfg.adapter("nonexistent")
        assert isinstance(ac, AdapterConfig)
        assert ac.port == 0

    def test_is_adapter_enabled_empty_list_means_all(self):
        cfg = LLMeshTomlConfig(enabled_adapters=[])
        assert cfg.is_adapter_enabled("http") is True
        assert cfg.is_adapter_enabled("grpc") is True

    def test_is_adapter_enabled_explicit_list(self):
        cfg = LLMeshTomlConfig(enabled_adapters=["http", "tcp"])
        assert cfg.is_adapter_enabled("http") is True
        assert cfg.is_adapter_enabled("udp") is False

    def test_to_dict_round_trip(self, tmp_path):
        content = textwrap.dedent("""
            [node]
            id = "round-trip"
            data_level = 0

            [adapters]
            enabled = ["http"]

            [adapters.http]
            host = "0.0.0.0"
            port = 8080

            [security]
            max_clock_drift_s = 10
            rate_limit_rate = 10.0
            rate_limit_burst = 20.0

            [circuit_breaker]
            failure_threshold = 3
            recovery_timeout = 60.0
        """)
        p = tmp_path / "llmesh.toml"
        p.write_text(content, encoding="utf-8")
        cfg = LLMeshTomlConfig.load(p)
        d = cfg.to_dict()
        assert d["node"]["id"] == "round-trip"
        assert d["adapters"]["enabled"] == ["http"]
        assert d["adapters"]["http"]["port"] == 8080
