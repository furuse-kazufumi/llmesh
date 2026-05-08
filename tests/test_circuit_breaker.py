"""Tests for llmesh.routing.circuit_breaker — CircuitBreaker + NodeCircuitBreakerMap."""
from __future__ import annotations

import pytest
from unittest.mock import patch

from llmesh.routing.circuit_breaker import CBState, CircuitBreaker, NodeCircuitBreakerMap

# Monkeypatching time.monotonic lets tests control "elapsed time" without sleeping.
_MONO_PATH = "llmesh.routing.circuit_breaker.time.monotonic"


class TestCircuitBreakerInit:
    def test_invalid_threshold(self):
        with pytest.raises(ValueError, match="failure_threshold"):
            CircuitBreaker(failure_threshold=0)

    def test_invalid_recovery(self):
        with pytest.raises(ValueError, match="recovery_timeout"):
            CircuitBreaker(recovery_timeout=0)

    def test_starts_closed(self):
        cb = CircuitBreaker()
        assert cb.state == CBState.CLOSED
        assert cb.allow_request()


class TestCircuitBreakerStateTransitions:
    def test_trips_to_open_after_threshold(self):
        cb = CircuitBreaker(failure_threshold=3)
        cb.record_failure()
        assert cb.state == CBState.CLOSED
        cb.record_failure()
        assert cb.state == CBState.CLOSED
        cb.record_failure()
        assert cb.state == CBState.OPEN
        assert not cb.allow_request()

    def test_success_resets_failure_count(self):
        cb = CircuitBreaker(failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()   # resets counter
        cb.record_failure()   # only 1 failure after reset
        assert cb.state == CBState.CLOSED

    def test_open_transitions_to_half_open_after_timeout(self):
        with patch(_MONO_PATH) as mock_mono:
            mock_mono.return_value = 0.0
            cb = CircuitBreaker(failure_threshold=1, recovery_timeout=60.0)
            cb.record_failure()
            assert cb.state == CBState.OPEN

            mock_mono.return_value = 61.0   # advance time past recovery_timeout
            assert cb.state == CBState.HALF_OPEN
            assert cb.allow_request()

    def test_half_open_success_closes(self):
        with patch(_MONO_PATH) as mock_mono:
            mock_mono.return_value = 0.0
            cb = CircuitBreaker(failure_threshold=1, recovery_timeout=60.0)
            cb.record_failure()

            mock_mono.return_value = 61.0
            assert cb.state == CBState.HALF_OPEN
            cb.record_success()
            assert cb.state == CBState.CLOSED

    def test_half_open_failure_reopens(self):
        with patch(_MONO_PATH) as mock_mono:
            mock_mono.return_value = 0.0
            cb = CircuitBreaker(failure_threshold=1, recovery_timeout=60.0)
            cb.record_failure()

            mock_mono.return_value = 61.0
            assert cb.state == CBState.HALF_OPEN
            cb.record_failure()
            assert cb.state == CBState.OPEN

    def test_open_does_not_transition_before_timeout(self):
        with patch(_MONO_PATH) as mock_mono:
            mock_mono.return_value = 0.0
            cb = CircuitBreaker(failure_threshold=1, recovery_timeout=60.0)
            cb.record_failure()

            mock_mono.return_value = 59.9   # not yet
            assert cb.state == CBState.OPEN

    def test_single_threshold_trips_immediately(self):
        cb = CircuitBreaker(failure_threshold=1)
        cb.record_failure()
        assert cb.state == CBState.OPEN

    def test_reset_clears_open_state(self):
        cb = CircuitBreaker(failure_threshold=1)
        cb.record_failure()
        assert cb.state == CBState.OPEN
        cb.reset()
        assert cb.state == CBState.CLOSED
        assert cb.allow_request()


class TestNodeCircuitBreakerMap:
    def setup_method(self):
        self.cbm = NodeCircuitBreakerMap(failure_threshold=2, recovery_timeout=60.0)

    def test_new_node_allowed(self):
        assert self.cbm.allow_request("n1")
        assert not self.cbm.is_open("n1")

    def test_trip_specific_node(self):
        self.cbm.record_failure("n1")
        self.cbm.record_failure("n1")
        assert self.cbm.is_open("n1")
        assert not self.cbm.allow_request("n1")

    def test_other_nodes_unaffected(self):
        self.cbm.record_failure("n1")
        self.cbm.record_failure("n1")
        assert self.cbm.allow_request("n2")

    def test_reset_reopens_node(self):
        self.cbm.record_failure("n1")
        self.cbm.record_failure("n1")
        assert self.cbm.is_open("n1")
        self.cbm.reset("n1")
        assert self.cbm.allow_request("n1")

    def test_all_states_snapshot(self):
        self.cbm.record_failure("a")
        self.cbm.record_failure("a")   # a → open
        self.cbm.record_success("b")   # b → closed (created)
        states = self.cbm.all_states()
        assert states["a"] == "open"
        assert states["b"] == "closed"
