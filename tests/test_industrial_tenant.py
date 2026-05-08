"""Tests for TenantScope / TenantRegistry / tenant_event (v3 preview)."""
from __future__ import annotations

import pytest

from llmesh.industrial.tenant import (
    TenantScope, TenantRegistry, tenant_event, validate_tenant_id,
)
from llmesh.industrial.sensor_event import SensorEvent


def _ev(sensor_id="acme_pressure", device_id="line1", **meta) -> SensorEvent:
    return SensorEvent.create(
        sensor_id=sensor_id, protocol="test", payload=b"",
        device_id=device_id, metadata=meta,
    )


class TestValidateTenantId:
    @pytest.mark.parametrize("tid", ["acme", "ACME-01", "u_b_c", "x" * 64])
    def test_valid(self, tid):
        validate_tenant_id(tid)  # no exception

    @pytest.mark.parametrize("tid", ["", "has space", "has/slash", "x" * 65, "u@b"])
    def test_invalid(self, tid):
        with pytest.raises(ValueError):
            validate_tenant_id(tid)


class TestTenantEvent:
    def test_prefixes_sensor_and_device(self):
        ev = _ev()
        scoped = tenant_event(ev, "acme")
        assert scoped.sensor_id == "acme/acme_pressure"
        assert scoped.device_id == "acme/line1"
        assert scoped.metadata["tenant_id"] == "acme"

    def test_empty_device_id_replaced_with_tenant(self):
        ev = SensorEvent.create(sensor_id="s", protocol="t", payload=b"")
        scoped = tenant_event(ev, "acme")
        assert scoped.device_id == "acme"

    def test_invalid_tenant_id_rejected(self):
        with pytest.raises(ValueError):
            tenant_event(_ev(), "bad/id")


class TestTenantScope:
    def test_no_filter_allows_all(self):
        scope = TenantScope("acme")
        assert scope.allows(_ev(sensor_id="anything"))

    def test_prefix_filter(self):
        scope = TenantScope("acme", allow_sensor_prefixes={"acme_"})
        assert scope.allows(_ev(sensor_id="acme_pressure"))
        assert not scope.allows(_ev(sensor_id="other_pressure"))

    def test_wrap_callback_forwards_allowed(self):
        scope = TenantScope("acme", allow_sensor_prefixes={"acme_"})
        seen: list[SensorEvent] = []
        cb = scope.wrap_callback(seen.append)
        cb(_ev(sensor_id="acme_p1"))
        assert len(seen) == 1
        assert seen[0].sensor_id == "acme/acme_p1"
        assert scope.forwarded == 1
        assert scope.dropped == 0

    def test_wrap_callback_drops_disallowed(self):
        scope = TenantScope("acme", allow_sensor_prefixes={"acme_"})
        seen: list[SensorEvent] = []
        cb = scope.wrap_callback(seen.append)
        cb(_ev(sensor_id="other_p1"))
        assert seen == []
        assert scope.dropped == 1

    def test_callback_exception_does_not_propagate(self):
        scope = TenantScope("acme")
        cb = scope.wrap_callback(lambda e: (_ for _ in ()).throw(RuntimeError("boom")))
        cb(_ev())  # must not raise


class TestTenantRegistry:
    def test_register_and_lookup(self):
        reg = TenantRegistry()
        s = TenantScope("acme")
        reg.register(s)
        assert reg.get("acme") is s

    def test_double_register_rejected(self):
        reg = TenantRegistry()
        reg.register(TenantScope("acme"))
        with pytest.raises(ValueError, match="already"):
            reg.register(TenantScope("acme"))

    def test_unregister(self):
        reg = TenantRegistry()
        reg.register(TenantScope("acme"))
        reg.unregister("acme")
        assert reg.get("acme") is None
        # idempotent
        reg.unregister("acme")

    def test_fanout_counts_allowed_tenants(self):
        reg = TenantRegistry()
        reg.register(TenantScope("acme", allow_sensor_prefixes={"acme_"}))
        reg.register(TenantScope("globex", allow_sensor_prefixes={"globex_"}))
        reg.register(TenantScope("any"))    # no filter — accepts all
        assert reg.fanout(_ev(sensor_id="acme_p1")) == 2  # acme + any
        assert reg.fanout(_ev(sensor_id="globex_p1")) == 2  # globex + any
        assert reg.fanout(_ev(sensor_id="other_p1")) == 1  # any only

    def test_all(self):
        reg = TenantRegistry()
        reg.register(TenantScope("a"))
        reg.register(TenantScope("b"))
        assert sorted(s.tenant_id for s in reg.all()) == ["a", "b"]
