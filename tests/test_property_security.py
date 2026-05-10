"""Hypothesis property-based tests for PerNodeRateLimiter + NonceStore.

PerNodeRateLimiter (token bucket):
- 任意の random rate / burst / sequence で「burst 個までなら絶対通る」
- burst+1 個目は (短時間の場合) 必ず RateLimitExceeded
- reset 後は再びフル容量

NonceStore (replay 防御):
- 同じ (node_id, nonce) を 2 度入れると 2 回目は False (replay)
- 違う nonce 同士は独立
- 不正な nonce フォーマットは ValueError
"""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st


# ---------------------------------------------------------------------------
# PerNodeRateLimiter
# ---------------------------------------------------------------------------


@given(
    rate=st.floats(min_value=0.1, max_value=1000.0),
    burst=st.floats(min_value=1.0, max_value=1000.0),
)
@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
def test_rate_limiter_initial_capacity_equals_burst(
    rate: float, burst: float
) -> None:
    """初期化直後は burst 個分のトークンが利用可能."""
    from llmesh.security.rate_limiter import PerNodeRateLimiter

    limiter = PerNodeRateLimiter(rate=rate, burst=burst)
    available = limiter.available_tokens("any-node")
    # +/- 浮動小数誤差は無視
    assert abs(available - burst) < 1e-6


@given(
    burst_int=st.integers(min_value=1, max_value=20),
)
@settings(max_examples=20, suppress_health_check=[HealthCheck.too_slow])
def test_rate_limiter_burst_count_passes_then_blocks(burst_int: int) -> None:
    """burst 個までは通る、burst+1 個目は (rate × 経過時間 が小さければ) ブロック."""
    from llmesh.security.rate_limiter import (
        PerNodeRateLimiter,
        RateLimitExceeded,
    )

    # 100 ms 以内に burst+1 個を消費するため rate=0.001 (refill 無視できる)
    limiter = PerNodeRateLimiter(rate=0.001, burst=float(burst_int))

    # burst 個までは通る
    for _ in range(burst_int):
        limiter.check("node-a")

    # burst+1 個目は確実にブロック
    import pytest

    with pytest.raises(RateLimitExceeded):
        limiter.check("node-a")


@given(
    burst_int=st.integers(min_value=1, max_value=10),
)
@settings(max_examples=20, suppress_health_check=[HealthCheck.too_slow])
def test_rate_limiter_reset_restores_full_capacity(burst_int: int) -> None:
    """reset() 後は再び burst 個まで通る."""
    from llmesh.security.rate_limiter import PerNodeRateLimiter

    limiter = PerNodeRateLimiter(rate=0.001, burst=float(burst_int))
    # 全部使い切る
    for _ in range(burst_int):
        limiter.check("node-r")
    # reset で復活
    limiter.reset("node-r")
    # 再び burst 個まで通る
    for _ in range(burst_int):
        limiter.check("node-r")


def test_rate_limiter_zero_rate_raises() -> None:
    """rate=0 は明示的に拒否 (無効な設定)."""
    from llmesh.security.rate_limiter import PerNodeRateLimiter

    import pytest

    with pytest.raises(ValueError):
        PerNodeRateLimiter(rate=0, burst=10)
    with pytest.raises(ValueError):
        PerNodeRateLimiter(rate=10, burst=0)


@given(
    nodes=st.lists(
        st.text(min_size=1, max_size=10),
        min_size=2, max_size=5, unique=True,
    ),
)
@settings(max_examples=20, suppress_health_check=[HealthCheck.too_slow])
def test_rate_limiter_per_node_independent(nodes: list[str]) -> None:
    """異なる node_id は独立 bucket: 1 ノードを使い切っても他ノードは無事."""
    from llmesh.security.rate_limiter import (
        PerNodeRateLimiter,
        RateLimitExceeded,
    )

    burst = 2
    limiter = PerNodeRateLimiter(rate=0.001, burst=float(burst))
    # 最初のノードを使い切る
    first = nodes[0]
    for _ in range(burst):
        limiter.check(first)
    import pytest
    with pytest.raises(RateLimitExceeded):
        limiter.check(first)

    # 他のノードは影響を受けない
    for node in nodes[1:]:
        for _ in range(burst):
            limiter.check(node)


# ---------------------------------------------------------------------------
# NonceStore (replay defence)
# ---------------------------------------------------------------------------


# nonce は ^[a-f0-9]{32}$ 限定 (32 桁 hex)
_nonce_strategy = st.text(
    alphabet="0123456789abcdef",
    min_size=32, max_size=32,
)


@given(
    node_id=st.text(min_size=1, max_size=20),
    nonce=_nonce_strategy,
)
@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
def test_nonce_store_first_acceptance_then_replay_rejected(
    node_id: str, nonce: str
) -> None:
    """同じ (node, nonce) を 2 度入れると 2 回目は False (replay)."""
    from llmesh.mcp.nonce_store import NonceStore

    store = NonceStore(ttl_seconds=300)
    assert store.check_and_store(node_id, nonce) is True
    # 同じ key の再入力は False
    assert store.check_and_store(node_id, nonce) is False


@given(
    node_id=st.text(min_size=1, max_size=20),
    nonce1=_nonce_strategy,
    nonce2=_nonce_strategy,
)
@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
def test_nonce_store_different_nonces_independent(
    node_id: str, nonce1: str, nonce2: str
) -> None:
    """異なる nonce は独立に受け入れられる."""
    if nonce1 == nonce2:
        return  # skip degenerate case
    from llmesh.mcp.nonce_store import NonceStore

    store = NonceStore(ttl_seconds=300)
    assert store.check_and_store(node_id, nonce1) is True
    assert store.check_and_store(node_id, nonce2) is True


@given(nonce=_nonce_strategy)
@settings(max_examples=20, suppress_health_check=[HealthCheck.too_slow])
def test_nonce_store_different_nodes_independent(nonce: str) -> None:
    """同じ nonce でも異なる node_id なら独立."""
    from llmesh.mcp.nonce_store import NonceStore

    store = NonceStore(ttl_seconds=300)
    assert store.check_and_store("node-a", nonce) is True
    # 異なるノードからは新規として受理される
    assert store.check_and_store("node-b", nonce) is True


@given(
    bad_nonce=st.text(min_size=1, max_size=50).filter(
        lambda s: not (len(s) == 32 and all(c in "0123456789abcdef" for c in s))
    ),
)
@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
def test_nonce_store_invalid_format_raises(bad_nonce: str) -> None:
    """32 桁 hex 以外の nonce は ValueError (input validation)."""
    from llmesh.mcp.nonce_store import NonceStore

    store = NonceStore()
    import pytest
    with pytest.raises(ValueError):
        store.check_and_store("node", bad_nonce)


# ---------------------------------------------------------------------------
# Combined: rate limit + nonce store はそれぞれ独立に効く
# ---------------------------------------------------------------------------


def test_combined_rate_limit_and_nonce_store_orthogonal() -> None:
    """rate limit と nonce store は独立に評価される."""
    from llmesh.mcp.nonce_store import NonceStore
    from llmesh.security.rate_limiter import PerNodeRateLimiter

    limiter = PerNodeRateLimiter(rate=0.001, burst=2.0)
    store = NonceStore()

    # 2 回まで通る
    for i, nonce in enumerate(["a" * 32, "b" * 32]):
        limiter.check("node")
        assert store.check_and_store("node", nonce) is True

    # 3 個目は rate limit でブロック (nonce 自体は新規でも)
    import pytest
    from llmesh.security.rate_limiter import RateLimitExceeded
    with pytest.raises(RateLimitExceeded):
        limiter.check("node")

    # 1 度通った nonce はもう一度入れても False
    assert store.check_and_store("node", "a" * 32) is False
