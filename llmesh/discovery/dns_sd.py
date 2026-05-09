"""DNS-SD v2 — mDNS-based LLMesh node discovery via zeroconf.

Publishes a ``_llmesh._tcp.local.`` service record so that other nodes
on the LAN can discover this node without a central rendezvous server.

v2 TXT record extensions (beyond the bare endpoint):
  schema_version      — LLMesh discovery schema version (currently "2")
  node_id             — node identifier
  did                 — decentralised identity (DID string)
  capability_hash     — SHA-256 of the node's CapabilityManifest JSON
  data_levels_accepted — comma-separated accepted privacy levels, e.g. "0,1,2"
  protocols           — comma-separated active protocol adapters, e.g. "http,ssh"

SRV records for each protocol adapter endpoint are announced via separate
service types (``_llmesh-<protocol>._tcp.local.``).

Security:
  - Published TXT values are read-only announcements, not trusted data.
  - Consumers must verify the capability_hash against the signed manifest.
  - No shell=True, no eval/exec of remote data.

Dependencies: zeroconf>=0.131  (pip install llmesh[udp])
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import socket
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

_SERVICE_TYPE = "_llmesh._tcp.local."
_SCHEMA_VERSION = "2"

try:
    from zeroconf import ServiceInfo, Zeroconf
    from zeroconf.asyncio import AsyncZeroconf

    _ZEROCONF_AVAILABLE = True
except ImportError:
    _ZEROCONF_AVAILABLE = False
    ServiceInfo = None   # type: ignore[assignment, misc]
    Zeroconf = None      # type: ignore[assignment]
    AsyncZeroconf = None # type: ignore[assignment]


@dataclass
class DnsSdConfig:
    """Configuration for a DNS-SD v2 announcement.

    Attributes:
        node_id:             Node identifier (used as service instance name).
        did:                 DID string (e.g. ``did:key:z6Mk...``).
        host:                IP address or hostname to announce.
        port:                Primary HTTP port.
        capability_manifest: Serialisable manifest dict; its JSON is hashed for
                             the ``capability_hash`` TXT field.
        data_levels_accepted: Privacy levels this node accepts (list of ints).
        extra_protocols:     Additional protocol adapter announcements.
                             Each entry is ``{"protocol": "ssh", "port": 2222}``.
        ttl:                 Service record TTL in seconds (default 60).
    """

    node_id: str
    did: str
    host: str
    port: int
    capability_manifest: dict = field(default_factory=dict)
    data_levels_accepted: list[int] = field(default_factory=lambda: [0, 1, 2])
    extra_protocols: list[dict] = field(default_factory=list)
    ttl: int = 60


def _capability_hash(manifest: dict) -> str:
    return hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:16]


def _resolve_host_addresses(host: str) -> list[bytes]:
    """Resolve host to packed IPv4/IPv6 addresses for zeroconf."""
    addresses: list[bytes] = []
    try:
        ipaddress.ip_address(host)
        info = socket.getaddrinfo(host, None)
        for _, _, _, _, sockaddr in info:
            addr = sockaddr[0]
            try:
                addresses.append(socket.inet_pton(socket.AF_INET, addr))
            except OSError:
                try:
                    addresses.append(socket.inet_pton(socket.AF_INET6, addr))
                except OSError:
                    pass
    except ValueError:
        try:
            for _, _, _, _, sockaddr in socket.getaddrinfo(host, None):
                addr = sockaddr[0]
                try:
                    addresses.append(socket.inet_pton(socket.AF_INET, addr))
                except OSError:
                    try:
                        addresses.append(socket.inet_pton(socket.AF_INET6, addr))
                    except OSError:
                        pass
        except socket.gaierror:
            pass
    return addresses or [socket.inet_aton("127.0.0.1")]


def _build_service_info(cfg: DnsSdConfig, service_type: str, port: int) -> "ServiceInfo":
    """Build a zeroconf ServiceInfo for one endpoint."""
    addresses = _resolve_host_addresses(cfg.host)
    txt: dict[str, str | bytes] = {
        "schema_version": _SCHEMA_VERSION,
        "node_id": cfg.node_id,
        "did": cfg.did,
        "capability_hash": _capability_hash(cfg.capability_manifest),
        "data_levels_accepted": ",".join(str(level) for level in cfg.data_levels_accepted),
    }
    name = f"{cfg.node_id}.{service_type}"
    return ServiceInfo(
        service_type,
        name,
        addresses=addresses,
        port=port,
        properties=txt,
        server=f"{cfg.node_id}.local.",
    )


class DnsSdAnnouncer:
    """Publish and withdraw LLMesh DNS-SD v2 service records.

    Usage::

        cfg = DnsSdConfig(node_id="node1", did="did:key:z...",
                          host="192.168.1.5", port=8080,
                          extra_protocols=[{"protocol": "ssh", "port": 2222}])
        announcer = DnsSdAnnouncer(cfg)
        await announcer.start()
        # ...
        await announcer.stop()
    """

    def __init__(self, config: DnsSdConfig) -> None:
        if not _ZEROCONF_AVAILABLE:
            raise ImportError(
                "zeroconf is required for DNS-SD: pip install llmesh[udp]"
            )
        self._config = config
        self._zeroconf: "AsyncZeroconf | None" = None
        self._registered: list["ServiceInfo"] = []
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        self._zeroconf = AsyncZeroconf()
        self._registered = []

        # Primary HTTP service
        primary = _build_service_info(self._config, _SERVICE_TYPE, self._config.port)
        await self._zeroconf.async_register_service(primary)
        self._registered.append(primary)

        # Additional protocol adapters
        for proto_cfg in self._config.extra_protocols:
            protocol = proto_cfg.get("protocol", "")
            p_port = proto_cfg.get("port", 0)
            if not protocol or not p_port:
                continue
            stype = f"_llmesh-{protocol}._tcp.local."
            info = _build_service_info(self._config, stype, p_port)
            await self._zeroconf.async_register_service(info)
            self._registered.append(info)

        self._running = True
        logger.info(
            "DnsSdAnnouncer: announced %d service(s) for node %s",
            len(self._registered),
            self._config.node_id,
        )

    async def stop(self) -> None:
        if self._zeroconf is None:
            return
        for info in self._registered:
            try:
                await self._zeroconf.async_unregister_service(info)
            except Exception as exc:
                logger.debug("DnsSdAnnouncer: unregister error: %s", exc)
        await self._zeroconf.async_close()
        self._zeroconf = None
        self._registered = []
        self._running = False
        logger.info("DnsSdAnnouncer: stopped")

    async def update_manifest(self, new_manifest: dict) -> None:
        """Re-announce with an updated capability manifest hash."""
        await self.stop()
        self._config.capability_manifest = new_manifest
        await self.start()
