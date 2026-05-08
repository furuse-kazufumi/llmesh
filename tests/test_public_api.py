"""Public API smoke tests — verify the v2.14+ stability surface.

Each public symbol listed in docs/API_STABILITY.md must:

- be importable from the documented path,
- be present in the corresponding ``__all__``,
- and ``llmesh.__version__`` must look like a SemVer string.

These tests catch accidental breaks where a refactor moves a symbol
without updating the ``__init__`` re-exports.
"""
from __future__ import annotations

import re

import pytest


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------

class TestTopLevel:
    def test_version_is_semver_like(self):
        import llmesh
        assert isinstance(llmesh.__version__, str)
        # Allow x.y.z and x.y.z.devN / a / rc-style suffixes — but at minimum
        # a leading numeric major.minor.patch.
        assert re.match(r"^\d+\.\d+\.\d+", llmesh.__version__) is not None

    def test_public_imports(self):
        from llmesh import (  # noqa: F401
            DataLevel,
            ClassifiedPayload,
            PromptFirewall,
            FirewallDecision,
            PresidioDetector,
            PresidioResult,
            PrivacySummarizer,
            SensorEvent,
            Priority,
        )

    def test_all_listed(self):
        import llmesh
        expected = {
            "__version__",
            "DataLevel", "ClassifiedPayload",
            "PromptFirewall", "FirewallDecision",
            "PresidioDetector", "PresidioResult",
            "PrivacySummarizer",
            "SensorEvent", "Priority",
        }
        assert expected <= set(llmesh.__all__)


# ---------------------------------------------------------------------------
# Privacy
# ---------------------------------------------------------------------------

class TestPrivacy:
    def test_privacy_imports(self):
        from llmesh.privacy import (  # noqa: F401
            PromptFirewall,
            FirewallDecision,
            PresidioDetector,
            PresidioResult,
            PrivacySummarizer,
            SummaryResult,
            SummarizationError,
        )

    def test_privacy_all_listed(self):
        from llmesh import privacy
        for name in [
            "PromptFirewall", "FirewallDecision",
            "PresidioDetector", "PresidioResult",
            "PrivacySummarizer", "SummaryResult", "SummarizationError",
        ]:
            assert name in privacy.__all__


# ---------------------------------------------------------------------------
# RAG
# ---------------------------------------------------------------------------

class TestRAG:
    def test_rag_imports_no_numpy_required(self):
        # Importing the module must not require numpy. Concrete
        # numpy-backed stores are loaded lazily via __getattr__.
        from llmesh.rag import (  # noqa: F401
            Embedder,
            MockEmbedder,
            OllamaEmbedder,
            EmbeddingError,
            Document,
            RetrievedDocument,
            VectorStore,
            SqliteVectorStore,
            Retriever,
            RetrievalResult,
        )

    def test_lazy_numpy_stores_in_all(self):
        from llmesh import rag
        assert "NumpyVectorStore" in rag.__all__
        assert "LSHVectorStore" in rag.__all__

    def test_lazy_attribute_lookup_unknown_raises(self):
        from llmesh import rag
        with pytest.raises(AttributeError):
            rag.NotARealAttribute  # noqa: B018


# ---------------------------------------------------------------------------
# Industrial
# ---------------------------------------------------------------------------

class TestIndustrial:
    def test_v3_module_imports(self):
        from llmesh.industrial.explainer import LLMExplainer, AlarmEvent, IncidentReport  # noqa: F401
        from llmesh.industrial.explained_cusum import ExplainedCUSUM, ExplainedSPCResult  # noqa: F401
        from llmesh.industrial.video_cusum import VideoCUSUM, VideoCUSUMResult  # noqa: F401
        from llmesh.industrial.vlm_feature_extractor import (  # noqa: F401
            VLMFeatureExtractor,
            VLMFeature,
            MockVisionCaptioner,
        )
        from llmesh.industrial.dnp3_adapter import DNP3Adapter, DNP3Point  # noqa: F401
        from llmesh.industrial.goose_adapter import GOOSEAdapter, GoosePDU, GooseTransport  # noqa: F401
        from llmesh.industrial.multimodal_spc import UnifiedSPC, UnifiedSPCResult  # noqa: F401

    def test_classifier_paths_unchanged(self):
        # A backward-compat anchor — DataLevel / ClassifiedPayload have
        # been at this exact path since v0.2.
        from llmesh.classifier import DataLevel, ClassifiedPayload  # noqa: F401
