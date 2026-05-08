"""LLMesh — Secure Local LLM Swarm over MCP.

Public API surface
==================

The names exported from this top-level package follow LLMesh's
**stability guarantees** (see ``docs/API_STABILITY.md``):

- Anything listed in :data:`__all__` is part of the public API.
- Public names follow SemVer from v3.0.0 onwards. Any breaking change
  to a public name requires a major version bump and a deprecation
  cycle of at least one minor release.
- Sub-package public APIs are re-exported here for convenience but the
  authoritative list lives in each sub-package's ``__init__``.
- Anything imported through dotted paths into private sub-modules
  (``llmesh.foo._private``) is **not** public — those are free to
  change at any time.

The version is read from the installed package metadata when available
so that built wheels stay in sync with ``pyproject.toml``. During
in-place development we fall back to a pinned literal.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Version detection
# ---------------------------------------------------------------------------

try:
    from importlib.metadata import PackageNotFoundError, version as _pkg_version
    try:
        __version__ = _pkg_version("llmesh")
    except PackageNotFoundError:
        __version__ = "3.0.0"
except Exception:  # pragma: no cover — defensive
    __version__ = "3.0.0"


# ---------------------------------------------------------------------------
# Public API — re-export the most commonly used building blocks.
#
# All of these can also be imported from their sub-packages directly;
# the convenience surface here means downstream code can write
# ``from llmesh import PromptFirewall`` without remembering paths.
# ---------------------------------------------------------------------------

from llmesh.classifier import DataLevel, ClassifiedPayload
from llmesh.privacy import (
    FirewallDecision,
    PresidioDetector,
    PresidioResult,
    PrivacySummarizer,
    PromptFirewall,
)
from llmesh.industrial.sensor_event import Priority, SensorEvent


__all__ = [
    "__version__",
    # Classifier
    "DataLevel",
    "ClassifiedPayload",
    # Privacy stack
    "PromptFirewall",
    "FirewallDecision",
    "PresidioDetector",
    "PresidioResult",
    "PrivacySummarizer",
    # Industrial primitives
    "SensorEvent",
    "Priority",
]
