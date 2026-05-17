"""Tests for llmesh.llm.cn_llm presets."""
from __future__ import annotations

import pytest

from llmesh.llm.cn_llm import (
    PRESETS,
    CNProviderPreset,
    build_backend,
    get_preset,
)


def test_known_providers_present() -> None:
    """Six main Chinese LLM providers are bundled."""
    assert set(PRESETS) == {"qwen", "deepseek", "glm", "kimi", "baichuan", "yi"}


def test_preset_fields_populated() -> None:
    for code, preset in PRESETS.items():
        assert isinstance(preset, CNProviderPreset)
        assert preset.code == code
        assert preset.display_name
        assert preset.base_url.startswith("https://")
        assert preset.default_model
        assert preset.api_key_env  # env var name reserved


def test_qwen_uses_dashscope_url() -> None:
    p = get_preset("qwen")
    assert "dashscope.aliyuncs.com" in p.base_url
    assert p.api_key_env == "DASHSCOPE_API_KEY"


def test_glm_uses_bigmodel_url() -> None:
    p = get_preset("glm")
    assert "bigmodel.cn" in p.base_url
    assert p.api_key_env == "ZHIPUAI_API_KEY"


def test_deepseek_uses_v1_endpoint() -> None:
    p = get_preset("deepseek")
    assert p.base_url == "https://api.deepseek.com/v1"


def test_get_preset_is_case_insensitive() -> None:
    assert get_preset("Qwen") is get_preset("qwen")
    assert get_preset("GLM") is get_preset("glm")


def test_get_preset_unknown_raises() -> None:
    with pytest.raises(KeyError, match="unknown CN LLM provider"):
        get_preset("not-a-real-provider")


def test_build_backend_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """build_backend errors out when no key is provided / set in env."""
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    with pytest.raises(ValueError, match="api_key required"):
        build_backend("qwen")


def test_build_backend_picks_up_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """env var is honoured when explicit api_key is omitted."""
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key-not-real")
    backend = build_backend("qwen")
    # Don't assert internal attrs aggressively — just confirm construction.
    assert backend is not None


def test_build_backend_explicit_args_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "env-key")
    backend = build_backend(
        "qwen",
        api_key="explicit-key",
        model="qwen2.5-7b-instruct",
        base_url="https://internal-mirror.example.com/v1",
    )
    assert backend is not None
