"""LocalSynthesizer — k-of-n consensus over OutputValidator-verified outputs.

Only dicts that have already passed OutputValidator are accepted. The
synthesizer applies majority vote on the ``overall`` score (for
critique_output) or code similarity hashing (for code-producing tools).

Security: shell=True, pickle, eval, exec are never used here.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


class SynthesisError(Exception):
    """Raised when synthesis cannot produce a consensus result."""


class LocalSynthesizer:
    """Combine k-of-n validated node outputs into a single consensus result.

    Args:
        min_votes: Minimum number of agreeing outputs required to form
                   consensus. Defaults to 1 (any single valid output passes).
    """

    def __init__(self, min_votes: int = 1) -> None:
        if min_votes < 1:
            raise ValueError("min_votes must be >= 1")
        self._min_votes = min_votes

    def synthesize(self, outputs: list[dict[str, Any]], tool_name: str) -> dict[str, Any]:
        """Synthesize a consensus result from a list of validated outputs.

        All entries in *outputs* must already have passed OutputValidator.
        Returns a single dict representing the consensus.

        Raises SynthesisError if:
        - outputs is empty
        - no consensus group reaches min_votes
        """
        if not outputs:
            raise SynthesisError("no_outputs:empty_list")

        if tool_name in ("critique_output",):
            return self._synthesize_by_score(outputs, tool_name)
        return self._synthesize_by_code_hash(outputs, tool_name)

    # ------------------------------------------------------------------
    # Strategy: majority vote on overall score (critique_output)
    # ------------------------------------------------------------------

    def _synthesize_by_score(
        self, outputs: list[dict[str, Any]], tool_name: str
    ) -> dict[str, Any]:
        """Round overall score to 1 decimal and pick the majority bucket."""
        def _bucket(d: dict) -> str:
            overall = d.get("scores", {}).get("overall", 0.0)
            return f"{round(overall, 1):.1f}"

        return self._majority_vote(outputs, key_fn=_bucket, tool_name=tool_name)

    # ------------------------------------------------------------------
    # Strategy: code/content hash identity (generate_code, generate_tests)
    # ------------------------------------------------------------------

    def _synthesize_by_code_hash(
        self, outputs: list[dict[str, Any]], tool_name: str
    ) -> dict[str, Any]:
        """Group outputs by SHA-256 of their primary code field."""
        code_field = {
            "generate_code": "code",
            "generate_tests": "tests_code",
            "review_code": "code_sha256_echo",
        }.get(tool_name, "code")

        def _bucket(d: dict) -> str:
            raw = d.get(code_field, "")
            return hashlib.sha256(
                json.dumps(raw, sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest()

        return self._majority_vote(outputs, key_fn=_bucket, tool_name=tool_name)

    # ------------------------------------------------------------------
    # Core majority-vote helper
    # ------------------------------------------------------------------

    def _majority_vote(
        self,
        outputs: list[dict[str, Any]],
        key_fn,
        tool_name: str,
    ) -> dict[str, Any]:
        buckets: dict[str, list[dict]] = {}
        for output in outputs:
            key = key_fn(output)
            buckets.setdefault(key, []).append(output)

        # Find the largest group
        best_key = max(buckets, key=lambda k: len(buckets[k]))
        best_group = buckets[best_key]

        if len(best_group) < self._min_votes:
            raise SynthesisError(
                f"no_consensus:tool={tool_name} "
                f"best_group={len(best_group)} required={self._min_votes}"
            )

        # Return the first element of the winning group as the representative
        return best_group[0]
