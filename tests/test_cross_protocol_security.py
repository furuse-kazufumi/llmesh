"""Tests for llmesh.security.cross_protocol — v0.9.0 hardening primitives."""
from __future__ import annotations

import pytest

from llmesh.mcp.nonce_store import NonceStore
from llmesh.security.cross_protocol import (
    AdapterCircuitBreakerRegistry,
    CrossProtocolNonceGuard,
    RateLimitExceeded,
    UnifiedRateLimiter,
)


# ---------------------------------------------------------------------------
# CrossProtocolNonceGuard
# ---------------------------------------------------------------------------

class TestCrossProtocolNonceGuard:
    def setup_method(self):
        self.store = NonceStore()
        self.guard = CrossProtocolNonceGuard(self.store)

    def test_fresh_nonce_accepted(self):
        assert self.guard.check_and_store("node-a", "a" * 32)

    def test_same_nonce_second_call_rejected(self):
        nonce = "b" * 32
        assert self.guard.check_and_store("node-a", nonce)
        assert not self.guard.check_and_store("node-a", nonce)

    def test_cross_protocol_replay_blocked(self):
        """Nonce used on HTTP must not be accepted again on SMTP."""
        nonce = "c" * 32
        assert self.guard.check_and_store("node-a", nonce, protocol="http")
        assert not self.guard.check_and_store("node-a", nonce, protocol="smtp")

    def test_different_nodes_same_nonce_allowed(self):
        nonce = "d" * 32
        assert self.guard.check_and_store("node-a", nonce, protocol="http")
        assert self.guard.check_and_store("node-b", nonce, protocol="http")

    def test_invalid_nonce_raises(self):
        with pytest.raises(ValueError):
            self.guard.check_and_store("node-a", "tooshort")

    def test_invalid_store_raises_type_error(self):
        with pytest.raises(TypeError):
            CrossProtocolNonceGuard("not-a-store")  # type: ignore[arg-type]

    def test_protocol_param_ignored_for_dedup(self):
        """protocol argument is accepted but does not change dedup behaviour."""
        nonce = "e" * 32
        assert self.guard.check_and_store("node-a", nonce, protocol="ftp")
        # Same node, same nonce, different protocol → still rejected
        assert not self.guard.check_and_store("node-a", nonce, protocol="ssh")
        assert not self.guard.check_and_store("node-a", nonce, protocol="")


# ---------------------------------------------------------------------------
# UnifiedRateLimiter
# ---------------------------------------------------------------------------

class TestUnifiedRateLimiter:
    def test_fresh_node_protocol_allowed(self):
        limiter = UnifiedRateLimiter(rate=10.0, burst=5.0)
        limiter.check("http", "node-a")   # should not raise

    def test_exhausted_raises(self):
        limiter = UnifiedRateLimiter(rate=1.0, burst=2.0)
        limiter.check("http", "node-a")
        limiter.check("http", "node-a")
        with pytest.raises(RateLimitExceeded):
            limiter.check("http", "node-a")

    def test_different_protocols_independent(self):
        limiter = UnifiedRateLimiter(rate=1.0, burst=1.0)
        limiter.check("http", "node-a")   # exhausts http:node-a
        # smtp:node-a has its own bucket — should not raise
        limiter.check("smtp", "node-a")

    def test_different_nodes_independent(self):
        limiter = UnifiedRateLimiter(rate=1.0, burst=1.0)
        limiter.check("http", "node-a")
        limiter.check("http", "node-b")   # independent bucket

    def test_reset_restores_budget(self):
        limiter = UnifiedRateLimiter(rate=1.0, burst=1.0)
        limiter.check("http", "node-a")
        limiter.reset("http", "node-a")
        limiter.check("http", "node-a")   # should not raise

    def test_available_tokens_full_initially(self):
        limiter = UnifiedRateLimiter(rate=1.0, burst=5.0)
        tokens = limiter.available_tokens("http", "node-a")
        assert tokens == pytest.approx(5.0)

    def test_available_tokens_decreases_after_check(self):
        limiter = UnifiedRateLimiter(rate=1.0, burst=5.0)
        limiter.check("http", "node-a")
        tokens = limiter.available_tokens("http", "node-a")
        assert tokens < 5.0

    def test_invalid_rate_raises(self):
        with pytest.raises(ValueError):
            UnifiedRateLimiter(rate=0.0, burst=10.0)

    def test_invalid_burst_raises(self):
        with pytest.raises(ValueError):
            UnifiedRateLimiter(rate=1.0, burst=0.0)


# ---------------------------------------------------------------------------
# AdapterCircuitBreakerRegistry
# ---------------------------------------------------------------------------

class TestAdapterCircuitBreakerRegistry:
    def setup_method(self):
        self.registry = AdapterCircuitBreakerRegistry(
            failure_threshold=2, recovery_timeout=60.0
        )

    def test_new_pair_allowed(self):
        assert self.registry.allow_request("http", "node-a")
        assert not self.registry.is_open("http", "node-a")

    def test_trip_after_threshold(self):
        self.registry.record_failure("smtp", "node-b")
        self.registry.record_failure("smtp", "node-b")
        assert self.registry.is_open("smtp", "node-b")
        assert not self.registry.allow_request("smtp", "node-b")

    def test_different_adapters_independent(self):
        self.registry.record_failure("http", "node-a")
        self.registry.record_failure("http", "node-a")
        assert self.registry.is_open("http", "node-a")
        # smtp:node-a is unaffected
        assert self.registry.allow_request("smtp", "node-a")

    def test_different_nodes_independent(self):
        self.registry.record_failure("http", "node-a")
        self.registry.record_failure("http", "node-a")
        assert self.registry.is_open("http", "node-a")
        assert not self.registry.is_open("http", "node-b")

    def test_success_resets_failure_count(self):
        self.registry.record_failure("ftp", "node-c")
        self.registry.record_success("ftp", "node-c")
        self.registry.record_failure("ftp", "node-c")   # count starts from 0
        assert not self.registry.is_open("ftp", "node-c")

    def test_manual_reset_reopens_circuit(self):
        self.registry.record_failure("ssh", "node-d")
        self.registry.record_failure("ssh", "node-d")
        assert self.registry.is_open("ssh", "node-d")
        self.registry.reset("ssh", "node-d")
        assert self.registry.allow_request("ssh", "node-d")

    def test_all_states_snapshot(self):
        self.registry.record_failure("http", "node-x")
        self.registry.record_failure("http", "node-x")   # open
        self.registry.record_success("smtp", "node-y")    # closed (created)
        states = self.registry.all_states()
        assert states[("http", "node-x")] == "open"
        assert states[("smtp", "node-y")] == "closed"

    def test_unknown_node_not_in_all_states(self):
        states = self.registry.all_states()
        assert ("telnet", "unknown") not in states

    def test_invalid_threshold_raises(self):
        with pytest.raises(ValueError, match="failure_threshold"):
            AdapterCircuitBreakerRegistry(failure_threshold=0)
