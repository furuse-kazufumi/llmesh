"""Tests for NonceStore (in-memory) and SqliteNonceStore (v0.2.0 durable)."""
import threading
import time
import pytest
from llmesh.mcp.nonce_store import NonceStore, SqliteNonceStore

NONCE = "a" * 32
NONCE2 = "b" * 32
NODE_A = "node-a"
NODE_B = "node-b"


# ── In-memory NonceStore (unchanged) ─────────────────────────────────────────

class TestFreshNonce:
    def test_fresh_nonce_accepted(self):
        store = NonceStore()
        assert store.check_and_store(NODE_A, NONCE) is True

    def test_different_node_same_nonce_accepted(self):
        store = NonceStore()
        store.check_and_store(NODE_A, NONCE)
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
        assert store.check_and_store(NODE_A, NONCE) is False
        time.sleep(1.1)
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
        assert store.cleanup_expired() == 0


class TestNoncePatternValidation:
    def test_valid_32_hex_nonce_accepted(self):
        store = NonceStore()
        assert store.check_and_store(NODE_A, "0" * 32) is True
        assert store.check_and_store(NODE_A, "f" * 32) is True

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


class TestThreadSafety:
    def test_concurrent_checks_no_double_accept(self):
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

        assert results.count(True) == 1
        assert results.count(False) == 9


# ── SqliteNonceStore (P0-1 durable backend) ───────────────────────────────────

class TestSqliteNonceStoreFresh:
    def _store(self, ttl=300):
        return SqliteNonceStore(db_path=":memory:", ttl_seconds=ttl)

    def test_fresh_nonce_accepted(self):
        s = self._store()
        assert s.check_and_store(NODE_A, NONCE) is True

    def test_replay_rejected(self):
        s = self._store()
        assert s.check_and_store(NODE_A, NONCE) is True
        assert s.check_and_store(NODE_A, NONCE) is False

    def test_different_node_same_nonce_accepted(self):
        s = self._store()
        s.check_and_store(NODE_A, NONCE)
        assert s.check_and_store(NODE_B, NONCE) is True

    def test_invalid_nonce_raises(self):
        s = self._store()
        with pytest.raises(ValueError, match="invalid_nonce_pattern"):
            s.check_and_store(NODE_A, "UPPERCASE" * 4)

    def test_multiple_replays_all_rejected(self):
        s = self._store()
        s.check_and_store(NODE_A, NONCE)
        for _ in range(5):
            assert s.check_and_store(NODE_A, NONCE) is False

    def test_cleanup_removes_expired(self):
        s = self._store(ttl=1)
        s.check_and_store(NODE_A, NONCE)
        s.check_and_store(NODE_A, NONCE2)
        time.sleep(1.1)
        removed = s.cleanup_expired()
        assert removed == 2

    def test_cleanup_leaves_active_nonces(self):
        s = self._store(ttl=300)
        s.check_and_store(NODE_A, NONCE)
        removed = s.cleanup_expired()
        assert removed == 0
        # Nonce should still be active (replay rejected)
        assert s.check_and_store(NODE_A, NONCE) is False


class TestSqliteNonceStoreRaceSafety:
    """UNIQUE constraint ensures only one INSERT succeeds under concurrent writes."""

    def test_concurrent_same_nonce_only_one_accepted(self):
        store = SqliteNonceStore(db_path=":memory:", ttl_seconds=300)
        results = []
        lock = threading.Lock()

        def attempt():
            result = store.check_and_store(NODE_A, NONCE)
            with lock:
                results.append(result)

        threads = [threading.Thread(target=attempt) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert results.count(True) == 1
        assert results.count(False) == 7


class TestSqliteNoncePersistence:
    """Replay protection survives process restart (file-backed DB)."""

    def test_nonce_persists_across_instances(self, tmp_path):
        db = str(tmp_path / "nonces.db")
        s1 = SqliteNonceStore(db_path=db, ttl_seconds=300)
        assert s1.check_and_store(NODE_A, NONCE) is True

        # Simulate restart: new instance same DB file
        s2 = SqliteNonceStore(db_path=db, ttl_seconds=300)
        assert s2.check_and_store(NODE_A, NONCE) is False  # replay rejected

    def test_expired_nonce_not_replayed_after_restart(self, tmp_path):
        db = str(tmp_path / "nonces2.db")
        s1 = SqliteNonceStore(db_path=db, ttl_seconds=1)
        s1.check_and_store(NODE_A, NONCE)
        time.sleep(1.1)
        # New instance, expired nonce cleaned up
        s2 = SqliteNonceStore(db_path=db, ttl_seconds=1)
        s2.cleanup_expired()
        assert s2.check_and_store(NODE_A, NONCE) is True  # fresh after expiry
