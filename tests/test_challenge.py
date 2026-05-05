"""Tests for llmesh.challenge — bank, evaluator, protocol."""
from __future__ import annotations

import time
import uuid
from typing import Any

import pytest

from llmesh.challenge.bank import TASK_BANK, TASK_BY_ID, ChallengeTask, Difficulty, TaskType
from llmesh.challenge.evaluator import ChallengeEvaluator, ChallengeResult
from llmesh.challenge.protocol import ChallengeProtocol, ChallengeToken, ProtocolError


# ---------------------------------------------------------------------------
# Bank
# ---------------------------------------------------------------------------

class TestTaskBank:
    def test_exactly_20_tasks(self):
        assert len(TASK_BANK) == 20

    def test_all_ids_unique(self):
        ids = [t.id for t in TASK_BANK]
        assert len(ids) == len(set(ids))

    def test_task_by_id_covers_all(self):
        assert set(TASK_BY_ID.keys()) == {t.id for t in TASK_BANK}

    def test_all_difficulties_present(self):
        diffs = {t.difficulty for t in TASK_BANK}
        assert Difficulty.EASY in diffs
        assert Difficulty.MEDIUM in diffs
        assert Difficulty.HARD in diffs

    def test_all_task_types_present(self):
        types = {t.task_type for t in TASK_BANK}
        assert TaskType.CODE_GEN in types
        assert TaskType.CODE_REVIEW in types
        assert TaskType.TEST_GEN in types
        assert TaskType.CRITIQUE in types

    def test_easy_tasks_have_syntax_check_for_python_code_gen(self):
        for t in TASK_BANK:
            if t.task_type == TaskType.CODE_GEN and t.language == "python":
                assert t.requires_syntax_check

    def test_all_prompts_non_empty(self):
        for t in TASK_BANK:
            assert len(t.prompt.strip()) > 0


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------

def _code_gen_response(
    code: str,
    task_id: str = "cg-easy-01",
    nonce: str = "a" * 32,
) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "code": code,
        "language": "python",
        "explanation": "test",
        "dependencies_added": [],
        "generated_files": [],
        "cve_scan_requested": False,
        "caller_nonce_echo": nonce,
    }


class TestChallengeEvaluator:
    def setup_method(self):
        self.ev = ChallengeEvaluator(pass_threshold=0.6)
        self.task = TASK_BY_ID["cg-easy-01"]  # add(a,b), requires syntax check

    def test_perfect_response_passes(self):
        code = "def add(a, b):\n    return a + b\n"
        result = self.ev.evaluate(self.task, _code_gen_response(code))
        assert result.passed
        assert result.score >= 0.6

    def test_empty_code_fails(self):
        result = self.ev.evaluate(self.task, _code_gen_response(""))
        assert not result.passed
        assert result.score < 0.6

    def test_syntax_error_reduces_score(self):
        code = "def add(a b):\n    return a + b"  # missing comma
        result = self.ev.evaluate(self.task, _code_gen_response(code))
        assert result.syntax_ok is False

    def test_valid_syntax_sets_syntax_ok_true(self):
        code = "def add(a, b):\n    return a + b\n"
        result = self.ev.evaluate(self.task, _code_gen_response(code))
        assert result.syntax_ok is True

    def test_syntax_ok_none_for_non_syntax_task(self):
        task = TASK_BY_ID["cr-easy-01"]  # code_review, no syntax check
        response = {
            "task_id": task.id,
            "findings": [{"severity": "high",
                          "description": "shell=True allows command injection",
                          "recommendation": "use list args and avoid shell=True"}],
            "code_sha256_echo": "a" * 64,
            "caller_nonce_echo": "b" * 32,
        }
        result = self.ev.evaluate(task, response)
        assert result.syntax_ok is None

    def test_keyword_score_partial(self):
        # Only 'def add' present, 'return' missing
        code = "def add(a, b):\n    pass\n" + "x" * 30
        result = self.ev.evaluate(self.task, _code_gen_response(code))
        assert 0.0 < result.keyword_score < 1.0

    def test_keyword_score_full(self):
        code = "def add(a, b):\n    return a + b\n"
        result = self.ev.evaluate(self.task, _code_gen_response(code))
        assert result.keyword_score == 1.0

    def test_length_ok_false_when_short(self):
        code = "x"  # way too short (min_code_length=20)
        result = self.ev.evaluate(self.task, _code_gen_response(code))
        assert not result.length_ok

    def test_result_has_feedback_on_failure(self):
        result = self.ev.evaluate(self.task, _code_gen_response(""))
        assert len(result.feedback) > 0

    def test_result_to_dict_has_required_keys(self):
        code = "def add(a, b):\n    return a + b\n"
        result = self.ev.evaluate(self.task, _code_gen_response(code))
        d = result.to_dict()
        assert set(d.keys()) >= {"task_id", "score", "passed", "keyword_score",
                                   "syntax_ok", "length_ok", "feedback"}

    def test_invalid_pass_threshold_raises(self):
        with pytest.raises(ValueError):
            ChallengeEvaluator(pass_threshold=1.5)

    def test_code_review_task_evaluates_findings_text(self):
        task = TASK_BY_ID["cr-easy-01"]
        response = {
            "task_id": task.id,
            "findings": [
                {
                    "severity": "high",
                    "description": "shell=True enables command injection",
                    "recommendation": "Remove shell=True and use list arguments",
                }
            ],
            "code_sha256_echo": "a" * 64,
            "caller_nonce_echo": "b" * 32,
        }
        result = self.ev.evaluate(task, response)
        assert result.keyword_score > 0.0

    def test_test_gen_task_evaluates_tests_code(self):
        task = TASK_BY_ID["tg-med-01"]
        tests_code = (
            "def test_fizz():\n    assert fizzbuzz(3) == 'Fizz'\n"
            "def test_buzz():\n    assert fizzbuzz(5) == 'Buzz'\n"
            "def test_fizzbuzz():\n    assert fizzbuzz(15) == 'FizzBuzz'\n"
        )
        response = {
            "task_id": task.id,
            "tests_code": tests_code,
            "test_framework": "pytest",
            "test_count": 3,
            "coverage_estimate": 0.9,
            "dependencies_added": [],
            "generated_files": [],
            "caller_nonce_echo": "b" * 32,
        }
        result = self.ev.evaluate(task, response)
        assert result.passed


# ---------------------------------------------------------------------------
# Protocol — issue / get_task / verify
# ---------------------------------------------------------------------------

class TestChallengeProtocol:
    def setup_method(self):
        self.proto = ChallengeProtocol(secret_key=b"test-secret-key-32bytes-padding!!")

    def _good_response(self, task_id: str) -> dict[str, Any]:
        task = TASK_BY_ID[task_id]
        if task.task_type == TaskType.CODE_GEN:
            code = "\n".join(f"# {kw}\ndef stub(): return 0\n" + "x" * 50
                             for kw in task.expected_keywords)
            # Build a realistic passing response with all keywords present
            code = "\n".join(task.expected_keywords) + "\n" + "x" * 60
            return {
                "task_id": task_id,
                "code": code,
                "language": task.language,
                "explanation": "stub",
                "dependencies_added": [],
                "generated_files": [],
                "cve_scan_requested": False,
                "caller_nonce_echo": "b" * 32,
            }
        return {"task_id": task_id, "findings": [], "caller_nonce_echo": "b" * 32}

    def test_issue_returns_token(self):
        token = self.proto.issue(Difficulty.EASY)
        assert isinstance(token, ChallengeToken)
        assert token.task_id in TASK_BY_ID

    def test_issue_with_specific_task_id(self):
        token = self.proto.issue(task_id="cg-easy-01")
        assert token.task_id == "cg-easy-01"

    def test_issue_unknown_task_id_raises(self):
        with pytest.raises(ProtocolError, match="unknown task_id"):
            self.proto.issue(task_id="nonexistent")

    def test_get_task_returns_correct_task(self):
        token = self.proto.issue(task_id="cg-easy-02")
        task = self.proto.get_task(token)
        assert task.id == "cg-easy-02"

    def test_verify_valid_response_returns_result(self):
        token = self.proto.issue(task_id="cg-easy-01")
        resp = self._good_response("cg-easy-01")
        result = self.proto.verify(token, resp)
        assert isinstance(result, ChallengeResult)

    def test_verify_marks_token_as_used(self):
        token = self.proto.issue(task_id="cg-easy-01")
        resp = self._good_response("cg-easy-01")
        self.proto.verify(token, resp)
        with pytest.raises(ProtocolError, match="token_replayed"):
            self.proto.verify(token, resp)

    def test_verify_tampered_hmac_raises(self):
        token = self.proto.issue(task_id="cg-easy-01")
        token.hmac_sig = "0" * 64  # tampered
        with pytest.raises(ProtocolError, match="token_signature_invalid"):
            self.proto.verify(token, self._good_response("cg-easy-01"))

    def test_verify_expired_token_raises(self):
        proto = ChallengeProtocol(
            secret_key=b"test-secret-key-32bytes-padding!!",
            ttl_seconds=0,
        )
        token = proto.issue(task_id="cg-easy-01")
        time.sleep(0.01)  # ensure expiry
        with pytest.raises(ProtocolError, match="token_expired"):
            proto.verify(token, self._good_response("cg-easy-01"))

    def test_tampered_task_id_raises(self):
        token = self.proto.issue(task_id="cg-easy-01")
        original_sig = token.hmac_sig
        token.task_id = "cg-hard-01"
        # sig is now for cg-easy-01 but token says cg-hard-01 → HMAC mismatch
        with pytest.raises(ProtocolError, match="token_signature_invalid"):
            self.proto.verify(token, self._good_response("cg-hard-01"))

    def test_from_dict_roundtrip(self):
        token = self.proto.issue(task_id="cg-easy-03")
        token2 = ChallengeToken.from_dict(token.to_dict())
        assert token2.token_id == token.token_id
        assert token2.hmac_sig == token.hmac_sig

    def test_different_keys_cannot_verify_each_others_tokens(self):
        proto2 = ChallengeProtocol(secret_key=b"completely-different-key-padded!!")
        token = self.proto.issue(task_id="cg-easy-01")
        with pytest.raises(ProtocolError, match="token_signature_invalid"):
            proto2.verify(token, self._good_response("cg-easy-01"))

    def test_issue_hard_difficulty(self):
        token = self.proto.issue(Difficulty.HARD)
        task = TASK_BY_ID[token.task_id]
        assert task.difficulty == Difficulty.HARD

    def test_issue_medium_difficulty(self):
        token = self.proto.issue(Difficulty.MEDIUM)
        task = TASK_BY_ID[token.task_id]
        assert task.difficulty == Difficulty.MEDIUM
