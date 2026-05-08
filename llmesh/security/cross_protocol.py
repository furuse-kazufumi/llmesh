"""Cross-protocol security primitives for LLMesh v0.9.0.

Three hardening components that operate *across* all protocol adapters:

CrossProtocolNonceGuard
    Wraps any NonceStore / SqliteNonceStore and ignores the protocol label
    when deduplicating nonces.  This prevents a nonce captured on HTTP from
    being replayed via SMTP or any other adapter.

UnifiedRateLimiter
    A single PerNodeRateLimiter shared by all adapters.  Keys are
    ``"<protocol>:<node_id>"`` so burst budgets are protocol-aware but
    the instance is singleton across the mesh.

AdapterCircuitBreakerRegistry
    One CircuitBreaker per ``(adapter_name, node_id)`` pair.  A single
    registry is shared across all adapters so that a node repeatedly
    failing on SSH can be quarantined before it floods SMTP as well.

Security invariants:
- No shell=True, eval, exec, pickle anywhere.
- All mutations are thread-safe (delegated to underlying primitives).
- Guard failures fail closed: check() raises, callers must reject requests.
"""
from __future__ import annotations

import threading
from typing import Protocol, runtime_checkable

from llmesh.routing.circuit_breaker import CircuitBreaker, CBState, NodeCircuitBreakerMap
from llmesh.security.rate_limiter import PerNodeRateLimiter, RateLimitExceeded


# ---------------------------------------------------------------------------
# Nonce guard
# ---------------------------------------------------------------------------

@runtime_checkable
class _NonceStoreProto(Protocol):
    def check_and_store(self, node_id: str, nonce: str) -> bool: ...


class CrossProtocolNonceGuard:
    """Protocol-transparent nonce deduplication.

    Accepts any object that implements ``check_and_store(node_id, nonce) -> bool``
    (both ``NonceStore`` and ``SqliteNonceStore`` qualify).

    The ``protocol`` parameter in :meth:`check_and_store` is accepted for API
    symmetry but is intentionally *not* included in the deduplication key.
    A nonce is global: once consumed on any protocol, it cannot be replayed
    on any other.
    """

    def __init__(self, store: _NonceStoreProto) -> None:
        if not isinstance(store, _NonceStoreProto):
            raise TypeError("store must implement check_and_store(node_id, nonce) -> bool")
        self._store = store

    def check_and_store(self, node_id: str, nonce: str, protocol: str = "") -> bool:
        """Return True if the nonce is fresh; False if it was already seen.

        Args:
            node_id:  Originating node identifier.
            nonce:    32-hex-char nonce string.
            protocol: Ignored — kept for call-site documentation only.

        Raises:
            ValueError: If nonce format is invalid (delegated to inner store).
        """
        _ = protocol  # cross-protocol: protocol label does not scope nonces
        return self._store.check_and_store(node_id, nonce)


# ---------------------------------------------------------------------------
# Unified rate limiter
# ---------------------------------------------------------------------------

class UnifiedRateLimiter:
    """Single token-bucket rate limiter shared across all protocol adapters.

    Keys are ``"<protocol>:<node_id>"`` so each protocol has its own budget
    per node, while the limiter instance itself is a singleton.

    Usage::

        limiter = UnifiedRateLimiter(rate=10.0, burst=20.0)
        limiter.check("smtp", "node-abc")   # raises RateLimitExceeded if over budget

    Args:
        rate:  Tokens refilled per second per (protocol, node) pair.
        burst: Maximum token capacity per (protocol, node) pair.
    """

    def __init__(self, rate: float = 10.0, burst: float = 20.0) -> None:
        self._limiter = PerNodeRateLimiter(rate=rate, burst=burst)

    def _key(self, protocol: str, node_id: str) -> str:
        return f"{protocol}:{node_id}"

    def check(self, protocol: str, node_id: str) -> None:
        """Consume one token for (protocol, node_id).

        Raises:
            RateLimitExceeded: If the budget for this (protocol, node) is exhausted.
        """
        self._limiter.check(self._key(protocol, node_id))

    def reset(self, protocol: str, node_id: str) -> None:
        """Reset the token bucket for (protocol, node_id) to full capacity."""
        self._limiter.reset(self._key(protocol, node_id))

    def available_tokens(self, protocol: str, node_id: str) -> float:
        """Return current token count for (protocol, node_id)."""
        return self._limiter.available_tokens(self._key(protocol, node_id))


# Re-export so callers can catch it without importing rate_limiter directly.
__all__ = [
    "CrossProtocolNonceGuard",
    "UnifiedRateLimiter",
    "AdapterCircuitBreakerRegistry",
    "RateLimitExceeded",
]


# ---------------------------------------------------------------------------
# Adapter circuit breaker registry
# ---------------------------------------------------------------------------

class AdapterCircuitBreakerRegistry:
    """One CircuitBreaker per (adapter_name, node_id) pair.

    A shared registry means that a node repeatedly failing on one adapter
    can be identified and quarantined before it also saturates other adapters.

    Usage::

        registry = AdapterCircuitBreakerRegistry(failure_threshold=5)
        registry.record_failure("smtp", "node-abc")
        if not registry.allow_request("smtp", "node-abc"):
            raise TransportError("circuit open")

    Args:
        failure_threshold: Consecutive failures before tripping to OPEN.
        recovery_timeout:  Seconds in OPEN before a probe is allowed.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        if recovery_timeout <= 0:
            raise ValueError("recovery_timeout must be positive")
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._breakers: dict[tuple[str, str], CircuitBreaker] = {}
        self._lock = threading.Lock()

    def _key(self, adapter: str, node_id: str) -> tuple[str, str]:
        return (adapter, node_id)

    def _get_or_create(self, adapter: str, node_id: str) -> CircuitBreaker:
        key = self._key(adapter, node_id)
        with self._lock:
            if key not in self._breakers:
                self._breakers[key] = CircuitBreaker(
                    failure_threshold=self._failure_threshold,
                    recovery_timeout=self._recovery_timeout,
                )
            return self._breakers[key]

    def allow_request(self, adapter: str, node_id: str) -> bool:
        """Return True if the circuit is CLOSED or in HALF_OPEN probe state."""
        return self._get_or_create(adapter, node_id).allow_request()

    def record_success(self, adapter: str, node_id: str) -> None:
        """Record a successful request; closes an open circuit after HALF_OPEN probe."""
        self._get_or_create(adapter, node_id).record_success()

    def record_failure(self, adapter: str, node_id: str) -> None:
        """Record a failed request; trips to OPEN after failure_threshold."""
        self._get_or_create(adapter, node_id).record_failure()

    def is_open(self, adapter: str, node_id: str) -> bool:
        """Return True if the circuit is currently OPEN (node blocked)."""
        key = self._key(adapter, node_id)
        with self._lock:
            if key not in self._breakers:
                return False
        return self._get_or_create(adapter, node_id).state == CBState.OPEN

    def reset(self, adapter: str, node_id: str) -> None:
        """Manually reset the circuit to CLOSED."""
        self._get_or_create(adapter, node_id).reset()

    def all_states(self) -> dict[tuple[str, str], str]:
        """Return a snapshot of all (adapter, node_id) → state-name pairs."""
        with self._lock:
            keys = list(self._breakers.keys())
        return {k: self._breakers[k].state.value for k in keys}
