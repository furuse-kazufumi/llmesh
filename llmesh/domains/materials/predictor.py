"""Materials predictor — structure → property contracts and mocks (Phase 4).

Three pieces define the materials closed loop:

1. :class:`PropertyPredictor` — given a :class:`Structure`, predict
   one :class:`Property` with optional uncertainty.
2. :class:`CandidateGeneratorAgent` — produce new :class:`Structure`
   candidates around a seed (analogous to BO acquisition).
3. :class:`EvaluatorAgent` — score one :class:`PropertyPrediction`
   against the campaign target and return an :class:`EvaluationResult`.

All three are ABCs; the Mock* implementations are deterministic so
the test path is reproducible without ``scikit-learn`` /
``numpy`` / a real DFT backend.

The ``random forest 代替`` requested in the loop-queue task is
satisfied by :class:`MockPropertyPredictor`: a hash-of-composition
pseudo-regressor that returns a stable property value with a small
synthetic stddev.  Real predictors (random-forest, GNN, ALIGNN, etc.)
become a drop-in replacement at the ABC boundary in later phases.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import hashlib


# ---------------------------------------------------------------------------
# dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Structure:
    """A minimal materials structure descriptor.

    Kept dependency-free: ``composition`` is a mapping of element
    symbol → atomic fraction (e.g. ``{"Fe": 0.7, "Ni": 0.3}``), and
    ``descriptors`` is a free-form dict for whatever extra features a
    predictor consumes (lattice parameters, space-group, SMILES, ...).
    The id field gives downstream agents a stable handle even when
    structures are compared by value semantics.
    """

    structure_id: str
    composition: dict[str, float] = field(default_factory=dict)
    descriptors: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Property:
    """One physical property and its unit (e.g. ``"band_gap"``, ``"eV"``)."""

    name: str
    unit: str = ""


@dataclass(frozen=True)
class PropertyPrediction:
    """One predictor output.

    ``stddev`` is an optional 1-sigma estimate; ``None`` when the
    predictor does not quantify uncertainty.
    """

    structure_id: str
    property: Property
    value: float
    stddev: float | None = None
    method: str = ""


@dataclass(frozen=True)
class EvaluationResult:
    """How a single PropertyPrediction scores against the campaign target.

    Attributes:
        prediction: The candidate's prediction under review.
        target_value: Numeric goal for ``prediction.property``.
        score: Lower-is-better fitness (default: absolute distance).
        rank: Optional position when evaluating a batch.
        accept: ``True`` when the candidate is worth keeping.
    """

    prediction: PropertyPrediction
    target_value: float
    score: float
    rank: int = -1
    accept: bool = False


# ---------------------------------------------------------------------------
# ABCs
# ---------------------------------------------------------------------------


class PropertyPredictor(ABC):
    """ABC: ``predict(structure, property) → PropertyPrediction``."""

    @abstractmethod
    def predict(self, structure: Structure, property: Property) -> PropertyPrediction:
        ...


class CandidateGeneratorAgent(ABC):
    """ABC: ``propose(seed, target_property, n) → tuple[Structure, ...]``."""

    @abstractmethod
    def propose(
        self, *, seed: Structure, target_property: Property, n: int
    ) -> tuple[Structure, ...]:
        ...


class EvaluatorAgent(ABC):
    """ABC: ``evaluate(predictions, target_value) → tuple[EvaluationResult, ...]``."""

    @abstractmethod
    def evaluate(
        self,
        *,
        predictions: tuple[PropertyPrediction, ...],
        target_value: float,
    ) -> tuple[EvaluationResult, ...]:
        ...


# ---------------------------------------------------------------------------
# Mock implementations
# ---------------------------------------------------------------------------


def _stable_hash(*parts: object) -> int:
    """Hash composition + property into a stable non-negative int.

    Plain ``hash()`` is salted across Python runs which would make
    test fixtures non-reproducible; SHA-1 of a deterministic encoding
    is salt-free and dependency-free.
    """
    digest = hashlib.sha1("|".join(repr(p) for p in parts).encode(), usedforsecurity=False)
    return int.from_bytes(digest.digest()[:4], "big")


class MockPropertyPredictor(PropertyPredictor):
    """Hash-based pseudo-regressor used in place of a random forest.

    Produces values in ``[low, high]`` derived deterministically from
    ``(structure.composition, property.name)``. The standard deviation
    is a tiny fraction of the range so downstream uncertainty-aware
    code can exercise the path without dealing with NaNs.
    """

    def __init__(self, *, low: float = 0.0, high: float = 5.0, method: str = "mock-rf") -> None:
        if high <= low:
            raise ValueError("high must be > low")
        self._low = float(low)
        self._high = float(high)
        self._method = method

    def predict(self, structure: Structure, property: Property) -> PropertyPrediction:
        h = _stable_hash(tuple(sorted(structure.composition.items())), property.name)
        frac = (h % 10_000) / 10_000.0
        value = self._low + frac * (self._high - self._low)
        stddev = (self._high - self._low) * 0.02
        return PropertyPrediction(
            structure_id=structure.structure_id,
            property=property,
            value=value,
            stddev=stddev,
            method=self._method,
        )


class MockCandidateGeneratorAgent(CandidateGeneratorAgent):
    """Generate ``n`` perturbations around a seed structure.

    Each candidate flips a deterministic fraction of the seed's
    composition; the result is reproducible and dependency-free.
    """

    def propose(
        self, *, seed: Structure, target_property: Property, n: int
    ) -> tuple[Structure, ...]:
        if n < 0:
            raise ValueError("n must be >= 0")
        if n == 0:
            return ()
        out: list[Structure] = []
        for i in range(n):
            # Perturb each element's fraction by a deterministic delta
            # then renormalise so the composition still sums to 1.0.
            delta = ((i + 1) % 7) * 0.01
            new_comp = {
                el: max(0.0, frac + (delta if idx % 2 == 0 else -delta))
                for idx, (el, frac) in enumerate(seed.composition.items())
            }
            total = sum(new_comp.values()) or 1.0
            new_comp = {el: v / total for el, v in new_comp.items()}
            out.append(
                Structure(
                    structure_id=f"{seed.structure_id}-{i:03d}",
                    composition=new_comp,
                    descriptors={
                        **seed.descriptors,
                        "_parent": seed.structure_id,
                        "_target_property": target_property.name,
                    },
                )
            )
        return tuple(out)


class MockEvaluatorAgent(EvaluatorAgent):
    """Score by absolute distance to ``target_value``; accept best K-fraction.

    ``accept`` flags the top half of the batch by score so a downstream
    loop can prune. With a single candidate the lone entry is always
    accepted.
    """

    def __init__(self, *, accept_fraction: float = 0.5) -> None:
        if not (0.0 < accept_fraction <= 1.0):
            raise ValueError("accept_fraction must be in (0, 1]")
        self._accept_fraction = accept_fraction

    def evaluate(
        self,
        *,
        predictions: tuple[PropertyPrediction, ...],
        target_value: float,
    ) -> tuple[EvaluationResult, ...]:
        scored = [
            EvaluationResult(prediction=p, target_value=target_value, score=abs(p.value - target_value))
            for p in predictions
        ]
        # rank by ascending score (best first)
        order = sorted(range(len(scored)), key=lambda i: scored[i].score)
        cutoff = max(1, int(round(len(scored) * self._accept_fraction)))
        out: list[EvaluationResult] = [scored[0]] * len(scored)  # placeholder
        for rank, idx in enumerate(order):
            base = scored[idx]
            out[idx] = EvaluationResult(
                prediction=base.prediction,
                target_value=target_value,
                score=base.score,
                rank=rank,
                accept=rank < cutoff,
            )
        return tuple(out)


# ---------------------------------------------------------------------------
# closed-loop convenience
# ---------------------------------------------------------------------------


def discover_top_k(
    *,
    seed: Structure,
    target_property: Property,
    target_value: float,
    generator: CandidateGeneratorAgent,
    predictor: PropertyPredictor,
    evaluator: EvaluatorAgent,
    n_candidates: int,
    k: int,
) -> tuple[EvaluationResult, ...]:
    """Run one generation: propose → predict → evaluate → top-K.

    Returns the top ``k`` accepted candidates by score (ascending).
    Phase 4 ships the single-shot version; Bayesian-optimisation-style
    iteration is a Phase 5+ concern.
    """
    if n_candidates < 1:
        raise ValueError("n_candidates must be >= 1")
    if k < 1:
        raise ValueError("k must be >= 1")
    candidates = generator.propose(seed=seed, target_property=target_property, n=n_candidates)
    predictions = tuple(predictor.predict(c, target_property) for c in candidates)
    results = evaluator.evaluate(predictions=predictions, target_value=target_value)
    accepted = sorted(
        (r for r in results if r.accept), key=lambda r: r.score
    )
    return tuple(accepted[:k])


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
