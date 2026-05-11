"""Tests for Phase 9 — VLA PoC, turtlesim mock.

The headline assertion is **scene conditionality**: the same
instruction with two different observations must yield different
ActionStreams. The other tests pin down the encoder / decoder / scene
parser building blocks.
"""

from __future__ import annotations

import pytest

from llmesh.core.agent import AgentConfig
from llmesh.vla import (
    ActionStream,
    EpisodeOutcome,
    MockTextSceneEncoder,
    MockVLAAgent,
    Twist,
    VLAAgent,
    VisionLanguageRequest,
    evaluate_trials,
    parse_scene_text,
)
from llmesh.vla.encoders import VisionEncoder


# ---------------------------------------------------------------------------
# Scene parser
# ---------------------------------------------------------------------------


class TestSceneParser:
    def test_parses_turtle_and_objects(self) -> None:
        state = parse_scene_text(
            "turtle at (1.0, 2.0), red_flag at (5.0, 5.0), wall at (4.0, 4.0)"
        )
        assert state.has_self
        assert state.self_object.name == "turtle"
        names = [o.name for o in state.objects]
        assert "red_flag" in names
        assert "wall" in names

    def test_walls_collected_separately(self) -> None:
        state = parse_scene_text(
            "turtle at (0, 0), wall at (1, 0), wall at (0, 1), red_flag at (5, 5)"
        )
        wall_names = [w.name for w in state.walls]
        assert wall_names == ["wall", "wall"]

    def test_no_match_yields_empty(self) -> None:
        state = parse_scene_text("no recognisable tokens here")
        assert state.self_object is None
        assert state.objects == ()

    def test_blank_input_safe(self) -> None:
        state = parse_scene_text("")
        assert state.self_object is None

    def test_negative_and_decimal_floats(self) -> None:
        state = parse_scene_text("turtle at (-1.5, 2.25)")
        assert state.self_object.x == -1.5
        assert state.self_object.y == 2.25

    def test_find_returns_first_prefix_match(self) -> None:
        state = parse_scene_text("turtle at (0,0), red_flag at (1,1), red_block at (2,2)")
        red = state.find("red")
        assert red is not None
        assert red.name == "red_flag"


# ---------------------------------------------------------------------------
# Encoder
# ---------------------------------------------------------------------------


class TestEncoder:
    def test_features_include_distance_and_bearing(self) -> None:
        feats = MockTextSceneEncoder().encode(
            "turtle at (0, 0), red_flag at (3, 4)"
        )
        # 3-4-5 triangle
        assert feats.features["nearest_target_dist"] == pytest.approx(5.0)
        assert feats.features["nearest_target_name"] == "red_flag"

    def test_wall_close_flag_respects_threshold(self) -> None:
        # 1-m wall vs 1.0 m threshold → close
        feats = MockTextSceneEncoder(wall_proximity_m=1.0).encode(
            "turtle at (0, 0), wall at (1, 0), red_flag at (5, 5)"
        )
        assert feats.features["wall_close"] is True
        # 5-m wall vs 1.0 m threshold → not close
        feats_far = MockTextSceneEncoder(wall_proximity_m=1.0).encode(
            "turtle at (0, 0), wall at (5, 0), red_flag at (3, 4)"
        )
        assert feats_far.features["wall_close"] is False

    def test_no_self_observation_returns_minimal_features(self) -> None:
        feats = MockTextSceneEncoder().encode("red_flag at (1, 1)")
        assert feats.features["has_self"] is False
        assert "nearest_target_dist" not in feats.features

    def test_abc_cannot_be_instantiated(self) -> None:
        with pytest.raises(TypeError):
            VisionEncoder()  # type: ignore[abstract]

    def test_invalid_threshold_rejected(self) -> None:
        with pytest.raises(ValueError):
            MockTextSceneEncoder(wall_proximity_m=0)


# ---------------------------------------------------------------------------
# Decoder
# ---------------------------------------------------------------------------


class TestDecoder:
    def _agent(self) -> MockVLAAgent:
        return MockVLAAgent(AgentConfig(name="vla.mock", model="mock"))

    def test_emits_align_advance_stop_for_target(self) -> None:
        agent = self._agent()
        out = agent.run(
            VisionLanguageRequest(
                instruction="go to the red flag",
                observation="turtle at (0, 0), red_flag at (3, 0)",
            )
        )
        assert len(out.actions) == 3
        assert all(isinstance(a, Twist) for a in out.actions)
        # last action is a stop
        assert out.actions[-1].linear_x == 0.0
        assert out.actions[-1].angular_z == 0.0

    def test_wall_avoid_turns_in_place(self) -> None:
        agent = self._agent()
        out = agent.run(
            VisionLanguageRequest(
                instruction="avoid the wall",
                observation="turtle at (0, 0), wall at (0.5, 0)",
            )
        )
        # turn-in-place: linear_x stays zero
        assert all(a.linear_x == 0.0 for a in out.actions)
        # at least one action with nonzero angular_z
        assert any(a.angular_z != 0.0 for a in out.actions)

    def test_no_target_yields_idle_action(self) -> None:
        agent = self._agent()
        out = agent.run(
            VisionLanguageRequest(
                instruction="go to the moon",  # no such object in scene
                observation="turtle at (0, 0)",
            )
        )
        assert len(out.actions) == 1
        assert out.actions[0].linear_x == 0.0
        assert out.confidence is not None and out.confidence <= 0.5

    def test_no_self_object_yields_idle(self) -> None:
        agent = self._agent()
        out = agent.run(
            VisionLanguageRequest(
                instruction="go anywhere",
                observation="red_flag at (1, 1)",  # no turtle in scene
            )
        )
        assert out.confidence == 0.0
        assert "no self" in out.notes


# ---------------------------------------------------------------------------
# Scene conditionality (headline assertion)
# ---------------------------------------------------------------------------


class TestSceneConditional:
    def test_same_instruction_different_scenes_yield_different_actions(self) -> None:
        agent = MockVLAAgent(AgentConfig(name="vla.mock", model="mock"))
        instruction = "go to the red flag"
        scene_a = "turtle at (0, 0), red_flag at (5, 0)"
        scene_b = "turtle at (0, 0), red_flag at (0, 5)"
        out_a = agent.run(
            VisionLanguageRequest(instruction=instruction, observation=scene_a)
        )
        out_b = agent.run(
            VisionLanguageRequest(instruction=instruction, observation=scene_b)
        )
        # Different scene → different yaw bearing in the first action
        align_a = out_a.actions[0]
        align_b = out_b.actions[0]
        assert align_a.angular_z != align_b.angular_z

    def test_same_scene_different_instructions_yield_different_actions(self) -> None:
        agent = MockVLAAgent(AgentConfig(name="vla.mock", model="mock"))
        scene = "turtle at (0, 0), red_flag at (3, 0), wall at (0.5, 0)"
        target_request = VisionLanguageRequest(
            instruction="go to the red flag", observation=scene
        )
        avoid_request = VisionLanguageRequest(
            instruction="avoid the wall", observation=scene
        )
        out_target = agent.run(target_request)
        out_avoid = agent.run(avoid_request)
        # avoid path turns in place — no positive linear_x
        assert any(a.linear_x > 0 for a in out_target.actions)
        assert all(a.linear_x == 0.0 for a in out_avoid.actions)


# ---------------------------------------------------------------------------
# Determinism (acceptance criterion: replayable)
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_repeated_runs_same_output(self) -> None:
        agent = MockVLAAgent(AgentConfig(name="vla.mock", model="mock"))
        req = VisionLanguageRequest(
            instruction="go to the red flag",
            observation="turtle at (0, 0), red_flag at (3, 4)",
        )
        a = agent.run(req)
        b = agent.run(req)
        assert isinstance(a, ActionStream)
        assert a.actions == b.actions
        assert a.notes == b.notes


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


class TestMetrics:
    def test_evaluate_basic_aggregate(self) -> None:
        outcomes = [
            EpisodeOutcome(succeeded=True, intervened=False, n_steps=5),
            EpisodeOutcome(succeeded=False, intervened=True, n_steps=12),
            EpisodeOutcome(succeeded=True, intervened=False, n_steps=4),
        ]
        report = evaluate_trials(outcomes)
        assert report.n_episodes == 3
        assert report.success_rate == pytest.approx(2 / 3)
        assert report.intervention_rate == pytest.approx(1 / 3)
        assert report.mean_steps == pytest.approx((5 + 12 + 4) / 3)

    def test_empty_input_returns_zeros(self) -> None:
        report = evaluate_trials([])
        assert report.n_episodes == 0
        assert report.success_rate == 0.0
        assert report.intervention_rate == 0.0
        assert report.mean_steps == 0.0
        assert report.per_episode == ()


# ---------------------------------------------------------------------------
# ABC
# ---------------------------------------------------------------------------


class TestVLAAgentABC:
    def test_cannot_instantiate_directly(self) -> None:
        with pytest.raises(TypeError):
            VLAAgent(AgentConfig(name="x"))  # type: ignore[abstract]
