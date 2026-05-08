"""Cross-platform compatibility tests (v2.5+).

Verifies that LLMesh behaves identically on every supported platform:

1. Core Python module imports succeed regardless of OS
2. Optional Rust extension is detected correctly
3. Pure-Python and Rust paths produce *byte-identical* output
4. Platform-specific feature flags are reported honestly
5. Industrial adapters that gracefully degrade do so consistently

These tests run on every CI matrix combination and act as the canary
for platform regressions.
"""
from __future__ import annotations

import importlib
import platform
import struct
import sys
import pytest
from hypothesis import given, settings, strategies as st

from llmesh.industrial.sensor_3d.point_cloud import PointCloud
from llmesh.industrial.sensor_3d.event_adapter import (
    DvsEvent, encode_dvs_events, decode_dvs_events,
    _EVENT_BYTES, _EVENT_STRUCT_FMT,
)


_FAST = settings(max_examples=50, deadline=None)


# ---------------------------------------------------------------------------
# Section 1 — Core import / platform sanity
# ---------------------------------------------------------------------------

class TestCoreImports:
    """Every core module must import on any platform."""

    @pytest.mark.parametrize("module", [
        "llmesh.industrial",
        "llmesh.industrial.sensor_event",
        "llmesh.industrial.pipeline",
        "llmesh.industrial.metrics",
        "llmesh.industrial.tenant",
        "llmesh.industrial.tracing",
        "llmesh.industrial.adapter_protocol",
        "llmesh.industrial.sensor_3d",
        "llmesh.industrial.sensor_3d.point_cloud",
        "llmesh.industrial.sensor_3d.spatial_summarizer",
    ])
    def test_module_imports(self, module):
        importlib.import_module(module)


class TestPlatformProbe:
    def test_python_version_supported(self):
        assert sys.version_info >= (3, 11)

    def test_machine_architecture_known(self):
        m = platform.machine().lower()
        # We support x86_64 / amd64 (alias) / arm64 / aarch64 / armv7l
        assert m in {"x86_64", "amd64", "arm64", "aarch64", "armv7l", "i686"}, \
            f"unknown machine: {m}"

    def test_os_supported(self):
        s = platform.system()
        assert s in {"Linux", "Windows", "Darwin", "FreeBSD"}, \
            f"unsupported OS: {s}"


# ---------------------------------------------------------------------------
# Section 2 — Rust extension detection
# ---------------------------------------------------------------------------

class TestRustExtension:
    def test_rust_module_optional(self):
        """Importing llmesh_rust must not raise; absence is acceptable."""
        try:
            import llmesh_rust
            assert hasattr(llmesh_rust, "__version__")
        except ImportError:
            pytest.skip("llmesh_rust not built — pure-Python fallback active")

    def test_pointcloud_module_reports_status(self):
        from llmesh.industrial.sensor_3d import point_cloud as pc_mod
        assert isinstance(pc_mod._RUST_AVAILABLE, bool)

    def test_event_adapter_module_reports_status(self):
        from llmesh.industrial.sensor_3d import event_adapter as ev_mod
        assert isinstance(ev_mod._RUST_AVAILABLE, bool)


# ---------------------------------------------------------------------------
# Section 3 — Pure Python vs Rust byte equivalence
# ---------------------------------------------------------------------------

def _pc_to_bytes_python(points):
    """Reference pure-Python implementation."""
    buf = bytearray(len(points) * 12)
    for i, (x, y, z) in enumerate(points):
        struct.pack_into("<fff", buf, i * 12, float(x), float(y), float(z))
    return bytes(buf)


def _dvs_encode_python(events):
    buf = bytearray(len(events) * _EVENT_BYTES)
    for i, ev in enumerate(events):
        struct.pack_into(
            _EVENT_STRUCT_FMT,
            buf, i * _EVENT_BYTES,
            ev.x, ev.y, ev.t_us, int(ev.polarity),
        )
    return bytes(buf)


class TestRustPythonByteEquivalence:
    """Rust output must be byte-identical to the Python reference."""

    @_FAST
    @given(st.lists(
        st.tuples(
            st.floats(width=32, allow_nan=False, allow_infinity=False),
            st.floats(width=32, allow_nan=False, allow_infinity=False),
            st.floats(width=32, allow_nan=False, allow_infinity=False),
        ),
        min_size=0, max_size=100,
    ))
    def test_pointcloud_to_bytes_byte_identical(self, points):
        pc = PointCloud(points=points)
        actual = pc.to_bytes()
        expected = _pc_to_bytes_python(points)
        assert actual == expected

    @_FAST
    @given(st.lists(
        st.builds(
            DvsEvent,
            x=st.integers(min_value=0, max_value=0xFFFF),
            y=st.integers(min_value=0, max_value=0xFFFF),
            t_us=st.integers(min_value=0, max_value=0xFFFFFFFF),
            polarity=st.booleans(),
        ),
        min_size=0, max_size=100,
    ))
    def test_dvs_encode_byte_identical(self, events):
        actual = encode_dvs_events(events)
        expected = _dvs_encode_python(events)
        assert actual == expected

    @_FAST
    @given(st.lists(
        st.builds(
            DvsEvent,
            x=st.integers(min_value=0, max_value=0xFFFF),
            y=st.integers(min_value=0, max_value=0xFFFF),
            t_us=st.integers(min_value=0, max_value=0xFFFFFFFF),
            polarity=st.booleans(),
        ),
        min_size=0, max_size=100,
    ))
    def test_dvs_roundtrip_lossless(self, events):
        decoded = decode_dvs_events(encode_dvs_events(events))
        assert decoded == events


# ---------------------------------------------------------------------------
# Section 4 — Endianness invariants (defends against big-endian regressions)
# ---------------------------------------------------------------------------

class TestEndianness:
    """LLMesh wire formats are always little-endian regardless of host."""

    def test_pointcloud_lefirst_byte_is_low_mantissa(self):
        # 1.0 as little-endian float32 = 00 00 80 3f
        pc = PointCloud(points=[(1.0, 0.0, 0.0)])
        raw = pc.to_bytes()
        assert raw[:4] == b"\x00\x00\x80\x3f"

    def test_dvs_event_low_byte_first(self):
        ev = DvsEvent(x=0x1234, y=0x5678, t_us=0xAABBCCDD, polarity=True)
        raw = encode_dvs_events([ev])
        # x = 0x1234 LE → 34 12
        assert raw[0:2] == b"\x34\x12"
        # y = 0x5678 LE → 78 56
        assert raw[2:4] == b"\x56\x78" or raw[2:4] == b"\x78\x56"
        # explicit:
        assert raw[2:4] == b"\x78\x56"
        # t_us = 0xAABBCCDD LE → DD CC BB AA
        assert raw[4:8] == b"\xDD\xCC\xBB\xAA"
        # polarity = True → 1
        assert raw[8] == 1


# ---------------------------------------------------------------------------
# Section 5 — Optional adapter degradation
# ---------------------------------------------------------------------------

class TestOptionalAdapters:
    """Adapters depending on optional packages must raise a clear error."""

    @pytest.mark.parametrize("mod_path,flag", [
        ("llmesh.industrial.modbus_adapter",   "_PYMODBUS_AVAILABLE"),
        ("llmesh.industrial.opcua_adapter",    "_ASYNCUA_AVAILABLE"),
        ("llmesh.industrial.mqtt_adapter",     "_PAHO_AVAILABLE"),
        ("llmesh.industrial.ethercat_adapter", "_PYSOEM_AVAILABLE"),
        ("llmesh.industrial.can_adapter",      "_CAN_AVAILABLE"),
        ("llmesh.industrial.bacnet_adapter",   "_BACPYPES_AVAILABLE"),
    ])
    def test_module_exposes_availability_flag(self, mod_path, flag):
        m = importlib.import_module(mod_path)
        assert hasattr(m, flag), f"{mod_path} missing {flag}"
        assert isinstance(getattr(m, flag), bool)


# ---------------------------------------------------------------------------
# Section 6 — Reproducibility: byte output is deterministic
# ---------------------------------------------------------------------------

class TestDeterminism:
    @_FAST
    @given(st.lists(
        st.tuples(
            st.floats(width=32, allow_nan=False, allow_infinity=False),
            st.floats(width=32, allow_nan=False, allow_infinity=False),
            st.floats(width=32, allow_nan=False, allow_infinity=False),
        ),
        min_size=1, max_size=50,
    ))
    def test_pointcloud_encode_is_deterministic(self, points):
        a = PointCloud(points=points).to_bytes()
        b = PointCloud(points=points).to_bytes()
        assert a == b

    def test_synthetic_dataset_byte_reproducible(self, tmp_path):
        from tools.gen_synthetic_dataset import _run as gen_run
        out_a = tmp_path / "a"
        out_b = tmp_path / "b"
        gen_run("dvs", 5, out_a, seed=123)
        gen_run("dvs", 5, out_b, seed=123)
        for fa, fb in zip(sorted(out_a.iterdir()), sorted(out_b.iterdir())):
            assert fa.read_bytes() == fb.read_bytes()


# ---------------------------------------------------------------------------
# Section 7 — Bounded resource usage on extreme inputs
# ---------------------------------------------------------------------------

class TestResourceBounds:
    def test_dvs_decode_caps_at_max_events(self):
        """Pathological huge .dvs.bin must not cause OOM."""
        from llmesh.industrial.sensor_3d.event_adapter import _MAX_EVENTS_PER_BATCH
        # 2 × cap of synthetic data
        data = b"\x00" * (_EVENT_BYTES * (_MAX_EVENTS_PER_BATCH + 100))
        decoded = decode_dvs_events(data)
        assert len(decoded) == _MAX_EVENTS_PER_BATCH

    def test_pointcloud_truncates_partial_records(self):
        """Trailing bytes < 12 must be silently dropped, not error."""
        pc = PointCloud.from_bytes(b"\x00" * 25)  # 2 full points + 1 byte
        assert pc.count == 2
