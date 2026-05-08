"""Tests for MCP OutputValidator and tool schemas."""
import json
import pytest
from llmesh.mcp import OutputValidator, ValidationError, TOOL_SCHEMAS

V = OutputValidator()
NONCE = "a" * 32
SHA256 = "b" * 64
VALID_TASK_ID = "12345678-1234-4234-89ab-123456789abc"
INVALID_TASK_ID_V1 = "12345678-1234-1234-89ab-123456789abc"  # version=1, not 4
INVALID_TASK_ID_BAD = "not-a-uuid"


def _valid_generate_code(task_id: str = VALID_TASK_ID) -> dict:
    return {
        "task_id": task_id,
        "code": "def retry(): pass",
        "language": "python",
        "explanation": "simple retry",
        "dependencies_added": [],
        "generated_files": [],
        "cve_scan_requested": False,
        "caller_nonce_echo": NONCE,
    }


def _valid_review_code(task_id: str = VALID_TASK_ID) -> dict:
    return {
        "task_id": task_id,
        "findings": [],
        "code_sha256_echo": SHA256,
        "caller_nonce_echo": NONCE,
    }


def _valid_generate_tests(task_id: str = VALID_TASK_ID) -> dict:
    return {
        "task_id": task_id,
        "tests_code": "def test_it(): pass",
        "test_framework": "pytest",
        "test_count": 1,
        "dependencies_added": [],
        "generated_files": [],
        "caller_nonce_echo": NONCE,
    }


def _valid_critique_output(task_id: str = VALID_TASK_ID) -> dict:
    return {
        "task_id": task_id,
        "scores": {"overall": 0.8},
        "findings": [],
        "candidate_output_sha256_echo": SHA256,
        "caller_nonce_echo": NONCE,
    }


class TestValidGenerateCode:
    def test_valid_passes(self):
        raw = json.dumps(_valid_generate_code())
        result = V.validate(raw, "generate_code", NONCE)
        assert result["language"] == "python"

    def test_invalid_language_blocked(self):
        d = _valid_generate_code()
        d["language"] = "cobol"
        with pytest.raises(ValidationError, match="schema_violation"):
            V.validate(json.dumps(d), "generate_code", NONCE)

    def test_extra_field_blocked(self):
        d = _valid_generate_code()
        d["extra_field"] = "bad"
        with pytest.raises(ValidationError, match="schema_violation"):
            V.validate(json.dumps(d), "generate_code", NONCE)

    def test_missing_required_field_blocked(self):
        d = _valid_generate_code()
        del d["code"]
        with pytest.raises(ValidationError, match="schema_violation"):
            V.validate(json.dumps(d), "generate_code", NONCE)

    def test_code_too_long_blocked(self):
        d = _valid_generate_code()
        d["code"] = "x" * 32769
        with pytest.raises(ValidationError, match="schema_violation"):
            V.validate(json.dumps(d), "generate_code", NONCE)


class TestNonceCheck:
    def test_nonce_mismatch_blocked(self):
        d = _valid_generate_code()
        d["caller_nonce_echo"] = "0" * 32
        with pytest.raises(ValidationError, match="nonce_mismatch"):
            V.validate(json.dumps(d), "generate_code", NONCE)

    def test_correct_nonce_passes(self):
        V.validate(json.dumps(_valid_generate_code()), "generate_code", NONCE)


class TestParseGuards:
    def test_non_json_blocked(self):
        with pytest.raises(ValidationError, match="json_parse_error"):
            V.validate("not json at all", "generate_code", NONCE)

    def test_json_array_blocked(self):
        with pytest.raises(ValidationError, match="output_not_an_object"):
            V.validate(json.dumps([1, 2, 3]), "generate_code", NONCE)

    def test_oversized_raw_blocked(self):
        with pytest.raises(ValidationError, match="output_too_large"):
            V.validate("x" * 600_000, "generate_code", NONCE)

    def test_unknown_tool_blocked(self):
        with pytest.raises(ValidationError, match="unknown_tool"):
            V.validate(json.dumps({}), "nonexistent_tool", NONCE)


class TestAllTools:
    def test_review_code_valid(self):
        V.validate(json.dumps(_valid_review_code()), "review_code", NONCE)

    def test_generate_tests_valid(self):
        V.validate(json.dumps(_valid_generate_tests()), "generate_tests", NONCE)

    def test_critique_output_valid(self):
        V.validate(json.dumps(_valid_critique_output()), "critique_output", NONCE)

    def test_all_schemas_defined(self):
        assert set(TOOL_SCHEMAS.keys()) == {
            "generate_code", "review_code", "generate_tests", "critique_output"
        }


class TestFailClosed:
    def test_unexpected_exception_raises_validation_error(self):
        from unittest.mock import patch
        with patch("llmesh.mcp.validator.json.loads", side_effect=MemoryError("oom")):
            with pytest.raises(ValidationError, match="unexpected_error"):
                V.validate("{}", "generate_code", NONCE)


class TestTaskIdValidation:
    """Tests for task_id UUID v4 validation in schemas and OutputValidator."""

    def test_valid_task_id_in_payload_passes(self):
        raw = json.dumps(_valid_generate_code(VALID_TASK_ID))
        result = V.validate(raw, "generate_code", NONCE, task_id=VALID_TASK_ID)
        assert result["task_id"] == VALID_TASK_ID

    def test_missing_task_id_in_payload_blocked_by_schema(self):
        d = _valid_generate_code()
        del d["task_id"]
        with pytest.raises(ValidationError, match="schema_violation"):
            V.validate(json.dumps(d), "generate_code", NONCE)

    def test_invalid_task_id_pattern_blocked_by_schema(self):
        """Non-UUID string must be rejected by JSON schema pattern check."""
        d = _valid_generate_code()
        d["task_id"] = INVALID_TASK_ID_BAD
        with pytest.raises(ValidationError, match="schema_violation"):
            V.validate(json.dumps(d), "generate_code", NONCE)

    def test_non_v4_uuid_blocked_by_uuid_library(self):
        """UUID version != 4 must be rejected by the uuid library check.

        The JSON schema _UUID4_PATTERN regex only checks the structural format.
        A UUID with version nibble != '4' passes the regex but must be caught
        by uuid.UUID(val).version != 4 inside _validate_uuid4.

        We supply the non-v4 value as the caller task_id kwarg (not in the
        payload, so schema is not involved); the validator checks it first.
        """
        # v1 UUID: third group starts with '1' — passes basic UUID regex but version=1
        v1_uuid = "12345678-1234-1234-89ab-123456789abc"
        # Payload carries a valid v4 task_id so schema passes
        d = _valid_generate_code(VALID_TASK_ID)
        # Caller supplies a v1 UUID — must be rejected with invalid_uuid4
        with pytest.raises(ValidationError, match="invalid_uuid4"):
            V.validate(json.dumps(d), "generate_code", NONCE, task_id=v1_uuid)

    def test_task_id_mismatch_between_caller_and_payload_blocked(self):
        other_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        d = _valid_generate_code(VALID_TASK_ID)
        with pytest.raises(ValidationError, match="task_id_mismatch"):
            V.validate(json.dumps(d), "generate_code", NONCE, task_id=other_id)

    def test_task_id_validation_all_tools(self):
        """task_id is required and validated for all four tools."""
        tools_and_builders = [
            ("generate_code", _valid_generate_code),
            ("review_code", _valid_review_code),
            ("generate_tests", _valid_generate_tests),
            ("critique_output", _valid_critique_output),
        ]
        for tool_name, builder in tools_and_builders:
            d = builder(VALID_TASK_ID)
            result = V.validate(json.dumps(d), tool_name, NONCE, task_id=VALID_TASK_ID)
            assert result["task_id"] == VALID_TASK_ID, f"failed for {tool_name}"

    def test_schema_task_id_missing_blocked_all_tools(self):
        """Missing task_id in payload fails schema for every tool."""
        tools_and_builders = [
            ("generate_code", _valid_generate_code),
            ("review_code", _valid_review_code),
            ("generate_tests", _valid_generate_tests),
            ("critique_output", _valid_critique_output),
        ]
        for tool_name, builder in tools_and_builders:
            d = builder()
            del d["task_id"]
            with pytest.raises(ValidationError, match="schema_violation"):
                V.validate(json.dumps(d), tool_name, NONCE)


class TestValidatorAudit:
    """AuditTrace integration — output_validated / output_rejected events."""

    def _make_audit(self, tmp_path):
        from llmesh.audit import AuditTrace
        key = b"test-validator-hmac-32bytes-here"
        path = tmp_path / "val_audit.jsonl"
        return AuditTrace(path, key), path, key

    def test_valid_response_logs_output_validated(self, tmp_path):
        audit, path, key = self._make_audit(tmp_path)
        v = OutputValidator(audit_trace=audit)
        raw = json.dumps(_valid_generate_code())
        v.validate(raw, "generate_code", NONCE, node_id="n1", task_id=VALID_TASK_ID)
        entry = json.loads(path.read_text().strip())
        assert entry["event_type"] == "output_validated"
        assert entry["policy_decision"] == "ALLOW"
        assert entry["node_id"] == "n1"

    def test_invalid_response_logs_output_rejected(self, tmp_path):
        audit, path, key = self._make_audit(tmp_path)
        v = OutputValidator(audit_trace=audit)
        bad = _valid_generate_code()
        bad["caller_nonce_echo"] = "wrong_nonce"
        with pytest.raises(ValidationError):
            v.validate(json.dumps(bad), "generate_code", NONCE, node_id="n2", task_id=VALID_TASK_ID)
        entry = json.loads(path.read_text().strip())
        assert entry["event_type"] == "output_rejected"
        assert entry["policy_decision"] == "BLOCK"

    def test_audit_chain_verifies_after_mixed_events(self, tmp_path):
        from llmesh.audit import AuditTrace
        key = b"test-validator-hmac-32bytes-here"
        path = tmp_path / "val_chain.jsonl"
        audit = AuditTrace(path, key)
        v = OutputValidator(audit_trace=audit)
        v.validate(json.dumps(_valid_generate_code()), "generate_code", NONCE, node_id="n3", task_id=VALID_TASK_ID)
        bad = _valid_generate_code()
        bad["caller_nonce_echo"] = "bad"
        with pytest.raises(ValidationError):
            v.validate(json.dumps(bad), "generate_code", NONCE, node_id="n3", task_id=VALID_TASK_ID)
        assert AuditTrace.verify_chain(path, key) is True
