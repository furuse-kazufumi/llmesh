"""Tests for Phase 20 D3 — SNN + SparseVLAAgent."""

from __future__ import annotations

import pytest

from llmesh.core.agent import AgentConfig
from llmesh.vla.snn import (
    LIFLayer,
    LIFParams,
    LIFState,
    run_layer,
    spikes_to_rates,
    step_lif,
)
from llmesh.vla.sparse_vla import (
    DiscreteAction,
    SparseVLAAgent,
    SparseVLAConfig,
)
from llmesh.vla.vla import ActionStream, VisionLanguageRequest


# ---------------------------------------------------------------------------
# LIFParams / step_lif
# ---------------------------------------------------------------------------


class TestLIFParams:
    def test_negative_threshold_rejected(self) -> None:
        with pytest.raises(ValueError):
            LIFParams(threshold=-1)

    def test_leak_out_of_range_rejected(self) -> None:
        with pytest.raises(ValueError):
            LIFParams(leak=1.0)
        with pytest.raises(ValueError):
            LIFParams(leak=-0.1)

    def test_negative_refractory_rejected(self) -> None:
        with pytest.raises(ValueError):
            LIFParams(refractory_steps=-1)


class TestStepLIF:
    def test_subthreshold_does_not_spike(self) -> None:
        st = LIFState()
        params = LIFParams(threshold=1.0, leak=0.0)
        assert step_lif(st, 0.5, params) is False
        assert st.membrane == pytest.approx(0.5)

    def test_threshold_triggers_spike_and_resets(self) -> None:
        st = LIFState()
        params = LIFParams(threshold=1.0, leak=0.0, reset=0.0, refractory_steps=0)
        spiked = step_lif(st, 1.5, params)
        assert spiked is True
        assert st.membrane == pytest.approx(0.0)

    def test_leak_decays_membrane(self) -> None:
        st = LIFState(membrane=1.0)
        params = LIFParams(threshold=10.0, leak=0.5)
        step_lif(st, 0.0, params)
        assert st.membrane == pytest.approx(0.5)

    def test_refractory_period_blocks_input(self) -> None:
        st = LIFState()
        params = LIFParams(threshold=1.0, leak=0.0, refractory_steps=2)
        # spike
        step_lif(st, 1.5, params)
        assert st.refractory_remaining == 2
        # during refractory: even big input doesn't spike
        spiked = step_lif(st, 5.0, params)
        assert spiked is False
        assert st.refractory_remaining == 1


# ---------------------------------------------------------------------------
# LIFLayer
# ---------------------------------------------------------------------------


class TestLIFLayer:
    def test_construction_requires_weights(self) -> None:
        with pytest.raises(ValueError):
            LIFLayer(weights=())

    def test_inconsistent_row_lengths_rejected(self) -> None:
        with pytest.raises(ValueError):
            LIFLayer(weights=((1.0, 2.0), (3.0,)))

    def test_bias_length_must_match(self) -> None:
        with pytest.raises(ValueError):
            LIFLayer(weights=((1.0,), (1.0,)), bias=(0.5,))

    def test_input_dim_mismatch_rejected(self) -> None:
        layer = LIFLayer(weights=((1.0, 1.0),))
        with pytest.raises(ValueError):
            layer.step((0.5,))

    def test_step_returns_spike_mask(self) -> None:
        # One neuron, threshold 1.0, weight 1.0 -> input 1.5 spikes
        layer = LIFLayer(
            weights=((1.0,),),
            params=LIFParams(threshold=1.0, leak=0.0, refractory_steps=0),
        )
        spikes = layer.step((1.5,))
        assert spikes == (True,)

    def test_run_layer_aggregates_history(self) -> None:
        layer = LIFLayer(
            weights=((1.0,),),
            params=LIFParams(threshold=1.0, leak=0.0, refractory_steps=0),
        )
        history = run_layer(layer, [(1.5,), (0.0,), (1.5,)])
        assert history == ((True,), (False,), (True,))

    def test_reset_clears_state(self) -> None:
        layer = LIFLayer(
            weights=((1.0,),),
            params=LIFParams(threshold=1.0, leak=0.0, refractory_steps=5),
        )
        layer.step((1.5,))  # spike, enters refractory
        layer.reset()
        # After reset, next step should be able to spike again
        assert layer.step((1.5,)) == (True,)

    def test_bias_adds_to_current(self) -> None:
        layer = LIFLayer(
            weights=((1.0,),),
            bias=(0.8,),
            params=LIFParams(threshold=1.0, leak=0.0),
        )
        # input 0.3 + bias 0.8 = 1.1 -> spike
        assert layer.step((0.3,)) == (True,)


# ---------------------------------------------------------------------------
# spikes_to_rates
# ---------------------------------------------------------------------------


class TestSpikesToRates:
    def test_all_silent(self) -> None:
        rates = spikes_to_rates(((False, False),) * 4)
        assert rates == (0.0, 0.0)

    def test_one_neuron_half_rate(self) -> None:
        rates = spikes_to_rates(
            ((True, False), (False, False), (True, False), (False, False))
        )
        assert rates == (0.5, 0.0)

    def test_empty_history(self) -> None:
        assert spikes_to_rates(()) == ()


# ---------------------------------------------------------------------------
# SparseVLAConfig
# ---------------------------------------------------------------------------


class TestSparseVLAConfig:
    def test_empty_labels_rejected(self) -> None:
        with pytest.raises(ValueError):
            SparseVLAConfig(action_labels=())

    def test_invalid_window_rejected(self) -> None:
        with pytest.raises(ValueError):
            SparseVLAConfig(window_steps=0)

    def test_min_rate_out_of_range_rejected(self) -> None:
        with pytest.raises(ValueError):
            SparseVLAConfig(min_rate_to_emit=1.5)


# ---------------------------------------------------------------------------
# SparseVLAAgent
# ---------------------------------------------------------------------------


class TestSparseVLAAgent:
    def test_emits_action_stream(self) -> None:
        agent = SparseVLAAgent(
            AgentConfig(name="sparse"),
            n_dense_inputs=4,
            sparse_config=SparseVLAConfig(
                action_labels=("a", "b"),
                lif_params=LIFParams(threshold=0.05, leak=0.0, refractory_steps=0),
                window_steps=3,
                min_rate_to_emit=0.0,
            ),
        )
        req = VisionLanguageRequest(
            instruction="go", observation="0.5, 0.5, 0.5, 0.5"
        )
        stream = agent.run(req)
        assert isinstance(stream, ActionStream)
        assert len(stream.actions) == 1
        assert isinstance(stream.actions[0], DiscreteAction)

    def test_low_input_returns_stop(self) -> None:
        agent = SparseVLAAgent(
            AgentConfig(name="sparse"),
            n_dense_inputs=2,
            sparse_config=SparseVLAConfig(
                action_labels=("forward", "left"),
                lif_params=LIFParams(threshold=10.0, leak=0.0),  # very high threshold
                window_steps=2,
                min_rate_to_emit=0.4,
            ),
        )
        # First call: sparse encoder fires (vs zero baseline) but threshold
        # is huge, so no LIF spikes -> rate 0 -> below min_rate -> stop
        req = VisionLanguageRequest(instruction="go", observation="0.01, 0.01")
        stream = agent.run(req)
        assert stream.actions[0].label == "stop"

    def test_malformed_observation_padded(self) -> None:
        agent = SparseVLAAgent(
            AgentConfig(name="sparse"),
            n_dense_inputs=4,
            sparse_config=SparseVLAConfig(min_rate_to_emit=0.0),
        )
        # only 2 values supplied; padded to 4
        req = VisionLanguageRequest(instruction="go", observation="0.5, 0.5")
        # should not raise
        stream = agent.run(req)
        assert isinstance(stream, ActionStream)

    def test_reset_clears_encoder_and_layer(self) -> None:
        agent = SparseVLAAgent(
            AgentConfig(name="sparse"),
            n_dense_inputs=2,
            sparse_config=SparseVLAConfig(
                lif_params=LIFParams(threshold=0.5, leak=0.0, refractory_steps=10),
            ),
        )
        req = VisionLanguageRequest(instruction="go", observation="1.0, 1.0")
        agent.run(req)  # neurons go into refractory
        agent.reset()
        # encoder previous reset means second call again sees fresh-on
        agent.run(req)  # should run without error
