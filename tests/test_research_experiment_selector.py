"""Tests for Phase 14 D2 — Bayesian experiment selector."""

from __future__ import annotations


import pytest

from llmesh.research.experiment_selector import (
    Belief,
    BeliefStore,
    CandidateExperiment,
    SelectionReport,
    expected_information_gain,
    rank_candidates,
    select_next,
)


# ---------------------------------------------------------------------------
# Belief
# ---------------------------------------------------------------------------


class TestBelief:
    def test_default_is_uniform(self) -> None:
        b = Belief()
        assert b.probability == pytest.approx(0.5)

    def test_negative_or_zero_alpha_rejected(self) -> None:
        with pytest.raises(ValueError):
            Belief(alpha=0)
        with pytest.raises(ValueError):
            Belief(alpha=-1)

    def test_negative_beta_rejected(self) -> None:
        with pytest.raises(ValueError):
            Belief(beta=0)

    def test_updated_success_increases_probability(self) -> None:
        b = Belief().updated(success=True)
        assert b.probability > 0.5

    def test_updated_failure_decreases_probability(self) -> None:
        b = Belief().updated(success=False)
        assert b.probability < 0.5

    def test_updated_strength_scales(self) -> None:
        weak = Belief().updated(success=True, strength=0.1)
        strong = Belief().updated(success=True, strength=10.0)
        assert strong.probability > weak.probability

    def test_uncertainty_peaks_at_uniform(self) -> None:
        u_uniform = Belief(alpha=1, beta=1).uncertainty
        u_skewed = Belief(alpha=5, beta=1).uncertainty
        assert u_uniform > u_skewed

    def test_updated_strength_non_positive_raises(self) -> None:
        with pytest.raises(ValueError):
            Belief().updated(success=True, strength=0)

    def test_parent_preserved_through_update(self) -> None:
        b = Belief(parent="h0").updated(success=True)
        assert b.parent == "h0"


# ---------------------------------------------------------------------------
# BeliefStore
# ---------------------------------------------------------------------------


class TestBeliefStore:
    def test_set_then_get(self) -> None:
        s = BeliefStore()
        s.set("h1", Belief(alpha=3, beta=1))
        assert s.get("h1").alpha == 3

    def test_get_unknown_raises(self) -> None:
        with pytest.raises(KeyError):
            BeliefStore().get("nope")

    def test_set_empty_id_raises(self) -> None:
        with pytest.raises(ValueError):
            BeliefStore().set("", Belief())

    def test_has(self) -> None:
        s = BeliefStore()
        s.set("h1", Belief())
        assert s.has("h1") is True
        assert s.has("h2") is False

    def test_update_returns_new_belief_and_persists(self) -> None:
        s = BeliefStore()
        s.set("h1", Belief())
        new = s.update("h1", success=True)
        assert new.alpha > 1
        assert s.get("h1") == new

    def test_ancestor_uncertainty_walks_parent_chain(self) -> None:
        s = BeliefStore()
        s.set("root", Belief(alpha=1, beta=1))           # high uncertainty
        s.set("mid", Belief(alpha=1, beta=1, parent="root"))
        s.set("leaf", Belief(alpha=10, beta=10, parent="mid"))  # well-mapped
        # leaf's own uncertainty isn't included; only ancestors
        anc = s.ancestor_uncertainty("leaf")
        assert anc > 0
        # leaf in isolation (no parent) returns 0
        s.set("orphan", Belief())
        assert s.ancestor_uncertainty("orphan") == 0.0

    def test_ancestor_uncertainty_breaks_on_cycle(self) -> None:
        # Should not infinite-loop on a self-referential parent
        s = BeliefStore()
        s.set("a", Belief(parent="b"))
        s.set("b", Belief(parent="a"))
        anc = s.ancestor_uncertainty("a")
        assert anc < 10  # bounded; doesn't blow up


# ---------------------------------------------------------------------------
# CandidateExperiment
# ---------------------------------------------------------------------------


class TestCandidateExperiment:
    def test_probabilities_must_be_open_interval(self) -> None:
        with pytest.raises(ValueError):
            CandidateExperiment(
                candidate_id="c", hypothesis_id="h", p_success_if_true=0.0
            )
        with pytest.raises(ValueError):
            CandidateExperiment(
                candidate_id="c", hypothesis_id="h", p_success_if_false=1.0
            )


# ---------------------------------------------------------------------------
# expected_information_gain
# ---------------------------------------------------------------------------


class TestEIG:
    def test_perfectly_informative_experiment_yields_high_eig(self) -> None:
        s = BeliefStore()
        s.set("h", Belief())  # uniform prior
        # p_success_if_true=0.95 vs p_success_if_false=0.05 -> very informative
        informative = CandidateExperiment(
            candidate_id="info",
            hypothesis_id="h",
            p_success_if_true=0.95,
            p_success_if_false=0.05,
        )
        # p_success_if_true ≈ p_success_if_false -> almost no info
        useless = CandidateExperiment(
            candidate_id="useless",
            hypothesis_id="h",
            p_success_if_true=0.51,
            p_success_if_false=0.49,
        )
        eig_info = expected_information_gain(informative, s)
        eig_useless = expected_information_gain(useless, s)
        assert eig_info > eig_useless
        assert eig_useless < 0.001
        assert eig_info > 0.01

    def test_non_negative(self) -> None:
        s = BeliefStore()
        s.set("h", Belief(alpha=2, beta=5))
        c = CandidateExperiment(candidate_id="c", hypothesis_id="h")
        assert expected_information_gain(c, s) >= 0


# ---------------------------------------------------------------------------
# rank_candidates
# ---------------------------------------------------------------------------


class TestRankCandidates:
    def test_sorted_descending_by_score(self) -> None:
        s = BeliefStore()
        s.set("h1", Belief())
        s.set("h2", Belief())
        c1 = CandidateExperiment(
            candidate_id="c1", hypothesis_id="h1",
            p_success_if_true=0.95, p_success_if_false=0.05,
        )
        c2 = CandidateExperiment(
            candidate_id="c2", hypothesis_id="h2",
            p_success_if_true=0.55, p_success_if_false=0.45,
        )
        ranked = rank_candidates([c2, c1], s)
        assert ranked[0].candidate.candidate_id == "c1"  # higher EIG

    def test_unknown_hypothesis_silently_skipped(self) -> None:
        s = BeliefStore()
        s.set("h1", Belief())
        good = CandidateExperiment(candidate_id="g", hypothesis_id="h1")
        bad = CandidateExperiment(candidate_id="b", hypothesis_id="missing")
        out = rank_candidates([good, bad], s)
        assert len(out) == 1
        assert out[0].candidate.candidate_id == "g"

    def test_parent_bonus_applied(self) -> None:
        s = BeliefStore()
        s.set("root", Belief())
        s.set("leaf", Belief(parent="root"))
        c = CandidateExperiment(candidate_id="c", hypothesis_id="leaf")
        ranked = rank_candidates([c], s, parent_bonus_weight=1.0)
        assert ranked[0].parent_bonus > 0
        assert ranked[0].score == pytest.approx(
            ranked[0].eig + ranked[0].parent_bonus
        )

    def test_eig_per_usd(self) -> None:
        s = BeliefStore()
        s.set("h", Belief())
        cheap = CandidateExperiment(
            candidate_id="cheap", hypothesis_id="h",
            p_success_if_true=0.9, p_success_if_false=0.1, cost_usd=0.01,
        )
        expensive = CandidateExperiment(
            candidate_id="exp", hypothesis_id="h",
            p_success_if_true=0.9, p_success_if_false=0.1, cost_usd=10.0,
        )
        ranked = rank_candidates([cheap, expensive], s)
        cheap_r = next(r for r in ranked if r.candidate.candidate_id == "cheap")
        exp_r = next(r for r in ranked if r.candidate.candidate_id == "exp")
        assert cheap_r.eig_per_usd > exp_r.eig_per_usd


# ---------------------------------------------------------------------------
# select_next
# ---------------------------------------------------------------------------


class TestSelectNext:
    def test_picks_top_score(self) -> None:
        s = BeliefStore()
        s.set("h", Belief())
        c1 = CandidateExperiment(
            candidate_id="lo", hypothesis_id="h",
            p_success_if_true=0.55, p_success_if_false=0.45,
        )
        c2 = CandidateExperiment(
            candidate_id="hi", hypothesis_id="h",
            p_success_if_true=0.95, p_success_if_false=0.05,
        )
        report = select_next([c1, c2], s)
        assert isinstance(report, SelectionReport)
        assert report.chosen is not None
        assert report.chosen.candidate.candidate_id == "hi"

    def test_budget_skips_too_expensive(self) -> None:
        s = BeliefStore()
        s.set("h", Belief())
        cheap = CandidateExperiment(
            candidate_id="cheap", hypothesis_id="h",
            p_success_if_true=0.6, p_success_if_false=0.4, cost_usd=0.05,
        )
        expensive = CandidateExperiment(
            candidate_id="exp", hypothesis_id="h",
            p_success_if_true=0.95, p_success_if_false=0.05, cost_usd=10.0,
        )
        # expensive has higher EIG but doesn't fit budget
        report = select_next([cheap, expensive], s, budget_usd=1.0)
        assert report.chosen is not None
        assert report.chosen.candidate.candidate_id == "cheap"
        assert report.budget_remaining_usd == pytest.approx(0.95)

    def test_no_candidate_yields_none(self) -> None:
        s = BeliefStore()
        report = select_next([], s)
        assert report.chosen is None
        assert report.ranked == ()

    def test_all_unaffordable_yields_none(self) -> None:
        s = BeliefStore()
        s.set("h", Belief())
        c = CandidateExperiment(
            candidate_id="c", hypothesis_id="h",
            p_success_if_true=0.9, p_success_if_false=0.1, cost_usd=1000.0,
        )
        report = select_next([c], s, budget_usd=1.0)
        assert report.chosen is None
        assert report.budget_remaining_usd == 1.0
