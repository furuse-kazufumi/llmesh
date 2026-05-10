"""Hypothesis property-based tests for UnifiedMessage / NodeAddress / codec.

これら 3 つは llmesh の protocol 層の中核 (全 adapter が round-trip させる)
ので、ランダム入力で **「どんな valid な値でも双方向変換が安定」** という
不変条件を検証する。

代表的な property:
- `NodeAddress.from_dict(addr.to_dict()) == addr`
- `UnifiedMessage.from_dict(msg.to_dict()) == msg` (但し timestamp 等
  default-factory フィールドは固定して比較)
- `UnifiedMessage.from_bytes(msg.to_bytes("json")) == msg`
- `decode(encode(d, "json"))` は等価な dict を返す
- decode は空 bytes で必ず ValueError
"""

from __future__ import annotations

import json

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from llmesh.protocol.codec import decode, encode
from llmesh.protocol.message import MessageType, NodeAddress, UnifiedMessage


# ---------------------------------------------------------------------------
# strategies
# ---------------------------------------------------------------------------

# JSON-safe payload: ネスト無しの dict[str, primitive]。round-trip で
# 完全一致を維持できる範囲に絞る。
_json_primitive = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-(2**31), max_value=2**31 - 1),
    # JSON は finite float 限定。Hypothesis の floats() は infinity / NaN を
    # 含むので allow_nan=False / allow_infinity=False で制約。
    st.floats(allow_nan=False, allow_infinity=False, width=32),
    st.text(max_size=20),
)
_json_payload = st.dictionaries(
    keys=st.text(min_size=1, max_size=10).filter(lambda s: not s.startswith("_")),
    values=_json_primitive,
    max_size=5,
)

_node_address_strategy = st.builds(
    NodeAddress,
    host=st.text(min_size=1, max_size=20).filter(lambda s: ":" not in s),
    port=st.integers(min_value=1, max_value=65535),
    node_id=st.text(max_size=20),
)


# ---------------------------------------------------------------------------
# NodeAddress
# ---------------------------------------------------------------------------


@given(_node_address_strategy)
@settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
def test_node_address_dict_roundtrip(addr: NodeAddress) -> None:
    assert NodeAddress.from_dict(addr.to_dict()) == addr


@given(_node_address_strategy)
@settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
def test_node_address_str_format(addr: NodeAddress) -> None:
    """str(addr) は host:port 形式 (node_id は含まない)."""
    s = str(addr)
    assert s == f"{addr.host}:{addr.port}"


# ---------------------------------------------------------------------------
# UnifiedMessage
# ---------------------------------------------------------------------------


@given(
    payload=_json_payload,
    sender=_node_address_strategy,
    target=st.one_of(st.none(), _node_address_strategy),
)
@settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
def test_unified_message_request_dict_roundtrip(
    payload: dict, sender: NodeAddress, target: NodeAddress | None
) -> None:
    msg = UnifiedMessage.request(payload, sender, target)
    restored = UnifiedMessage.from_dict(msg.to_dict())
    # round-trip 後は同じ shape で復元されているはず
    assert restored.id == msg.id
    assert restored.type == msg.type
    assert restored.payload == msg.payload
    assert restored.sender == msg.sender
    assert restored.target == msg.target
    assert restored.timestamp == msg.timestamp
    assert restored.ttl == msg.ttl


@given(
    payload=_json_payload,
    sender=_node_address_strategy,
)
@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
def test_unified_message_bytes_roundtrip_json(
    payload: dict, sender: NodeAddress
) -> None:
    """JSON codec で bytes <-> message の往復が安定."""
    msg = UnifiedMessage.request(payload, sender)
    wire = msg.to_bytes("json")
    assert isinstance(wire, bytes)
    assert wire[0:1] == b"{"  # JSON 識別子
    restored = UnifiedMessage.from_bytes(wire)
    assert restored.payload == msg.payload
    assert restored.sender == msg.sender


@given(
    payload=_json_payload,
    sender=_node_address_strategy,
    target=_node_address_strategy,
)
@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
def test_unified_message_response_correlates_to_request(
    payload: dict, sender: NodeAddress, target: NodeAddress
) -> None:
    """make_response は元 message に correlated."""
    req = UnifiedMessage.request({"q": "x"}, sender, target)
    resp = req.make_response(payload, target)
    assert resp.type == MessageType.RESPONSE
    assert resp.correlation_id == req.id
    assert resp.target == req.sender
    assert resp.sender == target


@given(
    payload=_json_payload,
    sender=_node_address_strategy,
    error=st.booleans(),
)
@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
def test_unified_message_response_error_flag(
    payload: dict, sender: NodeAddress, error: bool
) -> None:
    """error=True なら type が ERROR に切り替わる."""
    req = UnifiedMessage.request({"q": 1}, sender)
    resp = req.make_response(payload, sender, error=error)
    assert resp.type == (MessageType.ERROR if error else MessageType.RESPONSE)


@given(
    payload=_json_payload,
    sender=_node_address_strategy,
    seq=st.integers(min_value=0, max_value=10000),
)
@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
def test_unified_message_chunk_constructs_streaming(
    payload: dict, sender: NodeAddress, seq: int
) -> None:
    """chunk(total_chunks=None) → STREAM_CHUNK / chunk(total_chunks=N) → STREAM_END."""
    chunk = UnifiedMessage.chunk(
        payload, sender, stream_id="s1", sequence_no=seq, total_chunks=None
    )
    assert chunk.type == MessageType.STREAM_CHUNK
    assert chunk.sequence_no == seq
    assert chunk.correlation_id == "s1"
    end = UnifiedMessage.chunk(
        payload, sender, stream_id="s1", sequence_no=seq, total_chunks=seq + 1
    )
    assert end.type == MessageType.STREAM_END
    assert end.total_chunks == seq + 1


# ---------------------------------------------------------------------------
# codec.encode / decode
# ---------------------------------------------------------------------------


@given(_json_payload)
@settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
def test_codec_json_roundtrip(payload: dict) -> None:
    wire = encode(payload, "json")
    assert isinstance(wire, bytes)
    restored = decode(wire)
    assert restored == payload


@given(_json_payload)
@settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
def test_codec_decode_handles_only_json_form_when_msgpack_unavailable(
    payload: dict,
) -> None:
    """先頭 byte が `{` の場合は JSON として安全に decode できる."""
    wire = json.dumps(payload).encode()
    assert wire[0:1] == b"{"
    restored = decode(wire)
    assert restored == payload


def test_codec_decode_empty_raises() -> None:
    """空 bytes の decode は明示的に ValueError (silently 空 dict を返さない)."""
    import pytest

    with pytest.raises(ValueError):
        decode(b"")


@given(st.text(min_size=1, max_size=20))
def test_codec_encode_unknown_codec_raises(codec_name: str) -> None:
    """未知 codec 名は ValueError. JSON / msgpack 以外は受けない."""
    if codec_name in {"json", "msgpack"}:
        return  # skip known codecs
    import pytest

    with pytest.raises((ValueError, RuntimeError)):
        encode({"x": 1}, codec_name)
