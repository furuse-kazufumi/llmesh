"""ChallengeEvaluator — scores node responses against ChallengeTask criteria.

Scoring pipeline (each check contributes to a 0.0–1.0 overall score):
  1. Minimum code length check   (weight 0.20)
  2. Keyword presence check       (weight 0.50)
  3. Python AST syntax check      (weight 0.30, only when requires_syntax_check)

If a task has no requires_syntax_check, keyword + length weights are 0.60/0.40.

Security: no eval/exec is used. AST parsing uses ast.parse() read-only.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Any

from .bank import ChallengeTask, TaskType


@dataclass
class ChallengeResult:
    """Evaluation result for a single challenge response."""

    task_id: str
    score: float                    # 0.0–1.0
    passed: bool                    # score >= pass_threshold (default 0.6)
    keyword_score: float            # fraction of expected_keywords found
    syntax_ok: bool | None          # None if syntax check not applicable
    length_ok: bool
    feedback: list[str] = field(default_factory=list)
    raw_response_excerpt: str = ""  # first 200 chars, for logging only

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "score": self.score,
            "passed": self.passed,
            "keyword_score": self.keyword_score,
            "syntax_ok": self.syntax_ok,
            "length_ok": self.length_ok,
            "feedback": self.feedback,
        }


class ChallengeEvaluator:
    """Evaluate node responses against ChallengeTask criteria."""

    def __init__(self, pass_threshold: float = 0.6) -> None:
        if not 0.0 <= pass_threshold <= 1.0:
            raise ValueError(f"pass_threshold must be 0.0–1.0, got {pass_threshold}")
        self._threshold = pass_threshold

    def evaluate(self, task: ChallengeTask, response: dict[str, Any]) -> ChallengeResult:
        """Score a node response dict against the task criteria.

        The response dict should be the validated output of the MCP tool
        (e.g. generate_code result). Relevant fields:
          - "code" or "tests_code" → code content
          - "explanation" or "findings" → text content for review tasks
          - "scores" → dict for critique tasks

        Returns a ChallengeResult with overall score and per-check details.
        """
        feedback: list[str] = []

        # Extract the primary text to evaluate
        text = self._extract_text(task.task_type, response)
        excerpt = text[:200]

        # 1. Length check
        code_len = len(text.replace(" ", "").replace("\n", ""))
        length_ok = code_len >= task.min_code_length
        if not length_ok:
            feedback.append(
                f"Response too short: {code_len} non-whitespace chars "
                f"(minimum {task.min_code_length})"
            )

        # 2. Keyword check
        keyword_score, missing = self._keyword_check(task.expected_keywords, text)
        if missing:
            feedback.append(f"Missing expected keywords: {missing}")

        # 3. Syntax check (Python only, opt-in per task)
        syntax_ok: bool | None = None
        if task.requires_syntax_check and task.language == "python":
            syntax_ok, err = self._python_syntax_check(text)
            if not syntax_ok:
                feedback.append(f"Python syntax error: {err}")

        # Compute weighted score
        score = self._compute_score(length_ok, keyword_score, syntax_ok, task)

        return ChallengeResult(
            task_id=task.id,
            score=round(score, 4),
            passed=score >= self._threshold,
            keyword_score=round(keyword_score, 4),
            syntax_ok=syntax_ok,
            length_ok=length_ok,
            feedback=feedback,
            raw_response_excerpt=excerpt,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_text(task_type: TaskType, response: dict[str, Any]) -> str:
        """Pull the primary evaluable text from the response dict."""
        if task_type == TaskType.CODE_GEN:
            return str(response.get("code", ""))
        if task_type == TaskType.TEST_GEN:
            return str(response.get("tests_code", ""))
        if task_type == TaskType.CODE_REVIEW:
            findings = response.get("findings", [])
            parts = []
            for f in findings:
                parts.append(f.get("description", ""))
                parts.append(f.get("recommendation", ""))
            return " ".join(parts)
        if task_type == TaskType.CRITIQUE:
            # Combine scores + findings text
            parts = [str(response.get("scores", {}))]
            for f in response.get("findings", []):
                parts.append(f.get("description", ""))
            return " ".join(parts)
        return str(response)

    @staticmethod
    def _keyword_check(
        expected: list[str], text: str
    ) -> tuple[float, list[str]]:
        """Return (fraction_found, list_of_missing_keywords)."""
        if not expected:
            return 1.0, []
        lower = text.lower()
        missing = [kw for kw in expected if kw.lower() not in lower]
        found = len(expected) - len(missing)
        return found / len(expected), missing

    @staticmethod
    def _python_syntax_check(code: str) -> tuple[bool, str]:
        """Return (is_valid, error_message). Uses ast.parse — no exec/eval."""
        try:
            ast.parse(code)
            return True, ""
        except SyntaxError as exc:
            return False, str(exc)

    @staticmethod
    def _compute_score(
        length_ok: bool,
        keyword_score: float,
        syntax_ok: bool | None,
        task: ChallengeTask,
    ) -> float:
        """Compute weighted overall score."""
        if syntax_ok is not None:
            # Weights: length 0.20, keywords 0.50, syntax 0.30
            length_part   = 0.20 if length_ok else 0.0
            keyword_part  = 0.50 * keyword_score
            syntax_part   = 0.30 if syntax_ok else 0.0
            return length_part + keyword_part + syntax_part
        else:
            # No syntax check: length 0.40, keywords 0.60
            length_part  = 0.40 if length_ok else 0.0
            keyword_part = 0.60 * keyword_score
            return length_part + keyword_part
