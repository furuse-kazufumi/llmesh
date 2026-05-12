"""Sparse VLA agent (Phase 20, D3 integration step).

Wires :class:`SparseEncoder` (Phase 18) + :class:`LIFLayer` (Phase 20)
+ a thin action readout into the :class:`VLAAgent` contract from
Phase 9. The promise: a dense observation goes in, a sparse event
stream is produced, fed through one LIF layer, the resulting firing
rates pick an action class, and the agent emits an :class:`ActionStream`
of discrete commands.

This is intentionally a *small* integration. A production SNN-based
VLA would stack many layers (vision encoder -> recurrent reservoir
-> motor decoder), use trained weights, and run on neuromorphic
silicon. Phase 20 nails the **contract**: same input/output as the
mock VLA in Phase 9, but the internals are sparse + spiking. Phase 21+
can swap weights / depth / backend without touching the agent API.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from llmesh.core.agent import AgentConfig
from llmesh.vla.snn import LIFLayer, LIFParams, spikes_to_rates
from llmesh.vla.sparse_encoder import SparseEncoder, events_to_feature_vector
from llmesh.vla.vla import ActionStream, VisionLanguageRequest, VLAAgent


# ---------------------------------------------------------------------------
# Action space
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DiscreteAction:
    """One discrete action label + the rate that picked it."""

    label: str
    rate: float = 0.0
    notes: str = ""


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SparseVLAConfig:
    """Knobs for :class:`SparseVLAAgent`."""

    action_labels: tuple[str, ...] = ("forward", "left", "right", "stop")
    encoder_threshold: float = 0.1
    lif_params: LIFParams = field(default_factory=LIFParams)
    window_steps: int = 4              # how many SNN steps per observation
    min_rate_to_emit: float = 0.1      # below this -> "stop"

    def __post_init__(self) -> None:
        if not self.action_labels:
            raise ValueError("action_labels must not be empty")
        if self.window_steps <= 0:
            raise ValueError(f"window_steps must be > 0 (got {self.window_steps})")
        if not 0.0 <= self.min_rate_to_emit <= 1.0:
            raise ValueError(
                f"min_rate_to_emit must be in [0, 1] (got {self.min_rate_to_emit})"
            )


def _identity_weights(n_actions: int, n_inputs: int) -> tuple[tuple[float, ...], ...]:
    """A trivial weight matrix: each action neuron reads ``n_inputs`` evenly.

    Real systems would learn these. We pick a starter that gives every
    input a small symmetric influence so the agent isn't silent on
    first call. Bias is left at zero by default.
    """
    if n_actions <= 0 or n_inputs <= 0:
        raise ValueError("n_actions and n_inputs must be > 0")
    scale = 1.0 / max(1, n_inputs)
    return tuple(tuple(scale for _ in range(n_inputs)) for _ in range(n_actions))


class SparseVLAAgent(VLAAgent):
    """SparseEncoder + LIF layer + rate readout in one VLAAgent.

    The agent expects ``request.observation`` to encode a comma-
    separated dense vector ("0.5, 0.2, -0.1, ..."). That's the
    minimal interop with the existing text-observation contract from
    Phase 9. Phase 21+ can introduce richer observation types via a
    typed subclass without breaking this one.
    """

    def __init__(
        self,
        config: AgentConfig,
        *,
        n_dense_inputs: int,
        sparse_config: SparseVLAConfig | None = None,
        weights: Sequence[Sequence[float]] | None = None,
    ) -> None:
        super().__init__(config)
        self._sparse_config = sparse_config or SparseVLAConfig()
        self._encoder = SparseEncoder(
            channel_prefix="obs",
            threshold=self._sparse_config.encoder_threshold,
        )
        n_actions = len(self._sparse_config.action_labels)
        chosen_weights = (
            tuple(tuple(row) for row in weights)
            if weights is not None
            else _identity_weights(n_actions, n_dense_inputs)
        )
        self._layer = LIFLayer(
            weights=chosen_weights,
            params=self._sparse_config.lif_params,
        )
        self._n_dense_inputs = n_dense_inputs

    @property
    def encoder(self) -> SparseEncoder:
        return self._encoder

    @property
    def layer(self) -> LIFLayer:
        return self._layer

    def reset(self) -> None:
        """Clear encoder state and LIF membranes — call before a new episode."""
        self._encoder.reset()
        self._layer.reset()

    def run(self, request: VisionLanguageRequest) -> ActionStream:
        dense = _parse_dense_observation(request.observation, self._n_dense_inputs)
        # 1. dense -> sparse events (this advances encoder time by dt=1)
        obs = self._encoder.encode(dense)
        # 2. pool events back into a feature vector the layer can read
        pooled = events_to_feature_vector(obs, n_channels=self._n_dense_inputs)
        # 3. run the LIF layer for ``window_steps`` with the pooled current
        spikes: list[tuple[bool, ...]] = []
        for _ in range(self._sparse_config.window_steps):
            spikes.append(self._layer.step(pooled))
        # 4. firing rates -> action probabilities -> argmax
        rates = spikes_to_rates(tuple(spikes))
        cfg = self._sparse_config
        best_idx = 0
        best_rate = rates[0] if rates else 0.0
        for i, r in enumerate(rates):
            if r > best_rate:
                best_rate = r
                best_idx = i
        if best_rate < cfg.min_rate_to_emit:
            action = DiscreteAction(label="stop", rate=best_rate, notes="below_threshold")
            confidence = 0.0
        else:
            action = DiscreteAction(
                label=cfg.action_labels[best_idx],
                rate=best_rate,
                notes=f"argmax of {len(rates)} rates",
            )
            confidence = best_rate
        return ActionStream(
            actions=(action,),
            confidence=confidence,
            notes=(
                f"sparse_events={obs.n_events} sparsity={obs.sparsity:.3f} "
                f"window={cfg.window_steps} rate={best_rate:.3f}"
            ),
        )


def _parse_dense_observation(text: str, expected_dim: int) -> tuple[float, ...]:
    """Parse a "0.5, 0.2, ..." string into a fixed-length float vector.

    Pads with zeros if shorter, truncates if longer, so a malformed
    observation never crashes the agent mid-episode. Non-numeric
    tokens are coerced to 0.0 with the same tolerance.
    """
    if expected_dim <= 0:
        raise ValueError(f"expected_dim must be > 0 (got {expected_dim})")
    out: list[float] = []
    for token in (text or "").replace(";", ",").split(","):
        t = token.strip()
        if not t:
            continue
        try:
            out.append(float(t))
        except ValueError:
            out.append(0.0)
        if len(out) >= expected_dim:
            break
    while len(out) < expected_dim:
        out.append(0.0)
    return tuple(out)


__all__ = [
    "DiscreteAction",
    "SparseVLAAgent",
    "SparseVLAConfig",
]
