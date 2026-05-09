"""TenantScope — multi-tenant namespace isolation for Industrial adapters (v3 preview).

Wraps adapter callbacks with a *tenant prefix* so multiple tenants
(factories, customers, business units) can share a single LLMesh node
without colliding on sensor IDs, MQTT topics, OPC-UA node IDs, etc.

Usage::

    from llmesh.industrial.tenant import TenantScope, tenant_event

    # Wrap an existing pipeline so all events get a tenant prefix
    tenant = TenantScope(tenant_id="customer_acme",
                         allow_sensor_prefixes={"acme_"})
    pipeline.on_diagnosis(tenant.wrap_callback(forward_to_acme_dashboard))

    # Convert raw SensorEvents into tenant-prefixed copies
    scoped = tenant_event(raw_event, tenant.tenant_id)
    # scoped.sensor_id == "customer_acme/<original>"

Security invariants
-------------------
- Tenant IDs validated against `[a-zA-Z0-9_\\-]{1,64}`.
- Cross-tenant events (failing the allow-prefix check) are dropped with a
  metric increment (no exception → adapter loop unaffected).
- No shell=True, eval, exec, pickle.
"""
from __future__ import annotations

import dataclasses
import logging
import re
from collections.abc import Callable, Iterable
from typing import TypeVar

from llmesh.industrial.sensor_event import SensorEvent

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

# Tenant ID character set — alphanumerics + dash + underscore.  Matches
# common Kubernetes namespace conventions.
_TENANT_ID_RE = re.compile(r"^[a-zA-Z0-9_\-]{1,64}$")

# Separator between tenant prefix and original identifier.  Picked to be
# safe in MQTT topics, OPC-UA NodeId strings, and filesystem paths.
_TENANT_SEPARATOR = "/"

# Maximum number of distinct tenants we keep registered for cross-tenant
# diagnostics — any number above this raises (defense against unbounded
# growth in long-lived processes).
_MAX_REGISTERED_TENANTS = 4096


T = TypeVar("T")


def validate_tenant_id(tenant_id: str) -> None:
    if not _TENANT_ID_RE.match(tenant_id):
        raise ValueError(
            f"invalid tenant_id {tenant_id!r}; must match {_TENANT_ID_RE.pattern}"
        )


def tenant_event(event: SensorEvent, tenant_id: str) -> SensorEvent:
    """Return a copy of *event* with sensor_id and device_id tenant-prefixed.

    The original metadata gains a ``"tenant_id"`` key so downstream
    consumers can route accordingly.
    """
    validate_tenant_id(tenant_id)
    new_meta = dict(event.metadata)
    new_meta["tenant_id"] = tenant_id
    return dataclasses.replace(
        event,
        sensor_id=f"{tenant_id}{_TENANT_SEPARATOR}{event.sensor_id}",
        device_id=(
            f"{tenant_id}{_TENANT_SEPARATOR}{event.device_id}"
            if event.device_id else tenant_id
        ),
        metadata=new_meta,
    )


# ---------------------------------------------------------------------------
# TenantScope
# ---------------------------------------------------------------------------

class TenantScope:
    """Isolation policy for events flowing through a single tenant.

    Parameters
    ----------
    tenant_id:
        Stable tenant identifier (e.g. customer name, factory site code).
    allow_sensor_prefixes:
        Optional iterable of allowed *original* sensor_id prefixes for this
        tenant.  Events whose sensor_id does not start with any of these
        prefixes are silently dropped.  Empty / None disables the filter.
    """

    def __init__(
        self,
        tenant_id: str,
        *,
        allow_sensor_prefixes: Iterable[str] | None = None,
    ) -> None:
        validate_tenant_id(tenant_id)
        self.tenant_id = tenant_id
        self._allowed_prefixes: tuple[str, ...] = tuple(allow_sensor_prefixes or ())
        self._dropped_count = 0
        self._forwarded_count = 0

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    @property
    def dropped(self) -> int:
        return self._dropped_count

    @property
    def forwarded(self) -> int:
        return self._forwarded_count

    # ------------------------------------------------------------------
    # Filter
    # ------------------------------------------------------------------

    def allows(self, event: SensorEvent) -> bool:
        """Return True if *event* belongs to this tenant per the prefix policy."""
        if not self._allowed_prefixes:
            return True
        return any(event.sensor_id.startswith(p) for p in self._allowed_prefixes)

    # ------------------------------------------------------------------
    # Wrappers
    # ------------------------------------------------------------------

    def wrap_callback(
        self,
        callback: Callable[[SensorEvent], None],
    ) -> Callable[[SensorEvent], None]:
        """Return a callback that drops cross-tenant events and tenant-prefixes the rest."""
        def _scoped(event: SensorEvent) -> None:
            if not self.allows(event):
                self._dropped_count += 1
                return
            try:
                callback(tenant_event(event, self.tenant_id))
                self._forwarded_count += 1
            except Exception as exc:
                logger.error("TenantScope[%s] callback error: %s",
                             self.tenant_id, exc)
        return _scoped


# ---------------------------------------------------------------------------
# TenantRegistry
# ---------------------------------------------------------------------------

class TenantRegistry:
    """Process-wide registry of TenantScopes — useful for fan-out routing."""

    def __init__(self) -> None:
        self._scopes: dict[str, TenantScope] = {}

    def register(self, scope: TenantScope) -> None:
        if len(self._scopes) >= _MAX_REGISTERED_TENANTS:
            raise RuntimeError(
                f"TenantRegistry: tenant cardinality limit "
                f"({_MAX_REGISTERED_TENANTS}) reached"
            )
        if scope.tenant_id in self._scopes:
            raise ValueError(f"tenant {scope.tenant_id!r} already registered")
        self._scopes[scope.tenant_id] = scope

    def unregister(self, tenant_id: str) -> None:
        self._scopes.pop(tenant_id, None)

    def get(self, tenant_id: str) -> TenantScope | None:
        return self._scopes.get(tenant_id)

    def all(self) -> list[TenantScope]:
        return list(self._scopes.values())

    def fanout(self, event: SensorEvent) -> int:
        """Forward an event to every registered tenant that allows it.

        Returns the number of tenants the event was delivered to.
        """
        delivered = 0
        for scope in self._scopes.values():
            if scope.allows(event):
                delivered += 1
        return delivered
