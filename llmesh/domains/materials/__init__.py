"""Materials science domain — structure → property prediction (Phase 4).

Phase 4 ships **interfaces only**: ABCs for property prediction,
candidate generation, and evaluation, plus deterministic Mock
implementations so the rest of the research-orchestration stack
can wire up a materials pipeline without a real ML backend.
"""

from __future__ import annotations

from llmesh.domains.materials.predictor import (
    CandidateGeneratorAgent,
    EvaluationResult,
    EvaluatorAgent,
    MockCandidateGeneratorAgent,
    MockEvaluatorAgent,
    MockPropertyPredictor,
    Property,
    PropertyPrediction,
    PropertyPredictor,
    Structure,
    discover_top_k,
)

__all__ = [
    "CandidateGeneratorAgent",
    "EvaluationResult",
    "EvaluatorAgent",
    "MockCandidateGeneratorAgent",
    "MockEvaluatorAgent",
    "MockPropertyPredictor",
    "Property",
    "PropertyPrediction",
    "PropertyPredictor",
    "Structure",
    "discover_top_k",
]
