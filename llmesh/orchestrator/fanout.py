"""FanoutExecutor — k-of-n redundant execution across multiple LLMesh nodes.

Flow:
  1. If a SmartNodeSelector is attached, pre-filter and sort nodes before sending.
  2. Send the same MCP tool request to candidate nodes in parallel (ThreadPoolExecutor).
  3. Collect responses — each is validated by OutputValidator before counting.
  4. Once k valid responses are collected, pass them to LocalSynthesizer.
  5. Return the consensus FanoutResult.
  6. Record per-node outcomes (RTT, success, in_consensus) back to the selector.

Partial failures are gracefully handled: as long as k nodes respond
successfully, the fanout succeeds.

Security invariants:
- OutputValidator is always applied before a response counts toward k
- No shell=True, eval, exec, pickle anywhere
- Node endpoints from untrusted sources are never interpolated into shell commands
"""
from __future__ import annotations

import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed, Future
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..mcp.validator import OutputValidator, ValidationError
from .node_client import NodeCallError, NodeClient
from .synthesizer import LocalSynthesizer, SynthesisError

if TYPE_CHECKING:
    from ..routing.selector import SmartNodeSelector


class FanoutError(Exception):
    """Raised when fanout cannot collect k valid responses."""


@dataclass
class NodeResult:
    """Per-node outcome from a fanout execution."""

    node_id: str
    endpoint: str
    success: bool
    output: dict[str, Any] | None = None
    error: str = ""
    rtt_ms: float = 0.0


@dataclass
class FanoutResult:
    """Aggregated result from a k-of-n fanout execution."""

    consensus: dict[str, Any]     # synthesized output from LocalSynthesizer
    tool_name: str
    succeeded: int                 # nodes that returned a valid output
    failed: int                    # nodes that errored or timed out
    node_results: list[NodeResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.succeeded + self.failed


@dataclass
class _NodeSpec:
    """Minimal node descriptor used by FanoutExecutor."""

    node_id: str
    endpoint: str


class FanoutExecutor:
    """Execute a MCP tool call across n nodes and synthesize k-of-n consensus.

    Args:
        k:            Minimum number of valid node responses required for consensus.
        max_workers:  Thread pool size (defaults to candidate count, capped at 16).
        node_timeout: Per-node HTTP timeout in seconds.
        validator:    OutputValidator instance. A default is created if None.
        synthesizer:  LocalSynthesizer instance. A default is created if None.
        selector:     Optional SmartNodeSelector — filters slow/unreliable nodes,
                      sorts by contribution score, and records per-node outcomes.
                      When None, all provided nodes are used (legacy behaviour).
    """

    def __init__(
        self,
        k: int = 1,
        max_workers: int | None = None,
        node_timeout: int = 60,
        validator: OutputValidator | None = None,
        synthesizer: LocalSynthesizer | None = None,
        selector: "SmartNodeSelector | None" = None,
    ) -> None:
        if k < 1:
            raise ValueError("k must be >= 1")
        self._k = k
        self._max_workers = max_workers
        self._timeout = node_timeout
        self._validator = validator or OutputValidator()
        self._synthesizer = synthesizer or LocalSynthesizer(min_votes=k)
        self._node_client = NodeClient(timeout=node_timeout)
        self._selector = selector

    def execute(
        self,
        tool_name: str,
        body: dict[str, Any],
        nodes: list[Any],   # NodeEntry | _NodeSpec — anything with .node_id and .endpoint
        *,
        caller_nonce: str | None = None,
        task_id: str | None = None,
    ) -> FanoutResult:
        """Send tool_name to candidate nodes in parallel and return k-of-n consensus.

        Args:
            tool_name:    MCP tool name (e.g. "generate_code").
            body:         Request payload. task_id and caller_nonce must be included.
            nodes:        List of node descriptors with .node_id and .endpoint.
            caller_nonce: Nonce for OutputValidator (overrides body value).
            task_id:      task_id for OutputValidator (overrides body value).

        Returns:
            FanoutResult with consensus dict and per-node details.

        Raises:
            FanoutError: If fewer than k nodes return valid, validated responses.
            ValueError:  If nodes list is empty.
        """
        if not nodes:
            raise ValueError("nodes list is empty")

        _nonce = caller_nonce or body.get("caller_nonce", "")
        _task_id = task_id or body.get("task_id", "")

        # Pre-filter and sort nodes when a selector is attached
        candidates = self._selector.select(nodes, self._k) if self._selector else list(nodes)

        if not candidates:
            raise FanoutError(
                f"fanout_insufficient_responses: "
                f"required={self._k} succeeded=0 failed=0 "
                f"tool={tool_name} reason=all_nodes_filtered"
            )

        workers = min(self._max_workers or len(candidates), 16)

        node_results: list[NodeResult] = []
        # (node_id, validated_output) — preserves order of completion
        valid_pairs: list[tuple[str, dict[str, Any]]] = []

        with ThreadPoolExecutor(max_workers=workers) as pool:
            future_to_node: dict[Future, Any] = {
                pool.submit(self._timed_call_node, node, tool_name, body): node
                for node in candidates
            }

            for future in as_completed(future_to_node):
                node = future_to_node[future]
                raw_output, rtt_ms, call_error = future.result()  # never raises

                if call_error is not None:
                    node_results.append(NodeResult(
                        node_id=node.node_id,
                        endpoint=node.endpoint,
                        success=False,
                        error=str(call_error),
                        rtt_ms=rtt_ms,
                    ))
                    continue

                # Validate before counting toward k
                try:
                    validated = self._validator.validate(
                        json.dumps(raw_output),
                        tool_name,
                        _nonce,
                        node_id=node.node_id,
                        task_id=_task_id,
                    )
                    valid_pairs.append((node.node_id, validated))
                    node_results.append(NodeResult(
                        node_id=node.node_id,
                        endpoint=node.endpoint,
                        success=True,
                        output=validated,
                        rtt_ms=rtt_ms,
                    ))
                except ValidationError as exc:
                    node_results.append(NodeResult(
                        node_id=node.node_id,
                        endpoint=node.endpoint,
                        success=False,
                        error=f"validation_failed:{exc.reason}",
                        rtt_ms=rtt_ms,
                    ))

        succeeded = sum(1 for r in node_results if r.success)
        failed = sum(1 for r in node_results if not r.success)

        if succeeded < self._k:
            if self._selector:
                self._record_all_outcomes(node_results, consensus_node_ids=set())
            raise FanoutError(
                f"fanout_insufficient_responses: "
                f"required={self._k} succeeded={succeeded} failed={failed} "
                f"tool={tool_name}"
            )

        # Consensus uses the first k valid outputs (ordered by completion time)
        consensus_node_ids = {nid for nid, _ in valid_pairs[: self._k]}
        valid_outputs = [out for _, out in valid_pairs[: self._k]]

        try:
            consensus = self._synthesizer.synthesize(valid_outputs, tool_name)
        except SynthesisError as exc:
            if self._selector:
                self._record_all_outcomes(node_results, consensus_node_ids=set())
            raise FanoutError(f"synthesis_failed:{exc}") from exc

        if self._selector:
            self._record_all_outcomes(node_results, consensus_node_ids)

        return FanoutResult(
            consensus=consensus,
            tool_name=tool_name,
            succeeded=succeeded,
            failed=failed,
            node_results=node_results,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _timed_call_node(
        self,
        node: Any,
        tool_name: str,
        body: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, float, NodeCallError | None]:
        """Call a single node and return (output, rtt_ms, error). Never raises."""
        t0 = time.monotonic()
        try:
            output = self._call_node(node, tool_name, body)
            rtt_ms = (time.monotonic() - t0) * 1000.0
            return output, rtt_ms, None
        except NodeCallError as exc:
            rtt_ms = (time.monotonic() - t0) * 1000.0
            return None, rtt_ms, exc

    def _call_node(
        self,
        node: Any,
        tool_name: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        """Call a single node and return raw (unvalidated) response."""
        return self._node_client.call(
            endpoint=node.endpoint,
            tool_name=tool_name,
            body=body,
            node_id=node.node_id,
        )

    def _record_all_outcomes(
        self,
        node_results: list[NodeResult],
        consensus_node_ids: set[str],
    ) -> None:
        """Push per-node outcomes to the SmartNodeSelector."""
        assert self._selector is not None
        for r in node_results:
            self._selector.record_outcome(
                node_id=r.node_id,
                rtt_ms=r.rtt_ms,
                success=r.success,
                in_consensus=r.node_id in consensus_node_ids,
            )
