"""Shared fixtures for LLMesh tests."""
from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest
from hypothesis import settings

from llmesh.protocol.message import NodeAddress

sys.path.insert(0, str(Path(__file__).parent))
from helpers import _alloc_port  # noqa: F401 — re-exported for tests that need it


# Windows での初回 file IO / import / hypothesis warmup で 200ms deadline を
# 超えるケースが頻発するため, llmesh の全 property test で deadline を
# 無効化する. 個別 test で `@settings(deadline=...)` を上書きすれば優先.
settings.register_profile("local-flaky-safe", deadline=None)
settings.load_profile("local-flaky-safe")


@pytest.fixture
def free_port() -> int:
    """A free TCP port on 127.0.0.1."""
    return _alloc_port()


@pytest.fixture
def free_udp_port() -> int:
    """A free UDP port on 127.0.0.1."""
    return _alloc_port(socket.SOCK_DGRAM)


@pytest.fixture
def sender() -> NodeAddress:
    """Default sender address with ephemeral port."""
    return NodeAddress("127.0.0.1", 0)


# ---------------------------------------------------------------------------
# Industrial helpers (Phase A–G + v3 preview)
# ---------------------------------------------------------------------------

@pytest.fixture
def make_sensor_event():
    """Factory: build a SensorEvent with sensible defaults for tests."""
    from llmesh.industrial.sensor_event import SensorEvent

    def _factory(
        *,
        sensor_id: str = "test_sensor",
        protocol: str = "test",
        payload: bytes = b"",
        device_id: str = "test_device",
        sensor_type: str = "",
        unit: str = "",
        **metadata,
    ):
        return SensorEvent.create(
            sensor_id=sensor_id,
            protocol=protocol,
            payload=payload,
            device_id=device_id,
            sensor_type=sensor_type,
            unit=unit,
            metadata=dict(metadata),
        )

    return _factory


@pytest.fixture
def industrial_pipeline():
    """A fresh IndustrialPipeline ready for analyzer attachment."""
    from llmesh.industrial.pipeline import IndustrialPipeline
    return IndustrialPipeline()
