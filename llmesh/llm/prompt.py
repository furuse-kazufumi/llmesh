"""Tool-specific prompt builders for LLMesh MCP tools.

Each builder returns (system_prompt, user_message) tuples.
Prompts instruct the LLM to respond with a strict JSON object
matching the tool's output schema — including task_id and caller_nonce_echo.
"""
from __future__ import annotations

import json
from typing import Any

_COMMON_RULES = """\
Rules:
- Respond with a single JSON object only. No markdown, no prose outside the JSON.
- Include the exact task_id and caller_nonce_echo values from the request.
- All string fields must be under the documented maxLength limits.
- Do not add fields not listed in the schema.
"""


def build_generate_code(body: dict[str, Any]) -> tuple[str, str]:
    nonce = body.get("caller_nonce", "")
    task_id = body.get("task_id", "")
    system = (
        "You are a secure code generation agent. "
        "Generate code that is correct, minimal, and free of known vulnerabilities.\n"
        + _COMMON_RULES
        + f'Output schema: {{"task_id": "{task_id}", "code": "<string>", '
        '"language": "<python|c|cpp|rust|typescript|go|java>", '
        '"explanation": "<string>", "dependencies_added": [], '
        f'"generated_files": [], "cve_scan_requested": false, '
        f'"caller_nonce_echo": "{nonce}"}}'
    )
    user = json.dumps({
        "task_id": body.get("task_id", ""),
        "caller_nonce": body.get("caller_nonce", ""),
        "prompt": body.get("prompt", ""),
        "language": body.get("language", "python"),
    })
    return system, user


def build_review_code(body: dict[str, Any]) -> tuple[str, str]:
    nonce = body.get("caller_nonce", "")
    task_id = body.get("task_id", "")
    sha256 = body.get("code_sha256", "")
    system = (
        "You are a security code reviewer. "
        "Find vulnerabilities, bad practices, and risks.\n"
        + _COMMON_RULES
        + f'Output schema: {{"task_id": "{task_id}", "findings": [], '
        f'"code_sha256_echo": "{sha256}", "caller_nonce_echo": "{nonce}"}}'
    )
    user = json.dumps({
        "task_id": body.get("task_id", ""),
        "caller_nonce": body.get("caller_nonce", ""),
        "code": body.get("code", ""),
        "code_sha256": body.get("code_sha256", ""),
    })
    return system, user


def build_generate_tests(body: dict[str, Any]) -> tuple[str, str]:
    nonce = body.get("caller_nonce", "")
    task_id = body.get("task_id", "")
    system = (
        "You are a test generation agent. "
        "Write thorough unit tests with good coverage.\n"
        + _COMMON_RULES
        + f'Output schema: {{"task_id": "{task_id}", "tests_code": "<string>", '
        '"test_framework": "<string>", "test_count": 0, '
        f'"coverage_estimate": 0.0, "dependencies_added": [], '
        f'"generated_files": [], "caller_nonce_echo": "{nonce}"}}'
    )
    user = json.dumps({
        "task_id": body.get("task_id", ""),
        "caller_nonce": body.get("caller_nonce", ""),
        "code": body.get("code", ""),
        "language": body.get("language", "python"),
    })
    return system, user


def build_critique_output(body: dict[str, Any]) -> tuple[str, str]:
    nonce = body.get("caller_nonce", "")
    task_id = body.get("task_id", "")
    sha256 = body.get("candidate_sha256", "")
    system = (
        "You are a quality critique agent. "
        "Score the candidate output on correctness, security, testability, and maintainability.\n"
        + _COMMON_RULES
        + f'Output schema: {{"task_id": "{task_id}", '
        '"scores": {"correctness": 0.0, "security": 0.0, "testability": 0.0, '
        f'"maintainability": 0.0, "overall": 0.0}}, "findings": [], '
        f'"candidate_output_sha256_echo": "{sha256}", '
        f'"caller_nonce_echo": "{nonce}"}}'
    )
    user = json.dumps({
        "task_id": body.get("task_id", ""),
        "caller_nonce": body.get("caller_nonce", ""),
        "candidate_output": body.get("candidate_output", ""),
        "candidate_sha256": body.get("candidate_sha256", ""),
    })
    return system, user


_BUILDERS = {
    "generate_code": build_generate_code,
    "review_code": build_review_code,
    "generate_tests": build_generate_tests,
    "critique_output": build_critique_output,
}


def build_prompt(tool_name: str, body: dict[str, Any]) -> tuple[str, str]:
    """Return (system_prompt, user_message) for the given tool.

    Raises KeyError if tool_name is not registered.
    """
    return _BUILDERS[tool_name](body)
