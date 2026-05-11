"""Tests for Phase 11 — behavior-cloning trainer for VLA trajectories."""

from __future__ import annotations

import pytest

from llmesh.vla import (
    BCEvalReport,
    BCPolicy,
    Featurizer,
    JointTrajectory,
    JointWaypoint,
    TrajectoryEpisode,
    evaluate_bc_policy,
    train_bc_policy,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _traj(values: float, n_wp: int = 3, gripper_open_last: bool = True) -> JointTrajectory:
    wps = tuple(
        JointWaypoint(
            positions=(values + 0.1 * i, -0.8, 1.2, 0.0, 0.5, 0.0),
            duration_s=1.0,
            gripper=1.0 if (gripper_open_last and i == n_wp - 1) else 0.0,
        )
        for i in range(n_wp)
    )
    return JointTrajectory(
        joint_names=("j1", "j2", "j3", "j4", "j5", "j6"),
        waypoints=wps,
    )


def _ep(
    *,
    ep_id: str,
    instruction: str,
    colour: str,
    value: float,
    outcome: str = "success",
) -> TrajectoryEpisode:
    return TrajectoryEpisode(
        episode_id=ep_id,
        instruction=instruction,
        observation={"caption_colour": colour},
        trajectory=_traj(value),
        outcome=outcome,
    )


@pytest.fixture
def episodes() -> list[TrajectoryEpisode]:
    return [
        _ep(ep_id="e1", instruction="pick the red cup on the left", colour="red", value=0.0),
        _ep(ep_id="e2", instruction="pick the blue cup on the right", colour="blue", value=1.0),
        _ep(ep_id="e3", instruction="place the green block on the left", colour="green", value=2.0),
        _ep(ep_id="e4", instruction="pick the red cup", colour="red", value=3.0, outcome="collision"),
    ]


# ---------------------------------------------------------------------------
# Featurizer
# ---------------------------------------------------------------------------


class TestFeaturizer:
    def test_dim_matches_vector_length(self) -> None:
        fz = Featurizer()
        vec = fz.transform(instruction="pick red left", observation={"caption_colour": "red"})
        assert len(vec) == fz.dim

    def test_colour_one_hot(self) -> None:
        fz = Featurizer()
        vec = fz.transform(instruction="", observation={"caption_colour": "blue"})
        blue_idx = fz.colour_vocab.index("blue")
        red_idx = fz.colour_vocab.index("red")
        assert vec[blue_idx] == 1.0
        assert vec[red_idx] == 0.0

    def test_unknown_colour_is_all_zero(self) -> None:
        fz = Featurizer()
        vec = fz.transform(instruction="", observation={"caption_colour": "magenta"})
        for i in range(len(fz.colour_vocab)):
            assert vec[i] == 0.0

    def test_side_flags(self) -> None:
        fz = Featurizer()
        left = fz.transform(instruction="put it on the left", observation={})
        right = fz.transform(instruction="put it on the right", observation={})
        # last 4: side_left, side_right, has_pick, has_place
        assert left[-4] == 1.0 and left[-3] == 0.0
        assert right[-4] == 0.0 and right[-3] == 1.0

    def test_pick_and_place_verbs(self) -> None:
        fz = Featurizer()
        v_pick = fz.transform(instruction="pick that up", observation={})
        v_place = fz.transform(instruction="place it down", observation={})
        assert v_pick[-2] == 1.0 and v_pick[-1] == 0.0
        assert v_place[-2] == 0.0 and v_place[-1] == 1.0

    def test_japanese_side_words(self) -> None:
        fz = Featurizer()
        vec = fz.transform(instruction="左に置いて", observation={})
        assert vec[-4] == 1.0

    def test_missing_observation_key_does_not_crash(self) -> None:
        fz = Featurizer()
        # observation has no caption_colour at all
        vec = fz.transform(instruction="pick left", observation={})
        assert len(vec) == fz.dim
        # nothing in the colour one-hot should fire
        for i in range(len(fz.colour_vocab)):
            assert vec[i] == 0.0

    def test_deterministic(self) -> None:
        fz = Featurizer()
        a = fz.transform(instruction="pick the red cup", observation={"caption_colour": "red"})
        b = fz.transform(instruction="pick the red cup", observation={"caption_colour": "red"})
        assert a == b


# ---------------------------------------------------------------------------
# train_bc_policy
# ---------------------------------------------------------------------------


class TestTrainBCPolicy:
    def test_filters_out_non_success_by_default(self, episodes) -> None:
        policy = train_bc_policy(episodes)
        assert policy.n_examples == 3  # e4 is "collision"
        assert "e4" not in policy.source_ids

    def test_success_only_false_keeps_everything(self, episodes) -> None:
        policy = train_bc_policy(episodes, success_only=False)
        assert policy.n_examples == 4
        assert "e4" in policy.source_ids

    def test_empty_after_filter_raises(self) -> None:
        # all-failure dataset
        eps = [_ep(ep_id="x", instruction="i", colour="red", value=0.0, outcome="timeout")]
        with pytest.raises(ValueError):
            train_bc_policy(eps)

    def test_empty_input_raises(self) -> None:
        with pytest.raises(ValueError):
            train_bc_policy([])

    def test_accepts_iterable_not_just_list(self, episodes) -> None:
        policy = train_bc_policy(iter(episodes))
        assert policy.n_examples == 3

    def test_custom_featurizer_is_respected(self, episodes) -> None:
        custom = Featurizer(colour_vocab=("red", "blue"))
        policy = train_bc_policy(episodes, featurizer=custom)
        assert policy.featurizer is custom
        assert len(policy.features[0]) == custom.dim


# ---------------------------------------------------------------------------
# BCPolicy.predict
# ---------------------------------------------------------------------------


class TestBCPolicyPredict:
    def test_memorises_training_data(self, episodes) -> None:
        """Querying with a training example returns its own trajectory."""
        policy = train_bc_policy(episodes)
        for ep in episodes[:3]:  # only successes
            pred = policy.predict(
                instruction=ep.instruction, observation=ep.observation
            )
            assert pred == ep.trajectory

    def test_observation_conditional(self, episodes) -> None:
        """Same instruction, different scene -> different trajectory."""
        policy = train_bc_policy(episodes)
        red_pred = policy.predict(
            instruction="pick the cup on the left", observation={"caption_colour": "red"}
        )
        blue_pred = policy.predict(
            instruction="pick the cup on the right", observation={"caption_colour": "blue"}
        )
        assert red_pred != blue_pred

    def test_predict_on_empty_policy_raises(self) -> None:
        empty = BCPolicy(
            featurizer=Featurizer(),
            features=(),
            trajectories=(),
            source_ids=(),
        )
        with pytest.raises(ValueError):
            empty.predict(instruction="anything", observation={})

    def test_nearest_source_returns_episode_id(self, episodes) -> None:
        policy = train_bc_policy(episodes)
        sid = policy.nearest_source(
            instruction="pick the red cup on the left",
            observation={"caption_colour": "red"},
        )
        assert sid == "e1"

    def test_deterministic_tie_breaking(self) -> None:
        # two examples with identical features — earlier index wins.
        eps = [
            _ep(ep_id="a", instruction="pick left", colour="red", value=0.0),
            _ep(ep_id="b", instruction="pick left", colour="red", value=10.0),
        ]
        policy = train_bc_policy(eps)
        sid = policy.nearest_source(
            instruction="pick left", observation={"caption_colour": "red"}
        )
        assert sid == "a"

    def test_inconsistent_lengths_rejected(self) -> None:
        with pytest.raises(ValueError):
            BCPolicy(
                featurizer=Featurizer(),
                features=((0.0, 0.0),),
                trajectories=(),  # mismatch
                source_ids=("x",),
            )


# ---------------------------------------------------------------------------
# evaluate_bc_policy
# ---------------------------------------------------------------------------


class TestEvaluateBCPolicy:
    def test_perfect_recall_on_training_set(self, episodes) -> None:
        policy = train_bc_policy(episodes)
        # evaluate on the successful training episodes themselves
        report = evaluate_bc_policy(policy, episodes[:3])
        assert isinstance(report, BCEvalReport)
        assert report.n_episodes == 3
        assert report.mean_trajectory_mse == 0.0
        assert report.perfect_recall_rate == 1.0

    def test_held_out_returns_finite_mse(self, episodes) -> None:
        policy = train_bc_policy(episodes[:2])
        held_out = [
            _ep(
                ep_id="held",
                instruction="pick the green block on the left",
                colour="green",
                value=5.0,
            )
        ]
        report = evaluate_bc_policy(policy, held_out)
        assert report.n_episodes == 1
        assert report.mean_trajectory_mse > 0.0
        assert report.perfect_recall_rate == 0.0
        assert len(report.per_episode_mse) == 1

    def test_empty_val_set_does_not_crash(self, episodes) -> None:
        policy = train_bc_policy(episodes)
        report = evaluate_bc_policy(policy, [])
        assert report.n_episodes == 0
        assert report.mean_trajectory_mse == 0.0
        assert report.perfect_recall_rate == 0.0
        assert report.per_episode_mse == ()

    def test_length_mismatch_is_penalised(self) -> None:
        # Train on one 3-waypoint trajectory then evaluate against a
        # 5-waypoint reference for the same features. Expect a non-zero
        # MSE driven by the length penalty even if joint values overlap.
        train_ep = _ep(ep_id="t", instruction="pick left", colour="red", value=0.0)
        policy = train_bc_policy([train_ep])
        long_traj = JointTrajectory(
            joint_names=train_ep.trajectory.joint_names,
            waypoints=train_ep.trajectory.waypoints + train_ep.trajectory.waypoints[:2],
        )
        val_ep = TrajectoryEpisode(
            episode_id="v",
            instruction="pick left",
            observation={"caption_colour": "red"},
            trajectory=long_traj,
        )
        report = evaluate_bc_policy(policy, [val_ep])
        assert report.mean_trajectory_mse >= 4.0  # (3-5)^2 lower bound
