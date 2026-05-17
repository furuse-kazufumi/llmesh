"""CN-LLM provider presets — first-class 中国 LLM 統合.

Thin wrapper around :class:`OpenAICompatibleBackend` that bundles the
known-good base URLs and default models for the main Chinese LLM
providers. Goal: a regulated enterprise user can do::

    from llmesh.llm.cn_llm import qwen_backend, deepseek_backend
    backend = qwen_backend(api_key=os.environ["DASHSCOPE_API_KEY"])

…without hunting through provider documentation for the right URL or
suffix.

Coverage (v3.2 α):
- **Qwen** (Alibaba 通義千問) — DashScope OpenAI-compatible mode
- **DeepSeek** — already covered by ``openai_compatible.DEEPSEEK_BASE_URL``
- **GLM** (智譜 AI) — open.bigmodel.cn v4 endpoint
- **Kimi** (Moonshot K2.5) — api.moonshot.cn v1
- **Baichuan** (百川智能) — api.baichuan-ai.com v1
- **Yi** (零一万物) — api.lingyiwanwu.com v1 (bundled bonus)

Strategy reference:
- ``D:/projects/audit/STRATEGY_EAR_LOCAL_LLM_2026-05-17_PART2.md`` §5.2
- ``D:/projects/llmesh/docs/market/gap-analysis.md`` 領域 2

Each preset is a pure-data namespace — no network call, no provider
SDK import. The actual HTTP plumbing reuses
:class:`OpenAICompatibleBackend`, so US-dependency surface stays at
``httpx`` (transport-only).
"""
from __future__ import annotations

import dataclasses
import os
from typing import Optional

from .openai_compatible import OpenAICompatibleBackend


@dataclasses.dataclass(frozen=True)
class CNProviderPreset:
    """Static preset for a single Chinese LLM provider."""

    code: str  # "qwen" / "deepseek" / "glm" / "kimi" / "baichuan" / "yi"
    display_name: str
    base_url: str
    default_model: str
    api_key_env: str  # env var name commonly used by provider
    notes: str = ""


PRESETS: dict[str, CNProviderPreset] = {
    "qwen": CNProviderPreset(
        code="qwen",
        display_name="Qwen (通義千問 / Alibaba)",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        default_model="qwen2.5-72b-instruct",
        api_key_env="DASHSCOPE_API_KEY",
        notes="DashScope OpenAI-compatible endpoint; supports streaming + tools.",
    ),
    "deepseek": CNProviderPreset(
        code="deepseek",
        display_name="DeepSeek",
        base_url="https://api.deepseek.com/v1",
        default_model="deepseek-chat",
        api_key_env="DEEPSEEK_API_KEY",
        notes="OpenAI-compatible since v1; reasoner endpoint at /reasoner.",
    ),
    "glm": CNProviderPreset(
        code="glm",
        display_name="GLM (智譜 AI / Zhipu)",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        default_model="glm-4-plus",
        api_key_env="ZHIPUAI_API_KEY",
        notes="OpenAI-compatible v4 endpoint; GLM-5 ships under MIT license.",
    ),
    "kimi": CNProviderPreset(
        code="kimi",
        display_name="Kimi K2.5 (月之暗面 / Moonshot)",
        base_url="https://api.moonshot.cn/v1",
        default_model="moonshot-v1-128k",
        api_key_env="MOONSHOT_API_KEY",
        notes="OpenAI-compatible; up to 200k context.",
    ),
    "baichuan": CNProviderPreset(
        code="baichuan",
        display_name="Baichuan (百川智能)",
        base_url="https://api.baichuan-ai.com/v1",
        default_model="Baichuan4-Turbo",
        api_key_env="BAICHUAN_API_KEY",
        notes="OpenAI-compatible chat endpoint.",
    ),
    "yi": CNProviderPreset(
        code="yi",
        display_name="Yi (零一万物 / 01.AI)",
        base_url="https://api.lingyiwanwu.com/v1",
        default_model="yi-large",
        api_key_env="YI_API_KEY",
        notes="OpenAI-compatible chat endpoint.",
    ),
}


def get_preset(code: str) -> CNProviderPreset:
    """Return preset by code or raise KeyError with the known set."""
    key = code.lower()
    if key not in PRESETS:
        raise KeyError(
            f"unknown CN LLM provider: {code!r}; "
            f"choose from {sorted(PRESETS)}"
        )
    return PRESETS[key]


def build_backend(
    code: str,
    *,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
) -> OpenAICompatibleBackend:
    """Build an OpenAICompatibleBackend pre-configured for ``code``.

    ``api_key`` defaults to ``os.environ[preset.api_key_env]``.
    ``model`` defaults to ``preset.default_model``.
    ``base_url`` overrides the preset URL (e.g. for company-local mirrors).
    """
    preset = get_preset(code)
    if api_key is None:
        api_key = os.environ.get(preset.api_key_env)
    if not api_key:
        raise ValueError(
            f"api_key required for {preset.display_name}; "
            f"set arg or env {preset.api_key_env}"
        )
    return OpenAICompatibleBackend(
        base_url=base_url or preset.base_url,
        model=model or preset.default_model,
        api_key=api_key,
    )


# Convenience helpers — let callers say ``qwen_backend(api_key=...)``.

def qwen_backend(**kwargs: object) -> OpenAICompatibleBackend:
    return build_backend("qwen", **kwargs)  # type: ignore[arg-type]


def deepseek_backend(**kwargs: object) -> OpenAICompatibleBackend:
    return build_backend("deepseek", **kwargs)  # type: ignore[arg-type]


def glm_backend(**kwargs: object) -> OpenAICompatibleBackend:
    return build_backend("glm", **kwargs)  # type: ignore[arg-type]


def kimi_backend(**kwargs: object) -> OpenAICompatibleBackend:
    return build_backend("kimi", **kwargs)  # type: ignore[arg-type]


def baichuan_backend(**kwargs: object) -> OpenAICompatibleBackend:
    return build_backend("baichuan", **kwargs)  # type: ignore[arg-type]


def yi_backend(**kwargs: object) -> OpenAICompatibleBackend:
    return build_backend("yi", **kwargs)  # type: ignore[arg-type]
