"""Tests for llmesh.security.rate_limiter — PerNodeRateLimiter."""
from __future__ import annotations

import time
import pytest

from llmesh.security.rate_limiter import PerNodeRateLimiter, RateLimitExceeded


class TestPerNodeRateLimiterInit:
    def test_zero_rate_raises(self):
        with pytest.raises(ValueError, match="rate"):
            PerNodeRateLimiter(rate=0)

    def test_zero_burst_raises(self):
        with pytest.raises(ValueError, match="burst"):
            PerNodeRateLimiter(burst=0)

    def test_negative_raises(self):
        with pytest.raises(ValueError):
            PerNodeRateLimiter(rate=-1.0)


class TestPerNodeRateLimiterCheck:
    def test_burst_allows_initial_requests(self):
        rl = PerNodeRateLimiter(rate=1.0, burst=5.0)
        for _ in range(5):
            rl.check("node-a")   # should not raise

    def test_exceeding_burst_raises(self):
        rl = PerNodeRateLimiter(rate=0.001, burst=2.0)
        rl.check("n")
        rl.check("n")
        with pytest.raises(RateLimitExceeded, match="rate_limit_exceeded"):
            rl.check("n")

    def test_different_nodes_independent(self):
        rl = PerNodeRateLimiter(rate=0.001, burst=1.0)
        rl.check("a")   # consumes a's only token
        with pytest.raises(RateLimitExceeded):
            rl.check("a")
        rl.check("b")   # b has its own full bucket

    def test_reset_refills_bucket(self):
        rl = PerNodeRateLimiter(rate=0.001, burst=1.0)
        rl.check("n")
        with pytest.raises(RateLimitExceeded):
            rl.check("n")
        rl.reset("n")
        rl.check("n")   # should not raise after reset

    def test_tokens_refill_over_time(self):
        rl = PerNodeRateLimiter(rate=100.0, burst=1.0)
        rl.check("n")   # drains bucket
        time.sleep(0.02)   # 100 tok/s × 0.02 s = 2 tokens refilled
        rl.check("n")   # should not raise

    def test_available_tokens_decreases_after_check(self):
        rl = PerNodeRateLimiter(rate=0.001, burst=5.0)
        before = rl.available_tokens("n")
        rl.check("n")
        after = rl.available_tokens("n")
        assert after < before

    def test_unknown_node_has_full_bucket(self):
        rl = PerNodeRateLimiter(rate=1.0, burst=10.0)
        assert rl.available_tokens("new-node") == pytest.approx(10.0)

    # --- P0-1: new-bucket initialization regression tests ---

    def test_first_request_on_new_node_never_raises(self):
        """New bucket must start full so the very first check always passes."""
        rl = PerNodeRateLimiter(rate=1.0, burst=5.0)
        # Must NOT raise — bucket is full on first use
        rl.check("brand-new-node")

    def test_new_bucket_last_refill_not_after_now(self):
        """last_refill must equal now, not later (negative elapsed regression)."""
        rl = PerNodeRateLimiter(rate=100.0, burst=1.0)
        # If last_refill > now, elapsed < 0 → tokens = min(1, 1 + negative) < 1 → raises
        rl.check("regression-node")  # must not raise

    def test_consecutive_checks_up_to_burst_succeed(self):
        """Exactly burst consecutive calls must all succeed on a fresh bucket."""
        burst = 5
        rl = PerNodeRateLimiter(rate=0.0001, burst=float(burst))
        for i in range(burst):
            rl.check("flood-node")   # must not raise
        with pytest.raises(RateLimitExceeded):
            rl.check("flood-node")  # (burst+1)th must raise
