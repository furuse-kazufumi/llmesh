"""Tests for Phase 18 D3 — neuromorphic sparse encoder skeleton."""

from __future__ import annotations

import pytest

from llmesh.vla.sparse_encoder import (
    EventToken,
    SparseEncoder,
    SparseObservation,
    dense_to_events,
    events_to_feature_vector,
)


class TestDenseToEvents:
    def test_only_above_threshold_fires(self) -> None:
        out = dense_to_events(
            prev=(0.0, 0.0, 0.0),
            cur=(0.05, 0.5, -0.5),
            timestamp=1.0,
            channel_prefix="input",
            threshold=0.1,
        )
        # only ch 1 and 2 exceed |delta| > 0.1
        names = [e.channel for e in out]
        assert "input/0" not in names
        assert "input/1" in names
        assert "input/2" in names

    def test_signed_delta_preserved(self) -> None:
        out = dense_to_events(
            prev=(0.0,),
            cur=(0.5,),
            timestamp=1.0,
            channel_prefix="v",
            threshold=0.1,
        )
        assert out[0].value == pytest.approx(0.5)

        out2 = dense_to_events(
            prev=(0.5,),
            cur=(0.0,),
            timestamp=1.0,
            channel_prefix="v",
            threshold=0.1,
        )
        assert out2[0].value == pytest.approx(-0.5)

    def test_prev_none_treats_as_fresh_on(self) -> None:
        out = dense_to_events(
            prev=None,
            cur=(0.5, 0.5),
            timestamp=1.0,
            channel_prefix="v",
            threshold=0.1,
        )
        assert len(out) == 2

    def test_threshold_zero_rejected(self) -> None:
        with pytest.raises(ValueError):
            dense_to_events(
                prev=None, cur=(0.0,), timestamp=0.0,
                channel_prefix="v", threshold=0.0,
            )


class TestSparseObservation:
    def test_n_events(self) -> None:
        obs = SparseObservation(
            events=(
                EventToken(timestamp=0, channel="v/0", value=1.0),
                EventToken(timestamp=0, channel="v/1", value=1.0),
            ),
            n_dense_channels=4,
        )
        assert obs.n_events == 2

    def test_sparsity_half_active(self) -> None:
        obs = SparseObservation(
            events=(
                EventToken(timestamp=0, channel="v/0", value=1.0),
                EventToken(timestamp=0, channel="v/1", value=1.0),
            ),
            n_dense_channels=4,
        )
        # 2 distinct channels of 4 -> 0.5 sparsity
        assert obs.sparsity == pytest.approx(0.5)

    def test_sparsity_zero_when_n_channels_unset(self) -> None:
        obs = SparseObservation(events=(), n_dense_channels=0)
        assert obs.sparsity == 0.0

    def test_sparsity_full_when_no_events(self) -> None:
        obs = SparseObservation(events=(), n_dense_channels=10)
        assert obs.sparsity == pytest.approx(1.0)


class TestSparseEncoder:
    def test_first_encode_fresh_on(self) -> None:
        enc = SparseEncoder(threshold=0.1)
        obs = enc.encode((0.5, 0.5, 0.5))
        assert obs.n_events == 3  # all channels fire vs. zero baseline

    def test_second_encode_only_changes_fire(self) -> None:
        enc = SparseEncoder(threshold=0.1)
        enc.encode((0.5, 0.5, 0.5))
        obs = enc.encode((0.5, 0.5, 1.0))  # only ch 2 changes by 0.5
        assert obs.n_events == 1
        assert obs.events[0].channel == "input/2"

    def test_reset_clears_history(self) -> None:
        enc = SparseEncoder(threshold=0.1)
        enc.encode((0.5, 0.5))
        enc.reset()
        obs = enc.encode((0.5, 0.5))
        # after reset, baseline is 0 again -> both fire
        assert obs.n_events == 2

    def test_timestamps_advance(self) -> None:
        enc = SparseEncoder(threshold=0.1)
        obs1 = enc.encode((0.5,), dt=1.0)
        obs2 = enc.encode((1.0,), dt=2.0)
        assert obs1.events[0].timestamp == pytest.approx(1.0)
        assert obs2.events[0].timestamp == pytest.approx(3.0)

    def test_invalid_dt_rejected(self) -> None:
        enc = SparseEncoder()
        with pytest.raises(ValueError):
            enc.encode((0.5,), dt=0.0)

    def test_invalid_threshold_rejected(self) -> None:
        with pytest.raises(ValueError):
            SparseEncoder(threshold=0.0)


class TestEventsToFeatureVector:
    def test_pools_signed_deltas(self) -> None:
        obs = SparseObservation(
            events=(
                EventToken(timestamp=0, channel="v/0", value=0.5),
                EventToken(timestamp=1, channel="v/0", value=0.3),
                EventToken(timestamp=2, channel="v/1", value=-0.2),
            ),
            n_dense_channels=3,
        )
        vec = events_to_feature_vector(obs, n_channels=3)
        assert vec[0] == pytest.approx(0.8)
        assert vec[1] == pytest.approx(-0.2)
        assert vec[2] == pytest.approx(0.0)

    def test_invalid_channel_name_ignored(self) -> None:
        obs = SparseObservation(
            events=(
                EventToken(timestamp=0, channel="bogus", value=99.0),
                EventToken(timestamp=0, channel="v/0", value=1.0),
            ),
            n_dense_channels=2,
        )
        vec = events_to_feature_vector(obs, n_channels=2)
        assert vec[0] == pytest.approx(1.0)

    def test_out_of_range_index_ignored(self) -> None:
        obs = SparseObservation(
            events=(
                EventToken(timestamp=0, channel="v/99", value=1.0),
                EventToken(timestamp=0, channel="v/0", value=0.5),
            ),
            n_dense_channels=2,
        )
        vec = events_to_feature_vector(obs, n_channels=2)
        assert vec[0] == pytest.approx(0.5)
        assert vec[1] == pytest.approx(0.0)

    def test_invalid_n_channels_rejected(self) -> None:
        obs = SparseObservation(events=(), n_dense_channels=0)
        with pytest.raises(ValueError):
            events_to_feature_vector(obs, n_channels=0)
