"""Hypothesis property-based tests for AuditTrace HMAC chain + QoS deadline.

audit chain は **「append → verify → tamper → verify が False」** の不変条件を
持つ。ランダム入力の event_type / node_id / task_id / policy_decision で
1〜N 回 append し、verify_chain が True を返すことを確認。**1 文字でも改竄
すれば必ず False** という整合性を Hypothesis で固定する。

QoS は単純な pure 関数 (is_expired)。境界値 + 任意 deadline で検証。
"""

from __future__ import annotations

import os
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from llmesh.audit.trace import AuditTrace


# ---------------------------------------------------------------------------
# 共通 fixture
# ---------------------------------------------------------------------------

# Audit chain は file-locking を要求するが、Hypothesis のテスト粒度では
# 環境変数で許可する unsafe-no-lock モードに頼る (テスト目的なので OK)。
os.environ.setdefault("LLMESH_UNSAFE_AUDIT_NO_LOCK", "1")


# Filesystem-safe な短い identifier 用の text strategy。
_safe_text = st.text(
    alphabet=st.characters(min_codepoint=33, max_codepoint=126, blacklist_characters="\\/:*?\"<>|"),
    min_size=1,
    max_size=20,
)


# ---------------------------------------------------------------------------
# AuditTrace HMAC chain
# ---------------------------------------------------------------------------


@given(
    entries=st.lists(
        st.tuples(_safe_text, _safe_text, _safe_text, _safe_text),
        min_size=1,
        max_size=10,
    ),
    key=st.binary(min_size=16, max_size=64),
)
@settings(
    max_examples=30,
    deadline=None,  # Windows での初回ファイル IO で 490ms 超えるケースあり (flaky 防止)
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
def test_audit_chain_verify_succeeds_after_clean_appends(
    entries, key
) -> None:
    """N 件 append したログは ``verify_chain`` で必ず True を返す."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "audit.jsonl"
        trace = AuditTrace(path, key, unsafe_no_lock=True)

        for evt, node, task, dec in entries:
            trace.log(
                event_type=evt,
                node_id=node,
                task_id=task,
                policy_decision=dec,
                output_sha256="0" * 64,
            )

        # クリーンな chain は valid
        assert AuditTrace.verify_chain(path, key) is True


@given(
    entry_count=st.integers(min_value=2, max_value=5),
    key=st.binary(min_size=16, max_size=64),
)
@settings(max_examples=20, suppress_health_check=[HealthCheck.too_slow])
def test_audit_chain_verify_fails_after_tamper(entry_count, key) -> None:
    """1 文字でも改竄すれば verify_chain は False (HMAC 整合性)."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "audit.jsonl"
        trace = AuditTrace(path, key, unsafe_no_lock=True)
        for i in range(entry_count):
            trace.log(
                event_type=f"e{i}",
                node_id=f"n{i}",
                task_id=f"t{i}",
                policy_decision="ok",
                output_sha256="0" * 64,
            )

        # 中間行を 1 文字改竄: "ok" → "no"
        original = path.read_text(encoding="utf-8")
        # 必ず存在する: 最初に書いた行内の "ok" を "no" に
        if "ok" in original:
            tampered = original.replace("ok", "no", 1)
            path.write_text(tampered, encoding="utf-8")
            assert AuditTrace.verify_chain(path, key) is False


@given(
    key1=st.binary(min_size=16, max_size=32),
    key2=st.binary(min_size=16, max_size=32),
)
@settings(max_examples=20, suppress_health_check=[HealthCheck.too_slow])
def test_audit_chain_verify_fails_with_wrong_key(key1, key2) -> None:
    """異なる HMAC key で verify は False (key を知らない攻撃者を防ぐ)."""
    if key1 == key2:
        return  # skip degenerate case (Hypothesis が同じ bytes を出すこと)
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "audit.jsonl"
        trace = AuditTrace(path, key1, unsafe_no_lock=True)
        trace.log(
            event_type="e",
            node_id="n",
            task_id="t",
            policy_decision="ok",
            output_sha256="0" * 64,
        )

        # 正しい key では True
        assert AuditTrace.verify_chain(path, key1) is True
        # 異なる key では False
        assert AuditTrace.verify_chain(path, key2) is False


@given(key=st.binary(min_size=16, max_size=32))
@settings(max_examples=10, suppress_health_check=[HealthCheck.too_slow])
def test_audit_chain_empty_file_returns_zero_entries(key) -> None:
    """存在しないファイルは entry_count=0 で valid=False."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "nonexistent.jsonl"
        result = AuditTrace.verify_chain_detailed(path, key)
        assert result.entry_count == 0
        assert result.valid is False


# ---------------------------------------------------------------------------
# QoS deadline
# ---------------------------------------------------------------------------


@given(deadline=st.floats(min_value=0, max_value=1e10))
def test_qos_is_expired_with_finite_deadline(deadline: float) -> None:
    """``is_expired`` の決定的振る舞い: 過去の deadline は expired、未来は未 expired."""
    import time

    from llmesh.protocol.qos import is_expired

    now = time.time()
    if deadline <= now:
        # 過去 (or 同時刻直後) → expired
        assert is_expired(deadline) is True
    else:
        # 未来の deadline → 未 expired
        assert is_expired(deadline) is False


def test_qos_is_expired_none_returns_false() -> None:
    """None deadline は永遠に expired にならない (no expiry セマンティクス)."""
    from llmesh.protocol.qos import is_expired

    assert is_expired(None) is False


@given(deadline=st.floats(min_value=0, max_value=1e10))
def test_qos_is_expired_idempotent(deadline: float) -> None:
    """同じ deadline を 2 度評価しても結果は安定 (sub-second オーダーの揺れ無視)."""
    from llmesh.protocol.qos import is_expired

    a = is_expired(deadline)
    b = is_expired(deadline)
    # millisecond オーダーの境界を除けば一致するはず
    if abs(deadline - __import__("time").time()) > 0.01:
        assert a == b
