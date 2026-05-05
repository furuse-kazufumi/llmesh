"""Tests for NonceStore — replay detection, TTL expiry, pattern validation."""
import time
import pytest
from llmesh.mcp.nonce_store import NonceStore

NONCE = "a" * 32
NONCE2 = "b" * 32
NODE_A = "node-a"
NODE_B = "node-b"


class TestFreshNonce:
    def test_fresh_nonce_accepted(self):
        store = NonceStore()
        assert store.check_and_store(NODE_A, NONCE) is True

    def test_different_node_same_nonce_accepted(self):
        store = NonceStore()
        store.check_and_store(NODE_A, NONCE)
        # Same nonce, different node — should be accepted
        assert store.check_and_store(NODE_B, NONCE) is True

    def test_different_nonce_same_node_accepted(self):
        store = NonceStore()
        store.check_and_store(NODE_A, NONCE)
        assert store.check_and_store(NODE_A, NONCE2) is True


class TestReplayDetection:
    def test_replay_same_node_same_nonce_rejected(self):
        store = NonceStore()
        assert store.check_and_store(NODE_A, NONCE) is True
        assert store.check_and_store(NODE_A, NONCE) is False

    def test_multiple_replays_all_rejected(self):
        store = NonceStore()
        store.check_and_store(NODE_A, NONCE)
        for _ in range(5):
            assert store.check_and_store(NODE_A, NONCE) is False


class TestTTLExpiry:
    def test_nonce_reusable_after_ttl_expires(self):
        store = NonceStore(ttl_seconds=1)
        assert store.check_and_store(NODE_A, NONCE) is True
        assert store.check_and_store(NODE_A, NONCE) is False  # within TTL

        # Wait for TTL to expire
        time.sleep(1.1)

        # After TTL, the same nonce should be accepted again
        assert store.check_and_store(NODE_A, NONCE) is True

    def test_cleanup_expired_removes_entries(self):
        store = NonceStore(ttl_seconds=1)
        store.check_and_store(NODE_A, NONCE)
        store.check_and_store(NODE_A, NONCE2)
        assert len(store._store) == 2

        time.sleep(1.1)
        removed = store.cleanup_expired()
        assert removed == 2
        assert len(store._store) == 0

    def test_cleanup_expired_returns_zero_when_nothing_expired(self):
        store = NonceStore(ttl_seconds=300)
        store.check_and_store(NODE_A, NONCE)
        removed = store.cleanup_expired()
        assert removed == 0


class TestNoncePatternValidation:
    def test_valid_32_hex_nonce_accepted(self):
        store = NonceStore()
        assert store.check_and_store(NODE_A, "0" * 32) is True
        assert store.check_and_store(NODE_A, "f" * 32) is True
        assert store.check_and_store(NODE_A, "abcdef0123456789" * 2) is True

    def test_uppercase_hex_rejected(self):
        store = NonceStore()
        with pytest.raises(ValueError, match="invalid_nonce_pattern"):
            store.check_and_store(NODE_A, "A" * 32)

    def test_too_short_rejected(self):
        store = NonceStore()
        with pytest.raises(ValueError, match="invalid_nonce_pattern"):
            store.check_and_store(NODE_A, "a" * 31)

    def test_too_long_rejected(self):
        store = NonceStore()
        with pytest.raises(ValueError, match="invalid_nonce_pattern"):
            store.check_and_store(NODE_A, "a" * 33)

    def test_empty_nonce_rejected(self):
        store = NonceStore()
        with pytest.raises(ValueError, match="invalid_nonce_pattern"):
            store.check_and_store(NODE_A, "")

    def test_non_hex_chars_rejected(self):
        store = NonceStore()
        with pytest.raises(ValueError, match="invalid_nonce_pattern"):
            store.check_and_store(NODE_A, "g" * 32)

    def test_nonce_with_hyphens_rejected(self):
        store = NonceStore()
        with pytest.raises(ValueError, match="invalid_nonce_pattern"):
            store.check_and_store(NODE_A, "a" * 28 + "----")


class TestThreadSafety:
    def test_concurrent_checks_no_double_accept(self):
        """Both threads attempt to store same nonce; exactly one must succeed."""
        import threading
        store = NonceStore(ttl_seconds=300)
        results = []
        lock = threading.Lock()

        def attempt():
            result = store.check_and_store(NODE_A, NONCE)
            with lock:
                results.append(result)

        threads = [threading.Thread(target=attempt) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Exactly one True (first acceptance), rest False (replays)
        assert results.count(True) == 1
        assert results.count(False) == 9
