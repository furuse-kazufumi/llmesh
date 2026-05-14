"""Property-based tests using Hypothesis (v2.0.0+).

Hypothesis generates hundreds of randomised inputs to flush out edge cases
that example-based tests miss — short payloads, extreme values, malformed
metadata, unicode in topics, etc.  These tests run as part of the normal
pytest suite; failures are minimised to a small reproducer.
"""
from __future__ import annotations

import struct
import pytest
from hypothesis import given, settings, strategies as st

from llmesh.industrial.sensor_3d.point_cloud import PointCloud
from llmesh.industrial.sensor_3d.event_adapter import (
    DvsEvent,
    encode_dvs_events,
    decode_dvs_events,
    _EVENT_BYTES,
)
from llmesh.industrial.mqtt_adapter import _mqtt_topic_match, TopicSpec
from llmesh.industrial.ethercat_adapter import (
    SlaveSpec,
    _STRUCT_FMT,
)
from llmesh.industrial.pipeline import IndustrialPipeline
from llmesh.industrial.sensor_event import SensorEvent

# Bound test runtime — production use can override to deeper exploration.
_FAST = settings(max_examples=50, deadline=None)


# ---------------------------------------------------------------------------
# PointCloud roundtrip
# ---------------------------------------------------------------------------

@_FAST
@given(st.lists(
    st.tuples(
        st.floats(width=32, allow_nan=False, allow_infinity=False),
        st.floats(width=32, allow_nan=False, allow_infinity=False),
        st.floats(width=32, allow_nan=False, allow_infinity=False),
    ),
    max_size=200,
))
def test_point_cloud_roundtrip_arbitrary(points):
    pc = PointCloud(points=points)
    decoded = PointCloud.from_bytes(pc.to_bytes())
    assert decoded.count == len(points)
    for orig, dec in zip(decoded.points, points):
        # Allow float32 rounding tolerance (~1e-6 for normal magnitudes)
        for o, d in zip(orig, dec):
            assert abs(o - d) <= max(abs(d) * 1e-5, 1e-5)


@_FAST
@given(st.binary(min_size=0, max_size=2400))
def test_point_cloud_from_arbitrary_bytes(data):
    """Decoding arbitrary bytes must never raise — only complete records consumed."""
    pc = PointCloud.from_bytes(data)
    assert pc.count == len(data) // 12


# ---------------------------------------------------------------------------
# DVS encode/decode roundtrip
# ---------------------------------------------------------------------------

_dvs_event_strat = st.builds(
    DvsEvent,
    x=st.integers(min_value=0, max_value=0xFFFF),
    y=st.integers(min_value=0, max_value=0xFFFF),
    t_us=st.integers(min_value=0, max_value=0xFFFF_FFFF),
    polarity=st.booleans(),
)


@_FAST
@given(st.lists(_dvs_event_strat, max_size=200))
def test_dvs_roundtrip_arbitrary(events):
    decoded = decode_dvs_events(encode_dvs_events(events))
    assert decoded == events


@_FAST
@given(st.binary(min_size=0, max_size=900))
def test_dvs_decode_arbitrary_bytes_does_not_raise(data):
    decoded = decode_dvs_events(data)
    expected_n = min(len(data) // _EVENT_BYTES, 1_000_000)
    assert len(decoded) == expected_n


# ---------------------------------------------------------------------------
# MQTT topic matcher
# ---------------------------------------------------------------------------

@_FAST
@given(st.text(
    alphabet=st.characters(min_codepoint=0x21, max_codepoint=0x7e,
                           blacklist_characters=("\x00", "+", "#", "/")),
    min_size=1, max_size=10,
))
def test_mqtt_topic_exact_match(level):
    """Any non-wildcard topic matches itself exactly."""
    topic = f"a/{level}/c"
    assert _mqtt_topic_match(topic, topic) is True


@_FAST
@given(
    st.text(min_size=1, max_size=10,
            alphabet=st.characters(min_codepoint=0x21, max_codepoint=0x7e,
                                   blacklist_characters=("\x00", "+", "#", "/"))),
    st.text(min_size=1, max_size=10,
            alphabet=st.characters(min_codepoint=0x21, max_codepoint=0x7e,
                                   blacklist_characters=("\x00", "+", "#", "/"))),
)
def test_mqtt_single_wildcard_arbitrary(a, b):
    """+ matches exactly one level regardless of content."""
    assert _mqtt_topic_match("x/+/y", f"x/{a}/y") is True
    assert _mqtt_topic_match(f"+/{b}", f"{a}/{b}") is True


# ---------------------------------------------------------------------------
# TopicSpec validation
# ---------------------------------------------------------------------------

@_FAST
@given(st.text(
    alphabet=st.characters(blacklist_characters=("\x00",),
                           blacklist_categories=("Cs",)),  # exclude surrogates
    min_size=1, max_size=200,
))
def test_topic_spec_accepts_valid_topics(topic):
    spec = TopicSpec(topic=topic, sensor_id="s1")
    assert spec.topic == topic


@_FAST
@given(st.integers().filter(lambda q: q not in (0, 1, 2)))
def test_topic_spec_rejects_invalid_qos(qos):
    with pytest.raises(ValueError):
        TopicSpec(topic="t", sensor_id="s1", qos=qos)


# ---------------------------------------------------------------------------
# EtherCAT SlaveSpec validation
# ---------------------------------------------------------------------------

@_FAST
@given(
    st.integers(min_value=0, max_value=255),
    st.sampled_from(list(_STRUCT_FMT)),
    st.integers(min_value=0, max_value=1023),
)
def test_slave_spec_accepts_valid(slave_pos, dt, byte_offset):
    spec = SlaveSpec(slave_pos=slave_pos, sensor_id="s",
                     data_type=dt, byte_offset=byte_offset)
    assert spec.slave_pos == slave_pos
    assert spec.data_type == dt


@_FAST
@given(st.integers(max_value=-1))
def test_slave_spec_rejects_negative_pos(slave_pos):
    with pytest.raises(ValueError, match="slave_pos"):
        SlaveSpec(slave_pos=slave_pos, sensor_id="s")


# ---------------------------------------------------------------------------
# IndustrialPipeline value extraction
# ---------------------------------------------------------------------------

@_FAST
@given(st.floats(allow_nan=False, allow_infinity=False, width=64))
def test_pipeline_extracts_float64_payload(value):
    p = IndustrialPipeline()
    p.attach_cusum("s1", target=0.0, k=0.5, h=4.0, sigma=1.0)
    ev = SensorEvent.create(
        sensor_id="s1", protocol="t",
        payload=struct.pack("<d", value),
    )
    d = p.process(ev)
    assert d.evidence["value"] == pytest.approx(value, nan_ok=False)


@_FAST
@given(st.floats(allow_nan=False, allow_infinity=False, width=64))
def test_pipeline_uses_physical_value_metadata(value):
    p = IndustrialPipeline()
    p.attach_cusum("s1", target=0.0, k=0.5, h=4.0, sigma=1.0)
    ev = SensorEvent.create(
        sensor_id="s1", protocol="t", payload=b"",
        metadata={"physical_value": value},
    )
    d = p.process(ev)
    assert d.evidence["value"] == pytest.approx(value)


# ---------------------------------------------------------------------------
# CAN FrameSpec validation (v2.1)
# ---------------------------------------------------------------------------

from llmesh.industrial.can_adapter import (
    FrameSpec as CANFrameSpec, _CAN_STD_MAX, _CAN_EXT_MAX,
)


@_FAST
@given(
    st.integers(min_value=0, max_value=_CAN_STD_MAX),
    st.sampled_from(list(_STRUCT_FMT)),
    st.integers(min_value=0, max_value=63),
)
def test_can_frame_spec_accepts_standard_id(can_id, dt, byte_offset):
    spec = CANFrameSpec(can_id=can_id, sensor_id="s",
                        data_type=dt, byte_offset=byte_offset, extended=False)
    assert spec.can_id == can_id


@_FAST
@given(st.integers(min_value=_CAN_STD_MAX + 1, max_value=_CAN_EXT_MAX))
def test_can_frame_spec_standard_rejects_overflow(can_id):
    with pytest.raises(ValueError, match="can_id"):
        CANFrameSpec(can_id=can_id, sensor_id="s", extended=False)


@_FAST
@given(st.integers(min_value=0, max_value=_CAN_EXT_MAX))
def test_can_frame_spec_accepts_extended_id(can_id):
    spec = CANFrameSpec(can_id=can_id, sensor_id="s", extended=True)
    assert spec.extended is True


# ---------------------------------------------------------------------------
# BACnet ObjectSpec validation (v2.4)
# ---------------------------------------------------------------------------

from llmesh.industrial.bacnet_adapter import (
    BACnetObjectSpec, _SUPPORTED_OBJECT_TYPES, _DEVICE_ID_MAX,
)


@_FAST
@given(
    st.integers(min_value=0, max_value=_DEVICE_ID_MAX),
    st.sampled_from(list(_SUPPORTED_OBJECT_TYPES)),
    st.integers(min_value=0, max_value=10_000),
)
def test_bacnet_object_spec_accepts_valid(device_id, ot, instance):
    s = BACnetObjectSpec(device_id=device_id, object_type=ot,
                          instance=instance, sensor_id="s")
    assert s.device_id == device_id
    assert s.object_type == ot


@_FAST
@given(st.integers(min_value=_DEVICE_ID_MAX + 1, max_value=_DEVICE_ID_MAX * 4))
def test_bacnet_object_spec_rejects_overflow_device_id(device_id):
    with pytest.raises(ValueError, match="device_id"):
        BACnetObjectSpec(device_id=device_id, object_type="analog-input",
                          instance=0, sensor_id="s")


# ---------------------------------------------------------------------------
# DvsEvent invariants (v1.7) — ensure encode/decode preserves all fields
# ---------------------------------------------------------------------------

@_FAST
@given(st.lists(_dvs_event_strat, min_size=0, max_size=500))
def test_dvs_decode_preserves_event_count(events):
    encoded = encode_dvs_events(events)
    decoded = decode_dvs_events(encoded)
    assert len(decoded) == len(events)
    for o, d in zip(events, decoded):
        assert (o.x, o.y, o.t_us, o.polarity) == (d.x, d.y, d.t_us, d.polarity)


# ---------------------------------------------------------------------------
# IndustrialMetrics counter invariants (v3 preview)
# ---------------------------------------------------------------------------

from llmesh.industrial.metrics import IndustrialMetrics


@_FAST
@given(st.lists(st.floats(min_value=0.0, max_value=1e6,
                          allow_nan=False, allow_infinity=False),
                min_size=1, max_size=100))
def test_metrics_counter_monotonic(amounts):
    """A sequence of non-negative increments must equal their sum."""
    m = IndustrialMetrics()
    for a in amounts:
        m.increment("c", amount=a)
    assert m.get("c") == pytest.approx(sum(amounts))


# ---------------------------------------------------------------------------
# TenantScope invariants (v3 preview)
# ---------------------------------------------------------------------------

from llmesh.industrial.tenant import TenantScope, validate_tenant_id


@_FAST
@given(st.text(
    alphabet=st.characters(min_codepoint=0x30, max_codepoint=0x7a,
                           whitelist_categories=("Ll", "Lu", "Nd"),
                           whitelist_characters=("_", "-")),
    min_size=1, max_size=64,
))
def test_tenant_id_alphanumeric_dash_underscore_accepted(tid):
    validate_tenant_id(tid)
    s = TenantScope(tid)
    assert s.tenant_id == tid
