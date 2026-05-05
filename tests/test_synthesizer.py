"""Tests for LocalSynthesizer — consensus, empty list, all-failure cases."""
import pytest
from llmesh.orchestrator import LocalSynthesizer
from llmesh.orchestrator.synthesizer import SynthesisError

VALID_TASK_ID = "12345678-1234-4234-89ab-123456789abc"
NONCE = "a" * 32
SHA256 = "b" * 64


def _code_output(code: str = "def foo(): pass", task_id: str = VALID_TASK_ID) -> dict:
    return {
        "task_id": task_id,
        "code": code,
        "language": "python",
        "explanation": "test",
        "dependencies_added": [],
        "generated_files": [],
        "cve_scan_requested": False,
        "caller_nonce_echo": NONCE,
    }


def _critique_output(overall: float = 0.8, task_id: str = VALID_TASK_ID) -> dict:
    return {
        "task_id": task_id,
        "scores": {"overall": overall},
        "findings": [],
        "candidate_output_sha256_echo": SHA256,
        "caller_nonce_echo": NONCE,
    }


class TestSynthesizeCodeConsensus:
    def test_single_output_returns_it(self):
        s = LocalSynthesizer()
        out = _code_output("def foo(): pass")
        result = s.synthesize([out], "generate_code")
        assert result["code"] == "def foo(): pass"

    def test_majority_code_wins(self):
        s = LocalSynthesizer(min_votes=2)
        code_a = _code_output("def foo(): pass")
        code_b = _code_output("def foo(): pass")
        code_c = _code_output("def bar(): pass")  # minority
        result = s.synthesize([code_a, code_b, code_c], "generate_code")
        assert result["code"] == "def foo(): pass"

    def test_all_identical_codes_pass(self):
        s = LocalSynthesizer(min_votes=3)
        outputs = [_code_output("x = 1") for _ in range(3)]
        result = s.synthesize(outputs, "generate_code")
        assert result["code"] == "x = 1"


class TestSynthesizeCritiqueConsensus:
    def test_majority_score_bucket_wins(self):
        s = LocalSynthesizer(min_votes=2)
        # Two outputs with overall=0.8 (bucket "0.8"), one with 0.3
        outputs = [
            _critique_output(0.8),
            _critique_output(0.8),
            _critique_output(0.3),
        ]
        result = s.synthesize(outputs, "critique_output")
        assert result["scores"]["overall"] == 0.8

    def test_score_rounding_groups_similar_scores(self):
        """0.75 and 0.79 both round to 0.8 → same bucket."""
        s = LocalSynthesizer(min_votes=2)
        outputs = [
            _critique_output(0.75),
            _critique_output(0.79),
            _critique_output(0.1),
        ]
        result = s.synthesize(outputs, "critique_output")
        # Winner should be from the 0.8 bucket (0.75 or 0.79)
        assert round(result["scores"]["overall"], 1) == 0.8


class TestSynthesizeEmptyList:
    def test_empty_list_raises_synthesis_error(self):
        s = LocalSynthesizer()
        with pytest.raises(SynthesisError, match="no_outputs"):
            s.synthesize([], "generate_code")


class TestSynthesizeNoConsensus:
    def test_all_different_below_min_votes_raises(self):
        s = LocalSynthesizer(min_votes=2)
        # Three distinct codes — no pair matches
        outputs = [
            _code_output("def a(): pass"),
            _code_output("def b(): pass"),
            _code_output("def c(): pass"),
        ]
        with pytest.raises(SynthesisError, match="no_consensus"):
            s.synthesize(outputs, "generate_code")

    def test_single_output_fails_when_min_votes_2(self):
        s = LocalSynthesizer(min_votes=2)
        with pytest.raises(SynthesisError, match="no_consensus"):
            s.synthesize([_code_output()], "generate_code")


class TestSynthesizerInit:
    def test_min_votes_zero_raises(self):
        with pytest.raises(ValueError):
            LocalSynthesizer(min_votes=0)

    def test_min_votes_negative_raises(self):
        with pytest.raises(ValueError):
            LocalSynthesizer(min_votes=-1)


class TestGenerateTests:
    def test_generate_tests_tool_name(self):
        s = LocalSynthesizer()
        out = {
            "task_id": VALID_TASK_ID,
            "tests_code": "def test_x(): assert True",
            "test_framework": "pytest",
            "test_count": 1,
            "dependencies_added": [],
            "generated_files": [],
            "caller_nonce_echo": NONCE,
        }
        result = s.synthesize([out], "generate_tests")
        assert result["tests_code"] == "def test_x(): assert True"
