"""Challenge task bank — 20 benchmark problems for node capability verification.

Each task has:
- A prompt sent to the node under test
- Expected criteria used by ChallengeEvaluator to score the response

Task distribution:
  code_gen   easy   × 6
  code_gen   medium × 5
  code_gen   hard   × 2
  code_review easy  × 3
  code_review medium× 2
  test_gen         × 1
  critique         × 1
  Total: 20
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TaskType(str, Enum):
    CODE_GEN = "code_gen"
    CODE_REVIEW = "code_review"
    TEST_GEN = "test_gen"
    CRITIQUE = "critique"


class Difficulty(str, Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


@dataclass(frozen=True)
class ChallengeTask:
    """A single benchmark problem.

    Attributes:
        id: Unique stable identifier.
        task_type: Category of the task.
        difficulty: Relative difficulty.
        language: Target programming language (relevant for code tasks).
        prompt: The question / instruction shown to the node.
        expected_keywords: Substrings that a correct answer should contain
            (case-insensitive). Used by the evaluator for keyword scoring.
        min_code_length: Minimum number of non-whitespace characters expected
            in the code/answer field. 0 = no minimum.
        requires_syntax_check: If True and language == "python", the evaluator
            runs ast.parse() on the response code field.
        metadata: Extra data for future evaluator extensions.
    """

    id: str
    task_type: TaskType
    difficulty: Difficulty
    language: str
    prompt: str
    expected_keywords: list[str] = field(default_factory=list)
    min_code_length: int = 0
    requires_syntax_check: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Task bank — 20 problems
# ---------------------------------------------------------------------------

TASK_BANK: list[ChallengeTask] = [

    # ── code_gen / easy ────────────────────────────────────────────────────

    ChallengeTask(
        id="cg-easy-01",
        task_type=TaskType.CODE_GEN,
        difficulty=Difficulty.EASY,
        language="python",
        prompt="Write a Python function `add(a, b)` that returns the sum of two numbers.",
        expected_keywords=["def add", "return"],
        min_code_length=20,
        requires_syntax_check=True,
    ),
    ChallengeTask(
        id="cg-easy-02",
        task_type=TaskType.CODE_GEN,
        difficulty=Difficulty.EASY,
        language="python",
        prompt=(
            "Write a Python function `is_even(n)` that returns True if n is even, "
            "False otherwise."
        ),
        expected_keywords=["def is_even", "return"],
        min_code_length=20,
        requires_syntax_check=True,
    ),
    ChallengeTask(
        id="cg-easy-03",
        task_type=TaskType.CODE_GEN,
        difficulty=Difficulty.EASY,
        language="python",
        prompt="Write a Python function `reverse_string(s)` that returns the reversed string.",
        expected_keywords=["def reverse_string", "return"],
        min_code_length=20,
        requires_syntax_check=True,
    ),
    ChallengeTask(
        id="cg-easy-04",
        task_type=TaskType.CODE_GEN,
        difficulty=Difficulty.EASY,
        language="python",
        prompt=(
            "Write a Python function `factorial(n)` that returns n! for non-negative integers."
        ),
        expected_keywords=["def factorial", "return"],
        min_code_length=30,
        requires_syntax_check=True,
    ),
    ChallengeTask(
        id="cg-easy-05",
        task_type=TaskType.CODE_GEN,
        difficulty=Difficulty.EASY,
        language="python",
        prompt=(
            "Write a Python function `max_of_list(lst)` that returns the largest element "
            "in a list without using the built-in max()."
        ),
        expected_keywords=["def max_of_list", "return"],
        min_code_length=40,
        requires_syntax_check=True,
    ),
    ChallengeTask(
        id="cg-easy-06",
        task_type=TaskType.CODE_GEN,
        difficulty=Difficulty.EASY,
        language="python",
        prompt=(
            "Write a Python function `count_vowels(s)` that returns the number of vowels "
            "(a, e, i, o, u — case-insensitive) in the string s."
        ),
        expected_keywords=["def count_vowels", "return"],
        min_code_length=30,
        requires_syntax_check=True,
    ),

    # ── code_gen / medium ──────────────────────────────────────────────────

    ChallengeTask(
        id="cg-med-01",
        task_type=TaskType.CODE_GEN,
        difficulty=Difficulty.MEDIUM,
        language="python",
        prompt=(
            "Write a Python function `binary_search(arr, target)` that returns the index "
            "of target in a sorted list arr, or -1 if not found."
        ),
        expected_keywords=["def binary_search", "return", "mid"],
        min_code_length=80,
        requires_syntax_check=True,
    ),
    ChallengeTask(
        id="cg-med-02",
        task_type=TaskType.CODE_GEN,
        difficulty=Difficulty.MEDIUM,
        language="python",
        prompt=(
            "Write a Python class `Stack` with methods push(item), pop(), peek(), "
            "and is_empty(). Use a list as the internal storage."
        ),
        expected_keywords=["class Stack", "def push", "def pop", "def peek", "def is_empty"],
        min_code_length=100,
        requires_syntax_check=True,
    ),
    ChallengeTask(
        id="cg-med-03",
        task_type=TaskType.CODE_GEN,
        difficulty=Difficulty.MEDIUM,
        language="python",
        prompt=(
            "Write a Python function `flatten(nested)` that flattens an arbitrarily "
            "nested list into a single flat list."
        ),
        expected_keywords=["def flatten", "return"],
        min_code_length=50,
        requires_syntax_check=True,
    ),
    ChallengeTask(
        id="cg-med-04",
        task_type=TaskType.CODE_GEN,
        difficulty=Difficulty.MEDIUM,
        language="python",
        prompt=(
            "Write a Python function `lru_cache_simple(capacity)` that returns a simple "
            "LRU cache object with get(key) and put(key, value) methods."
        ),
        expected_keywords=["def get", "def put", "return"],
        min_code_length=100,
        requires_syntax_check=True,
    ),
    ChallengeTask(
        id="cg-med-05",
        task_type=TaskType.CODE_GEN,
        difficulty=Difficulty.MEDIUM,
        language="python",
        prompt=(
            "Write a Python function `merge_sorted(a, b)` that merges two sorted lists "
            "into a single sorted list without using sort()."
        ),
        expected_keywords=["def merge_sorted", "return"],
        min_code_length=60,
        requires_syntax_check=True,
    ),

    # ── code_gen / hard ────────────────────────────────────────────────────

    ChallengeTask(
        id="cg-hard-01",
        task_type=TaskType.CODE_GEN,
        difficulty=Difficulty.HARD,
        language="python",
        prompt=(
            "Write a Python function `topological_sort(graph)` where graph is a dict "
            "mapping node → list of dependencies. Return a valid topological order or "
            "raise ValueError if the graph has a cycle."
        ),
        expected_keywords=["def topological_sort", "return", "visited"],
        min_code_length=120,
        requires_syntax_check=True,
    ),
    ChallengeTask(
        id="cg-hard-02",
        task_type=TaskType.CODE_GEN,
        difficulty=Difficulty.HARD,
        language="python",
        prompt=(
            "Write a Python async function `rate_limited_fetch(urls, max_concurrent)` "
            "that fetches a list of URLs concurrently using asyncio, limiting to "
            "max_concurrent simultaneous requests. Use asyncio.Semaphore."
        ),
        expected_keywords=["async def", "asyncio", "Semaphore", "await"],
        min_code_length=150,
        requires_syntax_check=True,
    ),

    # ── code_review / easy ─────────────────────────────────────────────────

    ChallengeTask(
        id="cr-easy-01",
        task_type=TaskType.CODE_REVIEW,
        difficulty=Difficulty.EASY,
        language="python",
        prompt=(
            "Review the following Python code for security issues:\n\n"
            "```python\n"
            "import subprocess\n"
            "def run_cmd(user_input):\n"
            "    subprocess.run(user_input, shell=True)\n"
            "```\n\n"
            "Identify the vulnerability and recommend a fix."
        ),
        expected_keywords=["shell=True", "injection", "shell"],
        min_code_length=30,
        requires_syntax_check=False,
    ),
    ChallengeTask(
        id="cr-easy-02",
        task_type=TaskType.CODE_REVIEW,
        difficulty=Difficulty.EASY,
        language="python",
        prompt=(
            "Review the following code for issues:\n\n"
            "```python\n"
            "password = 'hunter2'\n"
            "print(f'Your password is: {password}')\n"
            "```\n\n"
            "What is wrong with this code?"
        ),
        expected_keywords=["password", "hardcoded", "secret"],
        min_code_length=20,
        requires_syntax_check=False,
    ),
    ChallengeTask(
        id="cr-easy-03",
        task_type=TaskType.CODE_REVIEW,
        difficulty=Difficulty.EASY,
        language="python",
        prompt=(
            "Review the following code:\n\n"
            "```python\n"
            "def divide(a, b):\n"
            "    return a / b\n"
            "```\n\n"
            "What edge case is not handled, and how would you fix it?"
        ),
        expected_keywords=["zero", "division", "ZeroDivisionError"],
        min_code_length=20,
        requires_syntax_check=False,
    ),

    # ── code_review / medium ───────────────────────────────────────────────

    ChallengeTask(
        id="cr-med-01",
        task_type=TaskType.CODE_REVIEW,
        difficulty=Difficulty.MEDIUM,
        language="python",
        prompt=(
            "Review the following SQL query builder for vulnerabilities:\n\n"
            "```python\n"
            "def get_user(username):\n"
            "    query = f\"SELECT * FROM users WHERE name = '{username}'\"\n"
            "    return db.execute(query)\n"
            "```\n\n"
            "Identify the vulnerability, its impact, and provide a fix."
        ),
        expected_keywords=["sql injection", "parameterized", "placeholder"],
        min_code_length=50,
        requires_syntax_check=False,
    ),
    ChallengeTask(
        id="cr-med-02",
        task_type=TaskType.CODE_REVIEW,
        difficulty=Difficulty.MEDIUM,
        language="python",
        prompt=(
            "Review the following authentication code:\n\n"
            "```python\n"
            "def check_token(token, expected):\n"
            "    return token == expected\n"
            "```\n\n"
            "What timing vulnerability exists and how should it be fixed?"
        ),
        expected_keywords=["timing", "hmac", "secrets.compare_digest"],
        min_code_length=40,
        requires_syntax_check=False,
    ),

    # ── test_gen ───────────────────────────────────────────────────────────

    ChallengeTask(
        id="tg-med-01",
        task_type=TaskType.TEST_GEN,
        difficulty=Difficulty.MEDIUM,
        language="python",
        prompt=(
            "Write pytest unit tests for the following function:\n\n"
            "```python\n"
            "def fizzbuzz(n):\n"
            "    if n % 15 == 0: return 'FizzBuzz'\n"
            "    if n % 3 == 0: return 'Fizz'\n"
            "    if n % 5 == 0: return 'Buzz'\n"
            "    return str(n)\n"
            "```\n\n"
            "Cover all branches including edge cases."
        ),
        expected_keywords=["def test_", "assert", "fizzbuzz"],
        min_code_length=100,
        requires_syntax_check=True,
    ),

    # ── critique ───────────────────────────────────────────────────────────

    ChallengeTask(
        id="ct-med-01",
        task_type=TaskType.CRITIQUE,
        difficulty=Difficulty.MEDIUM,
        language="python",
        prompt=(
            "Critique the following code solution for correctness, security, "
            "testability, and maintainability. Score each dimension 0.0–1.0.\n\n"
            "```python\n"
            "def parse_config(path):\n"
            "    import yaml\n"
            "    return yaml.load(open(path).read())\n"
            "```"
        ),
        expected_keywords=["yaml", "safe_load", "security"],
        min_code_length=40,
        requires_syntax_check=False,
    ),
]

assert len(TASK_BANK) == 20, f"Expected 20 tasks, got {len(TASK_BANK)}"

# Index for fast lookup
TASK_BY_ID: dict[str, ChallengeTask] = {t.id: t for t in TASK_BANK}
