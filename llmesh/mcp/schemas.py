"""MCP tool output schemas for LLMesh Code Development Subnet."""
from __future__ import annotations

_GENERATED_FILE_ITEM = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "maxLength": 512},
        "sha256": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
        "requires_script_audit": {"type": "boolean"},
    },
    "required": ["path", "sha256", "requires_script_audit"],
    "additionalProperties": False,
}

_NONCE_PATTERN = "^[a-f0-9]{32}$"
_UUID4_PATTERN = "^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
_SHA256_PATTERN = "^[a-f0-9]{64}$"
_LANGUAGES = ["python", "c", "cpp", "rust", "typescript", "go", "java"]

# task_id field shared by all tool schemas
_TASK_ID_FIELD = {"type": "string", "pattern": _UUID4_PATTERN}

TOOL_SCHEMAS: dict[str, dict] = {
    "generate_code": {
        "type": "object",
        "properties": {
            "task_id":            _TASK_ID_FIELD,
            "code":               {"type": "string", "maxLength": 32768},
            "language":           {"type": "string", "enum": _LANGUAGES},
            "explanation":        {"type": "string", "maxLength": 2048},
            "dependencies_added": {"type": "array", "items": {"type": "string"}, "maxItems": 50},
            "generated_files":    {"type": "array", "items": _GENERATED_FILE_ITEM},
            "cve_scan_requested": {"type": "boolean"},
            "package_json_scripts_audit": {"type": "boolean"},
            "caller_nonce_echo":  {"type": "string", "pattern": _NONCE_PATTERN},
        },
        "required": ["task_id", "code", "language", "explanation", "dependencies_added",
                     "generated_files", "cve_scan_requested", "caller_nonce_echo"],
        "additionalProperties": False,
    },
    "review_code": {
        "type": "object",
        "properties": {
            "task_id": _TASK_ID_FIELD,
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "severity":       {"type": "string", "enum": ["critical","high","medium","low","info"]},
                        "cwe_id":         {"type": "string"},
                        "line_range":     {"type": "string"},
                        "description":    {"type": "string", "maxLength": 2048},
                        "recommendation": {"type": "string", "maxLength": 2048},
                    },
                    "required": ["severity", "description", "recommendation"],
                    "additionalProperties": False,
                },
            },
            "code_sha256_echo":  {"type": "string", "pattern": _SHA256_PATTERN},
            "caller_nonce_echo": {"type": "string", "pattern": _NONCE_PATTERN},
        },
        "required": ["task_id", "findings", "code_sha256_echo", "caller_nonce_echo"],
        "additionalProperties": False,
    },
    "generate_tests": {
        "type": "object",
        "properties": {
            "task_id":            _TASK_ID_FIELD,
            "tests_code":         {"type": "string", "maxLength": 32768},
            "test_framework":     {"type": "string"},
            "test_count":         {"type": "integer", "minimum": 0},
            "coverage_estimate":  {"type": "number", "minimum": 0, "maximum": 1},
            "dependencies_added": {"type": "array", "items": {"type": "string"}, "maxItems": 50},
            "generated_files":    {"type": "array", "items": _GENERATED_FILE_ITEM},
            "caller_nonce_echo":  {"type": "string", "pattern": _NONCE_PATTERN},
        },
        "required": ["task_id", "tests_code", "test_framework", "test_count",
                     "dependencies_added", "generated_files", "caller_nonce_echo"],
        "additionalProperties": False,
    },
    "critique_output": {
        "type": "object",
        "properties": {
            "task_id": _TASK_ID_FIELD,
            "scores": {
                "type": "object",
                "properties": {
                    "correctness":    {"type": "number", "minimum": 0, "maximum": 1},
                    "security":       {"type": "number", "minimum": 0, "maximum": 1},
                    "testability":    {"type": "number", "minimum": 0, "maximum": 1},
                    "maintainability":{"type": "number", "minimum": 0, "maximum": 1},
                    "overall":        {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": ["overall"],
                "additionalProperties": False,
            },
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "severity":    {"type": "string", "enum": ["critical","high","medium","low","info"]},
                        "description": {"type": "string", "maxLength": 1024},
                    },
                    "required": ["severity", "description"],
                    "additionalProperties": False,
                },
            },
            "candidate_output_sha256_echo": {"type": "string", "pattern": _SHA256_PATTERN},
            "caller_nonce_echo":            {"type": "string", "pattern": _NONCE_PATTERN},
        },
        "required": ["task_id", "scores", "findings", "candidate_output_sha256_echo", "caller_nonce_echo"],
        "additionalProperties": False,
    },
}
