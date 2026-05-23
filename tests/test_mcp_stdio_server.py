"""Tests for the MCP JSON-RPC 2.0 stdio server (v1.0.0)."""
from __future__ import annotations

import io
import json
import uuid
from unittest.mock import MagicMock


from llmesh.mcp.stdio_server import (
    _handle_initialize,
    _handle_tools_list,
    _handle_tools_call,
    _read_message,
    _write_message,
    run_stdio_server,
)
from llmesh.mcp.schemas import TOOL_SCHEMAS
from llmesh.mcp.validator import ValidationError


# ---------------------------------------------------------------------------
# Transport helpers
# ---------------------------------------------------------------------------

def _frame(msg: dict) -> bytes:
    body = json.dumps(msg).encode()
    return b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body


def _stream(*messages: dict) -> io.BytesIO:
    return io.BytesIO(b"".join(_frame(m) for m in messages))


def _parse_output(buf: io.BytesIO) -> list[dict]:
    buf.seek(0)
    results = []
    while True:
        headers: dict[bytes, bytes] = {}
        while True:
            line = buf.readline()
            if not line:
                return results
            line = line.strip()
            if not line:
                break
            if b":" in line:
                k, _, v = line.partition(b":")
                headers[k.strip().lower()] = v.strip()
        length = int(headers.get(b"content-length", 0))
        if length <= 0:
            return results
        body = buf.read(length)
        if not body:
            return results
        results.append(json.loads(body))
    return results


class TestReadMessage:
    def test_reads_valid_message(self):
        msg = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        stream = io.BytesIO(_frame(msg))
        result = _read_message(stream)
        assert result == msg

    def test_returns_none_on_empty_stream(self):
        result = _read_message(io.BytesIO(b""))
        assert result is None

    def test_returns_none_on_invalid_json(self):
        body = b"not json"
        header = b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n"
        result = _read_message(io.BytesIO(header + body))
        assert result is None

    def test_reads_multiple_messages_sequentially(self):
        m1 = {"id": 1, "method": "ping"}
        m2 = {"id": 2, "method": "tools/list"}
        stream = io.BytesIO(_frame(m1) + _frame(m2))
        assert _read_message(stream) == m1
        assert _read_message(stream) == m2


class TestWriteMessage:
    def test_writes_content_length_header(self):
        buf = io.BytesIO()
        _write_message(buf, {"id": 1, "result": {}})
        buf.seek(0)
        raw = buf.read()
        assert b"Content-Length:" in raw

    def test_roundtrip(self):
        msg = {"jsonrpc": "2.0", "id": 42, "result": {"ok": True}}
        buf = io.BytesIO()
        _write_message(buf, msg)
        buf.seek(0)
        result = _read_message(buf)
        assert result == msg


# ---------------------------------------------------------------------------
# Method handlers
# ---------------------------------------------------------------------------

class TestHandleInitialize:
    def test_returns_protocol_version(self):
        result = _handle_initialize({})
        assert "protocolVersion" in result

    def test_protocol_version_is_2025_06_18(self):
        assert _handle_initialize({})["protocolVersion"] == "2025-06-18"

    def test_returns_server_info(self):
        result = _handle_initialize({})
        assert result["serverInfo"]["name"] == "llmesh"
        assert result["serverInfo"]["version"] == "1.2.0"

    def test_returns_tools_capability(self):
        result = _handle_initialize({})
        assert "tools" in result["capabilities"]


class TestHandleToolsList:
    def test_returns_all_tools(self):
        result = _handle_tools_list({})
        names = {t["name"] for t in result["tools"]}
        assert names == set(TOOL_SCHEMAS.keys())

    def test_each_tool_has_input_schema(self):
        result = _handle_tools_list({})
        for tool in result["tools"]:
            assert "inputSchema" in tool
            assert tool["inputSchema"]["type"] == "object"

    def test_each_tool_has_output_schema(self):
        result = _handle_tools_list({})
        for tool in result["tools"]:
            assert tool["outputSchema"] == TOOL_SCHEMAS[tool["name"]]

    def test_each_tool_has_description(self):
        result = _handle_tools_list({})
        for tool in result["tools"]:
            assert "description" in tool
            assert isinstance(tool["description"], str)


class TestHandleToolsCall:
    def _make_pipeline(self, llm_result: dict | None = None, blocked: bool = False,
                       requires_summary: bool = False, validate_fail: bool = False):
        firewall = MagicMock()
        fw_decision = MagicMock()
        fw_decision.blocked = blocked
        fw_decision.requires_summarization = requires_summary
        fw_decision.reason = "test"
        fw_decision.level = 3
        firewall.classify.return_value = fw_decision

        summarizer = MagicMock()
        sum_result = MagicMock()
        sum_result.summary = "summarized"
        summarizer.summarize_text.return_value = sum_result

        llm = MagicMock()
        if llm_result is None:
            llm_result = {
                "task_id": str(uuid.uuid4()),
                "caller_nonce_echo": "a" * 32,
                "code": "x = 1",
                "language": "python",
                "explanation": "ok",
                "dependencies_added": [],
                "generated_files": [],
                "cve_scan_requested": False,
            }
        llm.invoke.return_value = dict(llm_result)

        validator = MagicMock()
        if validate_fail:
            validator.validate.side_effect = ValidationError("schema_fail")
        else:
            validator.validate.return_value = {"result": "ok"}

        return firewall, summarizer, llm, validator

    def test_unknown_tool_returns_error(self):
        fw, sm, llm, val = self._make_pipeline()
        result = _handle_tools_call({"name": "no_such_tool", "arguments": {}}, fw, sm, llm, val)
        assert result["isError"] is True
        assert "unknown tool" in result["content"][0]["text"]

    def test_blocked_prompt_returns_error(self):
        fw, sm, llm, val = self._make_pipeline(blocked=True)
        result = _handle_tools_call(
            {"name": "generate_code", "arguments": {"prompt": "secret data"}},
            fw, sm, llm, val,
        )
        assert result["isError"] is True
        assert "blocked" in result["content"][0]["text"]

    def test_successful_call_returns_result(self):
        fw, sm, llm, val = self._make_pipeline()
        result = _handle_tools_call(
            {"name": "generate_code", "arguments": {"prompt": "write hello world"}},
            fw, sm, llm, val,
        )
        assert result["isError"] is False
        assert result["content"][0]["type"] == "text"

    def test_successful_call_returns_structured_content(self):
        fw, sm, llm, val = self._make_pipeline()
        result = _handle_tools_call(
            {"name": "generate_code", "arguments": {"prompt": "hi"}},
            fw, sm, llm, val,
        )
        # 2025-06-18: structuredContent mirrors the validated dict and the text block.
        assert result["structuredContent"] == {"result": "ok"}
        assert json.loads(result["content"][0]["text"]) == result["structuredContent"]

    def test_summarization_applied_for_l3(self):
        fw, sm, llm, val = self._make_pipeline(requires_summary=True)
        _handle_tools_call(
            {"name": "generate_code", "arguments": {"prompt": "internal data"}},
            fw, sm, llm, val,
        )
        sm.summarize_text.assert_called_once()
        call_args = llm.invoke.call_args[0][1]
        assert call_args["prompt"] == "summarized"

    def test_validation_error_returns_error_response(self):
        fw, sm, llm, val = self._make_pipeline(validate_fail=True)
        result = _handle_tools_call(
            {"name": "generate_code", "arguments": {"prompt": "code please"}},
            fw, sm, llm, val,
        )
        assert result["isError"] is True
        assert "validation_error" in result["content"][0]["text"]

    def test_empty_prompt_is_forwarded(self):
        fw, sm, llm, val = self._make_pipeline()
        _handle_tools_call(
            {"name": "generate_code", "arguments": {}},
            fw, sm, llm, val,
        )
        fw.classify.assert_called_once()
        args = fw.classify.call_args[0]
        assert args[0] == ""


# ---------------------------------------------------------------------------
# run_stdio_server — integration
# ---------------------------------------------------------------------------

class TestRunStdioServer:
    def _make_pipeline(self):
        fw, sm, llm, val = TestHandleToolsCall()._make_pipeline()
        return fw, sm, llm, val

    def test_initialize_response(self):
        stdin = _stream({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        stdout = io.BytesIO()
        run_stdio_server(stdin, stdout, _pipeline=self._make_pipeline())
        responses = _parse_output(stdout)
        assert any(r.get("id") == 1 for r in responses)
        init_resp = next(r for r in responses if r.get("id") == 1)
        assert "protocolVersion" in init_resp["result"]

    def test_tools_list_response(self):
        stdin = _stream({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        stdout = io.BytesIO()
        run_stdio_server(stdin, stdout, _pipeline=self._make_pipeline())
        responses = _parse_output(stdout)
        resp = next(r for r in responses if r.get("id") == 2)
        assert "tools" in resp["result"]

    def test_ping_response(self):
        stdin = _stream({"jsonrpc": "2.0", "id": 3, "method": "ping", "params": {}})
        stdout = io.BytesIO()
        run_stdio_server(stdin, stdout, _pipeline=self._make_pipeline())
        responses = _parse_output(stdout)
        resp = next(r for r in responses if r.get("id") == 3)
        assert resp["result"] == {}

    def test_unknown_method_returns_error(self):
        stdin = _stream({"jsonrpc": "2.0", "id": 4, "method": "no_such_method", "params": {}})
        stdout = io.BytesIO()
        run_stdio_server(stdin, stdout, _pipeline=self._make_pipeline())
        responses = _parse_output(stdout)
        resp = next(r for r in responses if r.get("id") == 4)
        assert "error" in resp
        assert resp["error"]["code"] == -32601

    def test_notification_no_reply(self):
        # Notifications have no id — server must not reply
        stdin = _stream({"jsonrpc": "2.0", "method": "notifications/initialized"})
        stdout = io.BytesIO()
        run_stdio_server(stdin, stdout, _pipeline=self._make_pipeline())
        responses = _parse_output(stdout)
        assert responses == []

    def test_multiple_requests_in_sequence(self):
        stdin = _stream(
            {"jsonrpc": "2.0", "id": 1, "method": "ping"},
            {"jsonrpc": "2.0", "id": 2, "method": "ping"},
        )
        stdout = io.BytesIO()
        run_stdio_server(stdin, stdout, _pipeline=self._make_pipeline())
        responses = _parse_output(stdout)
        ids = {r["id"] for r in responses}
        assert ids == {1, 2}

    def test_tools_call_success(self):
        stdin = _stream({
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "generate_code", "arguments": {"prompt": "hello"}},
        })
        stdout = io.BytesIO()
        run_stdio_server(stdin, stdout, _pipeline=self._make_pipeline())
        responses = _parse_output(stdout)
        resp = next(r for r in responses if r.get("id") == 5)
        assert "result" in resp
        assert resp["result"]["isError"] is False


# ---------------------------------------------------------------------------
# v1.2.0: image_base64 support in tools/call
# ---------------------------------------------------------------------------

import base64 as _base64
from llmesh.privacy.image_firewall import ImageFirewall, ImageClassification, ImageAction
from llmesh.privacy.image_summarizer import ImageSummarizer, ImageSummary
from llmesh.mcp.stdio_server import _handle_image


def _png_b64() -> str:
    """Minimal PNG bytes encoded as base64."""
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    return _base64.b64encode(png).decode()


def _make_img_pipeline(
    clf_action: ImageAction = ImageAction.ALLOW,
    clf_reason: str = "safe_image",
    summary_blocked: bool = False,
    summary_description: str = "A diagram.",
):
    img_fw = MagicMock(spec=ImageFirewall)
    clf = ImageClassification(
        action=clf_action,
        level=4 if clf_action is ImageAction.BLOCK else (3 if clf_action is ImageAction.SUMMARIZE else 1),
        reason=clf_reason,
        width=100,
        height=100,
        format="PNG",
    )
    img_fw.classify_bytes.return_value = clf

    img_sum = MagicMock(spec=ImageSummarizer)
    img_sum.summarize.return_value = ImageSummary(
        original_level=3,
        summary_level=1,
        description=summary_description,
        blocked=summary_blocked,
        block_reason="captioner_error" if summary_blocked else "",
    )
    return img_fw, img_sum


class TestHandleImage:
    def test_allow_returns_placeholder(self):
        img_fw, img_sum = _make_img_pipeline(clf_action=ImageAction.ALLOW)
        desc, is_err, _ = _handle_image(_png_b64(), img_fw, img_sum)
        assert not is_err
        assert "Image" in desc

    def test_summarize_returns_description(self):
        img_fw, img_sum = _make_img_pipeline(
            clf_action=ImageAction.SUMMARIZE,
            summary_description="A screenshot of a dashboard.",
        )
        desc, is_err, _ = _handle_image(_png_b64(), img_fw, img_sum)
        assert not is_err
        assert "dashboard" in desc

    def test_blocked_image_returns_error(self):
        img_fw, img_sum = _make_img_pipeline(
            clf_action=ImageAction.BLOCK,
            clf_reason="filename_l4_pattern:passport.png",
        )
        _, is_err, err_text = _handle_image(_png_b64(), img_fw, img_sum)
        assert is_err
        assert "image_blocked" in err_text

    def test_invalid_base64_returns_error(self):
        img_fw, img_sum = _make_img_pipeline()
        _, is_err, err_text = _handle_image("!!!not-base64!!!", img_fw, img_sum)
        assert is_err
        assert "base64_decode_error" in err_text

    def test_summarizer_blocked_returns_error(self):
        img_fw, img_sum = _make_img_pipeline(
            clf_action=ImageAction.SUMMARIZE,
            summary_blocked=True,
        )
        _, is_err, err_text = _handle_image(_png_b64(), img_fw, img_sum)
        assert is_err
        assert "summarization_blocked" in err_text


class TestHandleToolsCallWithImage:
    def _pipeline(self):
        return TestHandleToolsCall()._make_pipeline()

    def test_image_description_appended_to_prompt(self):
        fw, sm, llm, val = self._pipeline()
        img_fw, img_sum = _make_img_pipeline(
            clf_action=ImageAction.ALLOW,
            summary_description="[Image: 100x100 PNG]",
        )
        result = _handle_tools_call(
            {"name": "review_code", "arguments": {
                "prompt": "review this",
                "image_base64": _png_b64(),
            }},
            fw, sm, llm, val,
            img_firewall=img_fw,
            img_summarizer=img_sum,
        )
        assert result["isError"] is False
        call_prompt = fw.classify.call_args[0][0]
        assert "review this" in call_prompt

    def test_l4_image_returns_error(self):
        fw, sm, llm, val = self._pipeline()
        img_fw, img_sum = _make_img_pipeline(
            clf_action=ImageAction.BLOCK,
            clf_reason="filename_l4_pattern:selfie.jpg",
        )
        result = _handle_tools_call(
            {"name": "generate_code", "arguments": {
                "prompt": "describe",
                "image_base64": _png_b64(),
            }},
            fw, sm, llm, val,
            img_firewall=img_fw,
            img_summarizer=img_sum,
        )
        assert result["isError"] is True
        assert "image_blocked" in result["content"][0]["text"]

    def test_no_image_works_as_before(self):
        fw, sm, llm, val = self._pipeline()
        result = _handle_tools_call(
            {"name": "generate_code", "arguments": {"prompt": "hello"}},
            fw, sm, llm, val,
        )
        assert result["isError"] is False

    def test_image_only_no_text_prompt(self):
        fw, sm, llm, val = self._pipeline()
        img_fw, img_sum = _make_img_pipeline(
            clf_action=ImageAction.SUMMARIZE,
            summary_description="A flowchart.",
        )
        result = _handle_tools_call(
            {"name": "review_code", "arguments": {
                "prompt": "",
                "image_base64": _png_b64(),
            }},
            fw, sm, llm, val,
            img_firewall=img_fw,
            img_summarizer=img_sum,
        )
        assert result["isError"] is False
        call_prompt = fw.classify.call_args[0][0]
        assert "flowchart" in call_prompt
