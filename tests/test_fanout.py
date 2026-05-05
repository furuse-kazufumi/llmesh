"""Tests for llmesh.orchestrator.fanout — FanoutExecutor k-of-n execution."""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from llmesh.orchestrator.fanout import FanoutError, FanoutExecutor, FanoutResult, NodeResult
from llmesh.orchestrator.node_client import NodeCallError, NodeClient
from llmesh.orchestrator.synthesizer import LocalSynthesizer
from llmesh.mcp.validator import OutputValidator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@dataclass
class _FakeNode:
    node_id: str
    endpoint: str


def _nonce() -> str:
    return uuid.uuid4().hex[:32]


def _task_id() -> str:
    return str(uuid.uuid4())


def _valid_output(task_id: str, nonce: str, code: str = "def f(): pass") -> dict[str, Any]:
    return {
        "task_id": task_id,
        "code": code,
        "language": "python",
        "explanation": "stub",
        "dependencies_added": [],
        "generated_files": [],
        "cve_scan_requested": False,
        "caller_nonce_echo": nonce,
    }


def _make_nodes(n: int) -> list[_FakeNode]:
    return [_FakeNode(node_id=f"node-{i}", endpoint=f"http://node-{i}:8080") for i in range(n)]


def _make_executor(k: int = 1) -> FanoutExecutor:
    return FanoutExecutor(k=k, node_timeout=5)


# ---------------------------------------------------------------------------
# NodeClient unit tests
# ---------------------------------------------------------------------------

def _mock_http_response(data: Any) -> MagicMock:
    mock = MagicMock()
    mock.read.return_value = json.dumps(data).encode()
    mock.__enter__ = lambda s: s
    mock.__exit__ = MagicMock(return_value=False)
    return mock


class TestNodeClient:
    def setup_method(self):
        self.nc = NodeClient(timeout=5)

    def _body(self) -> dict[str, Any]:
        return {"task_id": _task_id(), "caller_nonce": _nonce()}

    def test_call_returns_dict(self):
        expected = {"task_id": "x", "code": "pass"}
        with patch("urllib.request.urlopen", return_value=_mock_http_response(expected)):
            result = self.nc.call("http://node:8080", "generate_code", self._body())
        assert result == expected

    def test_call_raises_on_url_error(self):
        import urllib.error
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            with pytest.raises(NodeCallError, match="url_error"):
                self.nc.call("http://node:8080", "generate_code", self._body())

    def test_call_raises_on_timeout(self):
        with patch("urllib.request.urlopen", side_effect=TimeoutError()):
            with pytest.raises(NodeCallError, match="timeout"):
                self.nc.call("http://node:8080", "generate_code", self._body())

    def test_call_raises_on_non_json(self):
        mock = MagicMock()
        mock.read.return_value = b"not json"
        mock.__enter__ = lambda s: s
        mock.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock):
            with pytest.raises(NodeCallError, match="response_not_json"):
                self.nc.call("http://node:8080", "generate_code", self._body())

    def test_call_raises_on_non_object_response(self):
        with patch("urllib.request.urlopen", return_value=_mock_http_response([1, 2, 3])):
            with pytest.raises(NodeCallError, match="response_not_an_object"):
                self.nc.call("http://node:8080", "generate_code", self._body())


# ---------------------------------------------------------------------------
# FanoutExecutor
# ---------------------------------------------------------------------------

class TestFanoutExecutorInit:
    def test_k_zero_raises(self):
        with pytest.raises(ValueError, match="k must be >= 1"):
            FanoutExecutor(k=0)

    def test_empty_nodes_raises(self):
        ex = _make_executor(k=1)
        with pytest.raises(ValueError, match="nodes list is empty"):
            ex.execute("generate_code", {}, nodes=[])


class TestFanoutExecutorSuccess:
    def setup_method(self):
        self.tid = _task_id()
        self.nonce = _nonce()
        self.body = {
            "task_id": self.tid,
            "caller_nonce": self.nonce,
            "prompt": "test",
            "language": "python",
        }

    def _patch_call(self, executor: FanoutExecutor, outputs: list[dict | Exception]):
        """Patch _call_node to return outputs[i] for node i (or raise if Exception)."""
        call_count = [0]

        def fake_call(node, tool_name, body):
            idx = call_count[0]
            call_count[0] += 1
            out = outputs[idx % len(outputs)]
            if isinstance(out, Exception):
                raise out
            return out

        executor._call_node = fake_call

    def test_single_node_success(self):
        nodes = _make_nodes(1)
        ex = _make_executor(k=1)
        output = _valid_output(self.tid, self.nonce)
        self._patch_call(ex, [output])
        result = ex.execute("generate_code", self.body, nodes)
        assert isinstance(result, FanoutResult)
        assert result.succeeded == 1
        assert result.failed == 0
        assert result.consensus["task_id"] == self.tid

    def test_3_of_3_success(self):
        nodes = _make_nodes(3)
        ex = _make_executor(k=3)
        output = _valid_output(self.tid, self.nonce)
        self._patch_call(ex, [output, output, output])
        result = ex.execute("generate_code", self.body, nodes)
        assert result.succeeded == 3
        assert result.failed == 0

    def test_2_of_3_success_k1(self):
        """1 failure is OK when k=1."""
        nodes = _make_nodes(3)
        ex = _make_executor(k=1)
        output = _valid_output(self.tid, self.nonce)
        error = NodeCallError("timeout", node_id="node-2", endpoint="http://node-2:8080")
        self._patch_call(ex, [output, output, error])
        result = ex.execute("generate_code", self.body, nodes)
        assert result.succeeded >= 1
        assert result.total == 3

    def test_node_results_count(self):
        nodes = _make_nodes(3)
        ex = _make_executor(k=2)
        output = _valid_output(self.tid, self.nonce)
        self._patch_call(ex, [output, output, output])
        result = ex.execute("generate_code", self.body, nodes)
        assert len(result.node_results) == 3

    def test_failed_node_recorded_in_results(self):
        nodes = _make_nodes(2)
        ex = _make_executor(k=1)
        output = _valid_output(self.tid, self.nonce)
        error = NodeCallError("url_error:refused", node_id="node-1", endpoint="http://node-1:8080")
        self._patch_call(ex, [output, error])
        result = ex.execute("generate_code", self.body, nodes)
        failed = [r for r in result.node_results if not r.success]
        assert len(failed) == 1
        assert "url_error" in failed[0].error

    def test_fanout_result_total_property(self):
        nodes = _make_nodes(2)
        ex = _make_executor(k=1)
        output = _valid_output(self.tid, self.nonce)
        error = NodeCallError("timeout")
        self._patch_call(ex, [output, error])
        result = ex.execute("generate_code", self.body, nodes)
        assert result.total == 2


class TestFanoutExecutorFailure:
    def setup_method(self):
        self.tid = _task_id()
        self.nonce = _nonce()
        self.body = {"task_id": self.tid, "caller_nonce": self.nonce}

    def _patch_call(self, executor, outputs):
        call_count = [0]

        def fake_call(node, tool_name, body):
            idx = call_count[0]
            call_count[0] += 1
            out = outputs[idx % len(outputs)]
            if isinstance(out, Exception):
                raise out
            return out

        executor._call_node = fake_call

    def test_all_fail_raises_fanout_error(self):
        nodes = _make_nodes(2)
        ex = _make_executor(k=1)
        error = NodeCallError("timeout")
        self._patch_call(ex, [error, error])
        with pytest.raises(FanoutError, match="fanout_insufficient_responses"):
            ex.execute("generate_code", self.body, nodes)

    def test_insufficient_valid_responses_raises(self):
        """k=2 but only 1 valid response."""
        nodes = _make_nodes(2)
        ex = _make_executor(k=2)
        output = _valid_output(self.tid, self.nonce)
        error = NodeCallError("timeout")
        self._patch_call(ex, [output, error])
        with pytest.raises(FanoutError, match="fanout_insufficient_responses"):
            ex.execute("generate_code", self.body, nodes)

    def test_validation_failure_counts_as_failed(self):
        """OutputValidator rejection must not count toward k."""
        nodes = _make_nodes(2)
        ex = _make_executor(k=2)
        # Bad output: nonce echo mismatch
        bad_output = _valid_output(self.tid, self.nonce)
        bad_output["caller_nonce_echo"] = "b" * 32  # wrong nonce
        good_output = _valid_output(self.tid, self.nonce)
        self._patch_call(ex, [bad_output, good_output])
        with pytest.raises(FanoutError, match="fanout_insufficient_responses"):
            ex.execute("generate_code", self.body, nodes)

    def test_fanout_error_message_includes_counts(self):
        nodes = _make_nodes(3)
        ex = _make_executor(k=3)
        error = NodeCallError("timeout")
        self._patch_call(ex, [error, error, error])
        with pytest.raises(FanoutError) as exc_info:
            ex.execute("generate_code", self.body, nodes)
        msg = str(exc_info.value)
        assert "required=3" in msg
        assert "succeeded=0" in msg


class TestFanoutExecutorConsensus:
    def setup_method(self):
        self.tid = _task_id()
        self.nonce = _nonce()
        self.body = {"task_id": self.tid, "caller_nonce": self.nonce}

    def _patch_call(self, executor, outputs):
        call_count = [0]

        def fake_call(node, tool_name, body):
            idx = call_count[0]
            call_count[0] += 1
            return outputs[idx % len(outputs)]

        executor._call_node = fake_call

    def test_majority_code_wins(self):
        """3 nodes: 2 produce code_A, 1 produces code_B → consensus is code_A."""
        nodes = _make_nodes(3)
        synthesizer = LocalSynthesizer(min_votes=2)
        ex = FanoutExecutor(k=3, synthesizer=synthesizer)
        out_a = _valid_output(self.tid, self.nonce, code="def a(): return 1")
        out_b = _valid_output(self.tid, self.nonce, code="def b(): return 2")
        self._patch_call(ex, [out_a, out_a, out_b])
        result = ex.execute("generate_code", self.body, nodes)
        assert result.consensus["code"] == "def a(): return 1"
