"""Tests for llmesh.domains.materials — Phase 4 predictor + loop skeleton."""

from __future__ import annotations

import pytest

from llmesh.domains.materials import (
    CandidateGeneratorAgent,
    EvaluatorAgent,
    MockCandidateGeneratorAgent,
    MockEvaluatorAgent,
    MockPropertyPredictor,
    Property,
    PropertyPredictor,
    Structure,
    discover_top_k,
)


def _seed() -> Structure:
    return Structure(structure_id="seed", composition={"Fe": 0.7, "Ni": 0.3})


# ---------------------------------------------------------------------------
# ABC contracts
# ---------------------------------------------------------------------------


class TestABCs:
    def test_property_predictor_abc(self) -> None:
        with pytest.raises(TypeError):
            PropertyPredictor()  # type: ignore[abstract]

    def test_candidate_generator_abc(self) -> None:
        with pytest.raises(TypeError):
            CandidateGeneratorAgent()  # type: ignore[abstract]

    def test_evaluator_abc(self) -> None:
        with pytest.raises(TypeError):
            EvaluatorAgent()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# MockPropertyPredictor
# ---------------------------------------------------------------------------


class TestMockPredictor:
    def test_deterministic_across_calls(self) -> None:
        pred = MockPropertyPredictor()
        prop = Property(name="band_gap", unit="eV")
        a = pred.predict(_seed(), prop)
        b = pred.predict(_seed(), prop)
        assert a.value == b.value
        assert a.method == "mock-rf"

    def test_value_within_bounds(self) -> None:
        pred = MockPropertyPredictor(low=1.0, high=3.0)
        prop = Property(name="hardness", unit="GPa")
        out = pred.predict(_seed(), prop)
        assert 1.0 <= out.value <= 3.0

    def test_stddev_is_small_positive(self) -> None:
        pred = MockPropertyPredictor(low=0.0, high=10.0)
        prop = Property(name="conductivity")
        out = pred.predict(_seed(), prop)
        assert out.stddev is not None
        assert 0.0 < out.stddev < 1.0

    def test_high_must_exceed_low(self) -> None:
        with pytest.raises(ValueError, match="high"):
            MockPropertyPredictor(low=2.0, high=1.0)

    def test_different_property_yields_different_value(self) -> None:
        # Probabilistic-ish: with the same structure but different
        # property names the hash should land on a different bucket
        # except by collision. Pick two random property names to make
        # the chance of collision negligible.
        pred = MockPropertyPredictor(low=0.0, high=1000.0)
        a = pred.predict(_seed(), Property(name="prop_a")).value
        b = pred.predict(_seed(), Property(name="prop_b")).value
        assert a != b


# ---------------------------------------------------------------------------
# MockCandidateGeneratorAgent
# ---------------------------------------------------------------------------


class TestMockGenerator:
    def test_returns_n_candidates(self) -> None:
        gen = MockCandidateGeneratorAgent()
        cands = gen.propose(seed=_seed(), target_property=Property(name="band_gap"), n=5)
        assert len(cands) == 5

    def test_compositions_renormalise_to_one(self) -> None:
        gen = MockCandidateGeneratorAgent()
        cands = gen.propose(seed=_seed(), target_property=Property(name="band_gap"), n=3)
        for c in cands:
            total = sum(c.composition.values())
            assert abs(total - 1.0) < 1e-9

    def test_n_zero_returns_empty(self) -> None:
        gen = MockCandidateGeneratorAgent()
        assert gen.propose(seed=_seed(), target_property=Property(name="p"), n=0) == ()

    def test_negative_n_rejected(self) -> None:
        gen = MockCandidateGeneratorAgent()
        with pytest.raises(ValueError, match="n must be"):
            gen.propose(seed=_seed(), target_property=Property(name="p"), n=-1)

    def test_descriptors_carry_parent_and_target(self) -> None:
        gen = MockCandidateGeneratorAgent()
        cands = gen.propose(seed=_seed(), target_property=Property(name="band_gap"), n=1)
        assert cands[0].descriptors["_parent"] == "seed"
        assert cands[0].descriptors["_target_property"] == "band_gap"


# ---------------------------------------------------------------------------
# MockEvaluatorAgent
# ---------------------------------------------------------------------------


class TestMockEvaluator:
    def _predictions(self) -> tuple:
        from llmesh.domains.materials import PropertyPrediction

        prop = Property(name="band_gap", unit="eV")
        return tuple(
            PropertyPrediction(structure_id=f"s{i}", property=prop, value=v, stddev=0.01)
            for i, v in enumerate([1.0, 2.0, 3.0, 4.0])
        )

    def test_score_is_absolute_distance(self) -> None:
        evaluator = MockEvaluatorAgent(accept_fraction=0.5)
        results = evaluator.evaluate(predictions=self._predictions(), target_value=2.5)
        scores = {r.prediction.structure_id: r.score for r in results}
        assert scores["s1"] == 0.5
        assert scores["s2"] == 0.5
        assert scores["s0"] == 1.5

    def test_ranks_assigned_by_ascending_score(self) -> None:
        evaluator = MockEvaluatorAgent()
        results = evaluator.evaluate(predictions=self._predictions(), target_value=2.5)
        # the closest two (s1, s2) should have ranks 0 and 1
        ranks = sorted(r.rank for r in results)
        assert ranks == [0, 1, 2, 3]
        best = min(results, key=lambda r: r.rank)
        assert best.prediction.structure_id in {"s1", "s2"}

    def test_accept_fraction_half(self) -> None:
        evaluator = MockEvaluatorAgent(accept_fraction=0.5)
        results = evaluator.evaluate(predictions=self._predictions(), target_value=2.5)
        accepted = [r for r in results if r.accept]
        # 4 predictions × 0.5 = 2 accepted
        assert len(accepted) == 2

    def test_invalid_accept_fraction(self) -> None:
        with pytest.raises(ValueError):
            MockEvaluatorAgent(accept_fraction=0.0)
        with pytest.raises(ValueError):
            MockEvaluatorAgent(accept_fraction=1.5)


# ---------------------------------------------------------------------------
# discover_top_k closed loop
# ---------------------------------------------------------------------------


class TestDiscoverTopK:
    def test_returns_at_most_k(self) -> None:
        top = discover_top_k(
            seed=_seed(),
            target_property=Property(name="band_gap", unit="eV"),
            target_value=2.5,
            generator=MockCandidateGeneratorAgent(),
            predictor=MockPropertyPredictor(low=0.0, high=5.0),
            evaluator=MockEvaluatorAgent(accept_fraction=0.5),
            n_candidates=10,
            k=3,
        )
        assert len(top) <= 3
        assert all(r.accept for r in top)
        # top results sorted by score ascending
        assert list(r.score for r in top) == sorted(r.score for r in top)

    def test_k_smaller_than_accepted(self) -> None:
        top = discover_top_k(
            seed=_seed(),
            target_property=Property(name="band_gap"),
            target_value=2.5,
            generator=MockCandidateGeneratorAgent(),
            predictor=MockPropertyPredictor(),
            evaluator=MockEvaluatorAgent(accept_fraction=1.0),  # accept all
            n_candidates=8,
            k=2,
        )
        assert len(top) == 2

    def test_invalid_args_rejected(self) -> None:
        with pytest.raises(ValueError, match="n_candidates"):
            discover_top_k(
                seed=_seed(),
                target_property=Property(name="p"),
                target_value=1.0,
                generator=MockCandidateGeneratorAgent(),
                predictor=MockPropertyPredictor(),
                evaluator=MockEvaluatorAgent(),
                n_candidates=0,
                k=1,
            )
        with pytest.raises(ValueError, match="k"):
            discover_top_k(
                seed=_seed(),
                target_property=Property(name="p"),
                target_value=1.0,
                generator=MockCandidateGeneratorAgent(),
                predictor=MockPropertyPredictor(),
                evaluator=MockEvaluatorAgent(),
                n_candidates=5,
                k=0,
            )


# ---------------------------------------------------------------------------
# Immutability sanity
# ---------------------------------------------------------------------------


class TestImmutability:
    def test_property_frozen(self) -> None:
        p = Property(name="x")
        with pytest.raises(Exception):
            p.name = "y"  # type: ignore[misc]

    def test_structure_frozen(self) -> None:
        s = _seed()
        with pytest.raises(Exception):
            s.structure_id = "other"  # type: ignore[misc]
