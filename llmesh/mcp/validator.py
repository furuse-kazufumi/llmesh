"""OutputValidator — boundary gate before Local Synthesizer.

Checks (in order):
1. JSON-only parse  — no YAML/pickle/eval
2. Schema validation against tool output_schema
3. caller_nonce_echo matches expected nonce
4. Output size within maxLength budget
5. No extra fields (additionalProperties: false enforced by schema)
6. task_id UUID v4 validation (both regex via schema and uuid library)
7. SCA gate — OSV CVE check for dependencies_added (CRITICAL/HIGH → blocked)

All exceptions → ValidationError (fail-closed).
"""
from __future__ import annotations

import hashlib
import json
import uuid
from typing import TYPE_CHECKING, Any

import jsonschema

from .schemas import TOOL_SCHEMAS
from .sca_gate import OsvQueryError, check_dependencies, BLOCKING_SEVERITIES

if TYPE_CHECKING:
    from .nonce_store import NonceStore
    from ..audit import AuditTrace

_MAX_RAW_BYTES = 512_000  # 512 KB hard cap before JSON parse


class ValidationError(Exception):
    """Raised when OutputValidator rejects a response."""

    def __init__(self, reason: str, node_id: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.node_id = node_id


class OutputValidator:
    """Validate remote MCP tool responses before synthesis."""

    def __init__(
        self,
        nonce_store: "NonceStore | None" = None,
        audit_trace: "AuditTrace | None" = None,
    ) -> None:
        """Create an OutputValidator.

        Args:
            nonce_store:  Optional NonceStore for server-side replay detection.
            audit_trace:  Optional AuditTrace — logs ``output_validated`` on
                          success and ``output_rejected`` on failure.
        """
        self._nonce_store = nonce_store
        self._audit = audit_trace

    def validate(
        self,
        raw: str | bytes,
        tool_name: str,
        caller_nonce: str,
        node_id: str = "",
        task_id: str = "",
    ) -> dict[str, Any]:
        """Parse and validate a remote node response.

        Returns the parsed dict on success.
        Raises ValidationError on any failure (fail-closed).
        """
        raw_bytes = raw.encode() if isinstance(raw, str) else raw
        try:
            result = self._validate(raw, tool_name, caller_nonce, node_id, task_id)
        except ValidationError:
            if self._audit is not None:
                self._audit.log(
                    event_type="output_rejected",
                    node_id=node_id,
                    task_id=task_id,
                    policy_decision="BLOCK",
                    output_sha256=hashlib.sha256(raw_bytes).hexdigest(),
                )
            raise
        except Exception as exc:
            if self._audit is not None:
                self._audit.log(
                    event_type="output_rejected",
                    node_id=node_id,
                    task_id=task_id,
                    policy_decision="BLOCK",
                    output_sha256=hashlib.sha256(raw_bytes).hexdigest(),
                )
            raise ValidationError(f"unexpected_error:{exc}", node_id) from exc

        if self._audit is not None:
            self._audit.log(
                event_type="output_validated",
                node_id=node_id,
                task_id=task_id,
                policy_decision="ALLOW",
                output_sha256=hashlib.sha256(raw_bytes).hexdigest(),
            )
        return result

    def _validate(
        self,
        raw: str | bytes,
        tool_name: str,
        caller_nonce: str,
        node_id: str,
        task_id: str,
    ) -> dict[str, Any]:
        # 1. Size guard
        raw_bytes = raw.encode() if isinstance(raw, str) else raw
        if len(raw_bytes) > _MAX_RAW_BYTES:
            raise ValidationError("output_too_large", node_id)

        # 2. JSON-only parse
        try:
            data: dict[str, Any] = json.loads(raw_bytes)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValidationError(f"json_parse_error:{exc}", node_id) from exc

        if not isinstance(data, dict):
            raise ValidationError("output_not_an_object", node_id)

        # 3. Schema validation (includes task_id regex pattern check)
        schema = TOOL_SCHEMAS.get(tool_name)
        if schema is None:
            raise ValidationError(f"unknown_tool:{tool_name}", node_id)
        try:
            jsonschema.validate(data, schema)
        except jsonschema.ValidationError as exc:
            raise ValidationError(f"schema_violation:{exc.message}", node_id) from exc

        # 4. Nonce echo check
        echo = data.get("caller_nonce_echo", "")
        if echo != caller_nonce:
            raise ValidationError(
                f"nonce_mismatch:expected={caller_nonce} got={echo}", node_id
            )

        # 5. task_id UUID v4 validation — both caller-provided and in-payload
        #    Validate caller-supplied task_id parameter
        if task_id:
            self._validate_uuid4(task_id, "caller_task_id", node_id)
        #    Validate task_id embedded in the response payload
        payload_task_id = data.get("task_id", "")
        if payload_task_id:
            self._validate_uuid4(payload_task_id, "payload_task_id", node_id)
        #    If caller supplied task_id, verify it matches the payload
        if task_id and payload_task_id and task_id != payload_task_id:
            raise ValidationError(
                f"task_id_mismatch:expected={task_id} got={payload_task_id}", node_id
            )

        # 6. Server-side nonce replay check (optional, requires NonceStore)
        if self._nonce_store is not None and node_id and echo:
            fresh = self._nonce_store.check_and_store(node_id, echo)
            if not fresh:
                raise ValidationError(
                    f"replay_attack_detected:node_id={node_id} nonce={echo}", node_id
                )

        # 7. SCA gate — block CRITICAL/HIGH CVEs in dependencies_added
        deps = data.get("dependencies_added") or []
        if deps:
            language = data.get("language", "")
            framework = data.get("test_framework", "")
            try:
                hits = check_dependencies(deps, language, framework=framework)
            except OsvQueryError as exc:
                raise ValidationError(f"sca_network_error:{exc}", node_id) from exc
            blocking = [h for h in hits if h.severity in BLOCKING_SEVERITIES]
            if blocking:
                detail = ", ".join(f"{h.dep}:{h.vuln_id}({h.severity})" for h in blocking)
                raise ValidationError(f"sca_blocked:{detail}", node_id)

        return data

    @staticmethod
    def _validate_uuid4(value: str, field_name: str, node_id: str) -> None:
        """Validate that *value* is a valid UUID v4 using the uuid library.

        Note: uuid.UUID(val, version=4) silently coerces the version bits and
        never raises for non-v4 strings.  The correct approach is to parse
        without the version kwarg, then assert .version == 4.
        """
        try:
            parsed = uuid.UUID(value)
        except (ValueError, AttributeError) as exc:
            raise ValidationError(
                f"invalid_uuid4:{field_name}:{exc}", node_id
            ) from exc
        if parsed.version != 4:
            raise ValidationError(
                f"invalid_uuid4:{field_name}:version={parsed.version}", node_id
            )
