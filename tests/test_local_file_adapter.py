"""Tests for LocalFileAdapter — drop-folder LLM task processing (v1.0.1)."""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from llmesh.mcp.validator import ValidationError
from llmesh.protocol.local_file_adapter import (
    LocalFileAdapter,
    _derive_tool_name,
    _safe_stem,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pipeline(
    blocked: bool = False,
    requires_summary: bool = False,
    validate_fail: bool = False,
    backend_error: bool = False,
):
    from llmesh.llm.backend import BackendError

    firewall = MagicMock()
    fw_decision = MagicMock()
    fw_decision.blocked = blocked
    fw_decision.requires_summarization = requires_summary
    fw_decision.reason = "test_blocked"
    fw_decision.level = 3
    firewall.classify.return_value = fw_decision

    summarizer = MagicMock()
    sum_result = MagicMock()
    sum_result.summary = "summarized prompt"
    summarizer.summarize_text.return_value = sum_result

    llm = MagicMock()
    if backend_error:
        llm.invoke.side_effect = BackendError("llm down")
    else:
        llm.invoke.return_value = {
            "task_id": str(uuid.uuid4()),
            "caller_nonce_echo": "a" * 32,
            "code": "print('hello')",
            "language": "python",
            "explanation": "hello world",
            "dependencies_added": [],
            "generated_files": [],
            "cve_scan_requested": False,
        }

    validator = MagicMock()
    if validate_fail:
        validator.validate.side_effect = ValidationError("schema_mismatch")
    else:
        validator.validate.return_value = {"result": "ok", "tool": "generate_code"}

    return firewall, summarizer, llm, validator


def _drop(in_dir: Path, filename: str, content: str = "write hello world") -> Path:
    p = in_dir / filename
    p.write_text(content, encoding="utf-8")
    return p


def _wait_for(path: Path, timeout: float = 3.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if path.exists():
            return True
        time.sleep(0.05)
    return False


# ---------------------------------------------------------------------------
# Unit: helper functions
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_safe_stem_strips_prompt_suffix(self):
        p = Path("hello.prompt.txt")
        assert _safe_stem(p) == "hello"

    def test_safe_stem_compound_name(self):
        p = Path("task.generate_code.prompt.txt")
        assert _safe_stem(p) == "task.generate_code"

    def test_derive_tool_name_default(self):
        assert _derive_tool_name("hello", "generate_code") == "generate_code"

    def test_derive_tool_name_from_stem(self):
        assert _derive_tool_name("task.review_code", "generate_code") == "review_code"

    def test_derive_tool_name_unknown_tool_uses_default(self):
        assert _derive_tool_name("task.no_such_tool", "generate_code") == "generate_code"

    def test_derive_tool_name_no_dot_uses_default(self):
        assert _derive_tool_name("myprompt", "review_code") == "review_code"


# ---------------------------------------------------------------------------
# Unit: constructor / ImportError guard
# ---------------------------------------------------------------------------

class TestConstructor:
    def test_creates_with_defaults(self, tmp_path):
        adapter = LocalFileAdapter(
            in_dir=tmp_path / "in",
            out_dir=tmp_path / "out",
            pipeline=_make_pipeline(),
        )
        assert adapter.protocol_name == "localfile"
        assert adapter.is_running is False

    def test_custom_default_tool(self, tmp_path):
        adapter = LocalFileAdapter(
            in_dir=tmp_path / "in",
            out_dir=tmp_path / "out",
            default_tool="review_code",
            pipeline=_make_pipeline(),
        )
        assert adapter._default_tool == "review_code"


# ---------------------------------------------------------------------------
# Unit: send / broadcast raise TransportError
# ---------------------------------------------------------------------------

class TestSendBroadcastRaise:
    @pytest.mark.asyncio
    async def test_send_raises(self, tmp_path):
        from llmesh.protocol.adapter import TransportError
        adapter = LocalFileAdapter(
            in_dir=tmp_path / "in",
            out_dir=tmp_path / "out",
            pipeline=_make_pipeline(),
        )
        msg = MagicMock()
        target = MagicMock()
        with pytest.raises(TransportError):
            await adapter.send(msg, target)

    @pytest.mark.asyncio
    async def test_broadcast_raises(self, tmp_path):
        from llmesh.protocol.adapter import TransportError
        adapter = LocalFileAdapter(
            in_dir=tmp_path / "in",
            out_dir=tmp_path / "out",
            pipeline=_make_pipeline(),
        )
        with pytest.raises(TransportError):
            await adapter.broadcast(MagicMock())


# ---------------------------------------------------------------------------
# Integration: start / stop
# ---------------------------------------------------------------------------

class TestStartStop:
    @pytest.mark.asyncio
    async def test_start_creates_dirs(self, tmp_path):
        in_dir = tmp_path / "in"
        out_dir = tmp_path / "out"
        adapter = LocalFileAdapter(in_dir=in_dir, out_dir=out_dir, pipeline=_make_pipeline())
        await adapter.start()
        assert in_dir.exists()
        assert out_dir.exists()
        assert (in_dir / "processed").exists()
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_is_running_after_start(self, tmp_path):
        adapter = LocalFileAdapter(
            in_dir=tmp_path / "in",
            out_dir=tmp_path / "out",
            pipeline=_make_pipeline(),
        )
        await adapter.start()
        assert adapter.is_running is True
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_not_running_after_stop(self, tmp_path):
        adapter = LocalFileAdapter(
            in_dir=tmp_path / "in",
            out_dir=tmp_path / "out",
            pipeline=_make_pipeline(),
        )
        await adapter.start()
        await adapter.stop()
        assert adapter.is_running is False


# ---------------------------------------------------------------------------
# Integration: file processing
# ---------------------------------------------------------------------------

class TestFileProcessing:
    @pytest.mark.asyncio
    async def test_prompt_file_produces_result(self, tmp_path):
        in_dir = tmp_path / "in"
        out_dir = tmp_path / "out"
        adapter = LocalFileAdapter(in_dir=in_dir, out_dir=out_dir, pipeline=_make_pipeline())
        await adapter.start()

        _drop(in_dir, "hello.prompt.txt", "write hello world")
        result_path = out_dir / "hello.result.txt"
        assert _wait_for(result_path), "result file not produced in time"

        data = json.loads(result_path.read_text())
        assert data.get("result") == "ok"
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_processed_prompt_is_archived(self, tmp_path):
        in_dir = tmp_path / "in"
        out_dir = tmp_path / "out"
        adapter = LocalFileAdapter(in_dir=in_dir, out_dir=out_dir, pipeline=_make_pipeline())
        await adapter.start()

        prompt_path = _drop(in_dir, "hello.prompt.txt")
        result_path = out_dir / "hello.result.txt"
        _wait_for(result_path)

        archived = in_dir / "processed" / "hello.prompt.txt"
        assert _wait_for(archived), "prompt not archived"
        assert not prompt_path.exists()
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_blocked_prompt_writes_error(self, tmp_path):
        in_dir = tmp_path / "in"
        out_dir = tmp_path / "out"
        adapter = LocalFileAdapter(
            in_dir=in_dir, out_dir=out_dir,
            pipeline=_make_pipeline(blocked=True),
        )
        await adapter.start()

        _drop(in_dir, "secret.prompt.txt", "top secret data")
        result_path = out_dir / "secret.result.txt"
        assert _wait_for(result_path)

        data = json.loads(result_path.read_text())
        assert "error" in data
        assert "blocked" in data["error"]
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_l3_prompt_is_summarized(self, tmp_path):
        in_dir = tmp_path / "in"
        out_dir = tmp_path / "out"
        pipeline = _make_pipeline(requires_summary=True)
        adapter = LocalFileAdapter(in_dir=in_dir, out_dir=out_dir, pipeline=pipeline)
        await adapter.start()

        _drop(in_dir, "internal.prompt.txt", "internal data")
        result_path = out_dir / "internal.result.txt"
        _wait_for(result_path)

        _, summarizer, llm, _ = pipeline
        summarizer.summarize_text.assert_called_once()
        call_body = llm.invoke.call_args[0][1]
        assert call_body["prompt"] == "summarized prompt"
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_backend_error_writes_error_file(self, tmp_path):
        in_dir = tmp_path / "in"
        out_dir = tmp_path / "out"
        adapter = LocalFileAdapter(
            in_dir=in_dir, out_dir=out_dir,
            pipeline=_make_pipeline(backend_error=True),
        )
        await adapter.start()

        _drop(in_dir, "task.prompt.txt", "some prompt")
        result_path = out_dir / "task.result.txt"
        assert _wait_for(result_path)

        data = json.loads(result_path.read_text())
        assert "backend_error" in data["error"]
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_validation_error_writes_error_file(self, tmp_path):
        in_dir = tmp_path / "in"
        out_dir = tmp_path / "out"
        adapter = LocalFileAdapter(
            in_dir=in_dir, out_dir=out_dir,
            pipeline=_make_pipeline(validate_fail=True),
        )
        await adapter.start()

        _drop(in_dir, "task.prompt.txt", "some prompt")
        result_path = out_dir / "task.result.txt"
        assert _wait_for(result_path)

        data = json.loads(result_path.read_text())
        assert "validation_error" in data["error"]
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_non_prompt_file_is_ignored(self, tmp_path):
        in_dir = tmp_path / "in"
        out_dir = tmp_path / "out"
        adapter = LocalFileAdapter(in_dir=in_dir, out_dir=out_dir, pipeline=_make_pipeline())
        await adapter.start()

        (in_dir / "readme.txt").write_text("not a prompt")
        time.sleep(0.3)
        assert not list(out_dir.glob("*.result.txt"))
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_tool_name_derived_from_filename(self, tmp_path):
        in_dir = tmp_path / "in"
        out_dir = tmp_path / "out"
        pipeline = _make_pipeline()
        adapter = LocalFileAdapter(in_dir=in_dir, out_dir=out_dir, pipeline=pipeline)
        await adapter.start()

        _drop(in_dir, "task.review_code.prompt.txt", "check my code")
        result_path = out_dir / "task.review_code.result.txt"
        _wait_for(result_path)

        _, _, llm, _ = pipeline
        called_tool = llm.invoke.call_args[0][0]
        assert called_tool == "review_code"
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_existing_files_processed_at_startup(self, tmp_path):
        in_dir = tmp_path / "in"
        out_dir = tmp_path / "out"
        in_dir.mkdir(parents=True)
        _drop(in_dir, "pre.prompt.txt", "pre-existing prompt")

        adapter = LocalFileAdapter(in_dir=in_dir, out_dir=out_dir, pipeline=_make_pipeline())
        await adapter.start()

        result_path = out_dir / "pre.result.txt"
        assert _wait_for(result_path), "pre-existing file not processed"
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_oversized_prompt_writes_error(self, tmp_path):
        in_dir = tmp_path / "in"
        out_dir = tmp_path / "out"
        adapter = LocalFileAdapter(in_dir=in_dir, out_dir=out_dir, pipeline=_make_pipeline())
        await adapter.start()

        big = "x" * (256 * 1024 + 1)
        _drop(in_dir, "big.prompt.txt", big)
        result_path = out_dir / "big.result.txt"
        assert _wait_for(result_path)

        data = json.loads(result_path.read_text())
        assert "prompt_too_large" in data["error"]
        await adapter.stop()


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class TestRegistry:
    def test_localfile_in_registry(self):
        from llmesh.protocol.registry import AdapterRegistry
        assert "localfile" in AdapterRegistry.available()
