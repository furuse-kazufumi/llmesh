"""Spiking neural network primitives (Phase 20, D3 step 2).

Phase 18 gives us :class:`SparseObservation` (event-stream input).
This module ships the next layer up — a stdlib implementation of the
Leaky Integrate-and-Fire (LIF) neuron model with a thin readout layer
— so an event stream can drive a small SNN-style policy that emits
spikes the downstream VLA agent can decode into actions.

Why bother in pure Python? Two reasons:

1. **Edge-deploy contract first.** Real neuromorphic runtimes (Loihi,
   Akida, SpiNNaker, BrainScaleS) all expect integer- / fixed-point
   semantics with explicit time-stepping. We get the shape of that
   contract right *before* picking a backend, so a later port is a
   data-transport problem, not a redesign.
2. **CI testability.** Stdlib means the model runs in our existing
   test suite with no extra dependencies and stays reproducible.

Reference: Gerstner & Kistler, *Spiking Neuron Models* (2002), ch. 4.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


# ---------------------------------------------------------------------------
# Leaky Integrate-and-Fire neuron
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LIFParams:
    """Hyperparameters for a LIF neuron.

    ``leak`` is per-timestep decay (0..1); ``threshold`` is the voltage
    at which a spike is emitted; ``reset`` is the voltage right after
    a spike (typically 0); ``refractory_steps`` blanks output for N
    steps after a spike, which models the recovery period.
    """

    threshold: float = 1.0
    leak: float = 0.1
    reset: float = 0.0
    refractory_steps: int = 1

    def __post_init__(self) -> None:
        if self.threshold <= 0:
            raise ValueError(f"threshold must be > 0 (got {self.threshold})")
        if not 0.0 <= self.leak < 1.0:
            raise ValueError(f"leak must be in [0, 1) (got {self.leak})")
        if self.refractory_steps < 0:
            raise ValueError(
                f"refractory_steps must be >= 0 (got {self.refractory_steps})"
            )


@dataclass
class LIFState:
    """Mutable per-neuron state."""

    membrane: float = 0.0
    refractory_remaining: int = 0


def step_lif(
    state: LIFState, input_current: float, params: LIFParams
) -> bool:
    """Advance one neuron by one timestep. Returns ``True`` if it spiked.

    Mutates ``state`` in place. Order of operations: refractory check
    first (input is gated to zero during refractory), then leak +
    integrate, then threshold check. This matches the standard
    Brian2 / Nengo conventions so trace replay across backends agrees.
    """
    if state.refractory_remaining > 0:
        state.refractory_remaining -= 1
        # leak still acts during refractory so very-long inputs decay
        state.membrane *= 1.0 - params.leak
        return False
    state.membrane = state.membrane * (1.0 - params.leak) + input_current
    if state.membrane >= params.threshold:
        state.membrane = params.reset
        state.refractory_remaining = params.refractory_steps
        return True
    return False


# ---------------------------------------------------------------------------
# Layer
# ---------------------------------------------------------------------------


@dataclass
class LIFLayer:
    """Vector of LIF neurons sharing the same params.

    ``weights`` is a row-major matrix: ``weights[i][j]`` is the
    connection from input ``j`` to neuron ``i``. ``bias[i]`` is the
    per-neuron drive added to the input current on every step.

    The layer is *not* meant to be trained inside this module — those
    bits live in :mod:`llmesh.vla.bc_trainer` (Phase 11) and the
    eventual neuromorphic-aware trainer. This class just executes a
    fixed weight matrix forward in event time.
    """

    weights: tuple[tuple[float, ...], ...]
    bias: tuple[float, ...] = ()
    params: LIFParams = field(default_factory=LIFParams)
    _state: list[LIFState] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.weights:
            raise ValueError("weights must not be empty")
        n_neurons = len(self.weights)
        n_inputs = len(self.weights[0])
        for row in self.weights:
            if len(row) != n_inputs:
                raise ValueError("weights rows must have equal length")
        if self.bias and len(self.bias) != n_neurons:
            raise ValueError(
                f"bias length ({len(self.bias)}) must equal n_neurons ({n_neurons})"
            )
        self._state = [LIFState() for _ in range(n_neurons)]

    @property
    def n_neurons(self) -> int:
        return len(self.weights)

    @property
    def n_inputs(self) -> int:
        return len(self.weights[0]) if self.weights else 0

    def reset(self) -> None:
        """Clear membrane voltages and refractory counters."""
        for s in self._state:
            s.membrane = 0.0
            s.refractory_remaining = 0

    def step(self, input_vec: tuple[float, ...]) -> tuple[bool, ...]:
        """Advance one timestep with ``input_vec``. Returns spike mask per neuron."""
        if len(input_vec) != self.n_inputs:
            raise ValueError(
                f"input dim {len(input_vec)} != layer n_inputs {self.n_inputs}"
            )
        spikes: list[bool] = []
        for i in range(self.n_neurons):
            current = sum(
                self.weights[i][j] * input_vec[j] for j in range(self.n_inputs)
            )
            if self.bias:
                current += self.bias[i]
            spikes.append(step_lif(self._state[i], current, self.params))
        return tuple(spikes)


def run_layer(
    layer: LIFLayer, input_sequence: Iterable[tuple[float, ...]]
) -> tuple[tuple[bool, ...], ...]:
    """Run a sequence of input vectors and return one spike mask per step."""
    return tuple(layer.step(vec) for vec in input_sequence)


# ---------------------------------------------------------------------------
# Rate decoder (spike train -> dense vector)
# ---------------------------------------------------------------------------


def spikes_to_rates(
    spike_history: tuple[tuple[bool, ...], ...],
) -> tuple[float, ...]:
    """Convert a window of spike masks into per-neuron firing rates.

    Rate = (spikes in window) / (length of window). Returns zeros for
    an empty window so a caller doesn't have to guard the divisor.
    """
    if not spike_history:
        return ()
    n_neurons = len(spike_history[0])
    counts = [0] * n_neurons
    for step in spike_history:
        for i, fired in enumerate(step):
            if fired:
                counts[i] += 1
    n = len(spike_history)
    return tuple(c / n for c in counts)


__all__ = [
    "LIFLayer",
    "LIFParams",
    "LIFState",
    "run_layer",
    "spikes_to_rates",
    "step_lif",
]
