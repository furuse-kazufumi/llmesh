"""CircuitBreaker — per-node failure isolation with CLOSED / OPEN / HALF_OPEN states.

State machine:
  CLOSED   → normal operation; failure counter increments on each error
  OPEN     → node is excluded; transitions to HALF_OPEN after recovery_timeout
  HALF_OPEN → one probe request allowed; success → CLOSED, failure → OPEN

Security invariants:
- No shell=True, eval, exec, pickle anywhere
- Thread-safe: all state mutations under _lock
"""
from __future__ import annotations

import threading
import time
from enum import Enum


class CBState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Single-node circuit breaker.

    Args:
        failure_threshold: Consecutive failures before tripping to OPEN.
        recovery_timeout:  Seconds to wait in OPEN before allowing a probe.
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        recovery_timeout: float = 60.0,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        if recovery_timeout <= 0:
            raise ValueError("recovery_timeout must be positive")
        self._threshold = failure_threshold
        self._recovery = recovery_timeout
        self._state = CBState.CLOSED
        self._failures = 0
        self._opened_at: float = 0.0
        self._lock = threading.Lock()

    @property
    def state(self) -> CBState:
        with self._lock:
            return self._current_state()

    def _current_state(self) -> CBState:
        """Return current state, transitioning OPEN → HALF_OPEN when timer expires."""
        if self._state == CBState.OPEN:
            if time.monotonic() - self._opened_at >= self._recovery:
                self._state = CBState.HALF_OPEN
        return self._state

    def allow_request(self) -> bool:
        """Return True if a request may pass through (CLOSED or HALF_OPEN)."""
        with self._lock:
            return self._current_state() in (CBState.CLOSED, CBState.HALF_OPEN)

    def record_success(self) -> None:
        """Signal that the last request succeeded; resets to CLOSED."""
        with self._lock:
            self._failures = 0
            self._state = CBState.CLOSED

    def record_failure(self) -> None:
        """Signal that the last request failed; may trip to OPEN."""
        with self._lock:
            self._failures += 1
            if self._state == CBState.HALF_OPEN or self._failures >= self._threshold:
                self._state = CBState.OPEN
                self._opened_at = time.monotonic()
                self._failures = 0

    def reset(self) -> None:
        """Forcibly reset to CLOSED (e.g., for testing or manual admin override)."""
        with self._lock:
            self._state = CBState.CLOSED
            self._failures = 0
            self._opened_at = 0.0


class NodeCircuitBreakerMap:
    """Manages one CircuitBreaker per node_id. Thread-safe.

    Args:
        failure_threshold: Passed to each new CircuitBreaker.
        recovery_timeout:  Passed to each new CircuitBreaker.
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        recovery_timeout: float = 60.0,
    ) -> None:
        self._threshold = failure_threshold
        self._recovery = recovery_timeout
        self._breakers: dict[str, CircuitBreaker] = {}
        self._lock = threading.Lock()

    def _get(self, node_id: str) -> CircuitBreaker:
        if node_id not in self._breakers:
            self._breakers[node_id] = CircuitBreaker(self._threshold, self._recovery)
        return self._breakers[node_id]

    def allow_request(self, node_id: str) -> bool:
        with self._lock:
            return self._get(node_id).allow_request()

    def record_success(self, node_id: str) -> None:
        with self._lock:
            self._get(node_id).record_success()

    def record_failure(self, node_id: str) -> None:
        with self._lock:
            self._get(node_id).record_failure()

    def is_open(self, node_id: str) -> bool:
        with self._lock:
            return not self._get(node_id).allow_request()

    def all_states(self) -> dict[str, str]:
        """Return a snapshot of all known breaker states."""
        with self._lock:
            return {nid: cb.state.value for nid, cb in self._breakers.items()}

    def reset(self, node_id: str) -> None:
        """Manually reset a single breaker to CLOSED."""
        with self._lock:
            self._get(node_id).reset()
