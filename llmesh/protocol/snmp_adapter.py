"""SNMPAdapter — SNMPv3 read-only agent exposing LLMesh node health metrics.

Exposes an OID tree under enterprises.llmesh (1.3.6.1.4.1.99999):

  .1.1.0  nodeId              OctetString — node identifier
  .1.2.0  did                 OctetString — decentralised identity
  .1.3.0  activeConnections   Integer32
  .1.4.0  requestsTotal       Counter64
  .1.5.0  firewallBlocksTotal Counter64
  .1.6.0  auditChainValid     Integer32  (0=invalid, 1=valid)
  .1.7.0  nonceStoreSize      Integer32
  .1.8.0  trustedPeerCount    Integer32

Only SNMPv3 is accepted; v1/v2c requests are silently dropped.
SET operations are not permitted (read-only MIB).

A StatsProvider callable can be passed to SNMPAdapter; it is called
before every GET/GETNEXT to refresh the OID values.  The callable
must return a dict with any subset of the keys above.

Security:
  - SNMPv3 with MD5 + DES or SHA + AES (configurable)
  - No community strings (SNMPv1/v2c disabled at message-processing layer)
  - SET operations raise NoSuchObjectError (write_variables stub)
  - No shell=True, no eval/exec of remote data

Dependencies: pysnmp>=6.1  (pip install llmesh[mgmt])
"""
from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING

from .adapter import MessageHandler, ProtocolAdapter, TransportError
from .message import NodeAddress, UnifiedMessage

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Enterprise OID base: 1.3.6.1.4.1.99999 (private, for LLMesh)
_ENTERPRISE_BASE = (1, 3, 6, 1, 4, 1, 99999)
_MIB_BASE = _ENTERPRISE_BASE + (1,)

# OID → (sub-index, default_value, key_in_stats)
_OID_DEFS: dict[tuple, tuple[str, object]] = {
    _MIB_BASE + (1, 0): ("nodeId", b""),
    _MIB_BASE + (2, 0): ("did", b""),
    _MIB_BASE + (3, 0): ("activeConnections", 0),
    _MIB_BASE + (4, 0): ("requestsTotal", 0),
    _MIB_BASE + (5, 0): ("firewallBlocksTotal", 0),
    _MIB_BASE + (6, 0): ("auditChainValid", 1),
    _MIB_BASE + (7, 0): ("nonceStoreSize", 0),
    _MIB_BASE + (8, 0): ("trustedPeerCount", 0),
}

# Sorted OID list for GETNEXT traversal
_OID_ORDER: list[tuple] = sorted(_OID_DEFS)

StatsProvider = Callable[[], dict]

try:
    from pysnmp.entity import engine as _snmp_engine_mod
    from pysnmp.entity import config as _snmp_config
    from pysnmp.carrier.asyncio.dgram import udp as _udp_transport
    from pysnmp.entity.rfc3413 import cmdrsp as _cmdrsp, context as _snmp_context
    from pysnmp.proto import rfc1902 as _rfc1902
    from pysnmp.smi import instrum as _instrum, error as _smi_error

    _PYSNMP_AVAILABLE = True
except ImportError:
    _PYSNMP_AVAILABLE = False


# ---------------------------------------------------------------------------
# Custom MIB instrumentation controller
# ---------------------------------------------------------------------------

if _PYSNMP_AVAILABLE:
    class _LlmeshMibController(_instrum.AbstractMibInstrumController):  # type: ignore[misc]
        """Read-only MIB controller backed by a live stats dict."""

        def __init__(self, stats_provider: StatsProvider | None = None) -> None:
            self._stats_provider = stats_provider
            self._values: dict[tuple, object] = {
                oid: default for oid, (_, default) in _OID_DEFS.items()
            }

        def refresh(self) -> None:
            if self._stats_provider is None:
                return
            try:
                stats = self._stats_provider()
            except Exception as exc:
                logger.warning("SNMPAdapter: stats_provider error: %s", exc)
                return
            for oid, (key, _) in _OID_DEFS.items():
                if key in stats:
                    self._values[oid] = stats[key]

        def _make_val(self, oid: tuple, raw: object) -> _rfc1902.ObjectSyntax:
            key = _OID_DEFS[oid][0]
            if key in ("nodeId", "did"):
                b = raw if isinstance(raw, (bytes, bytearray)) else str(raw).encode()
                return _rfc1902.OctetString(b)
            if key in ("requestsTotal", "firewallBlocksTotal"):
                return _rfc1902.Counter64(int(raw))
            return _rfc1902.Integer32(int(raw))

        def read_variables(self, *varBinds, **context):
            self.refresh()
            result = []
            for idx, varBind in enumerate(varBinds):
                name, _ = varBind
                oid = tuple(name)
                if oid in self._values:
                    result.append((name, self._make_val(oid, self._values[oid])))
                else:
                    raise _smi_error.NoSuchInstanceError(idx=idx, name=name)
            return tuple(result)

        def read_next_variables(self, *varBinds, **context):
            self.refresh()
            result = []
            for idx, varBind in enumerate(varBinds):
                name, _ = varBind
                current = tuple(name)
                # Find the first OID strictly greater than current
                next_oid: tuple | None = None
                for candidate in _OID_ORDER:
                    if candidate > current:
                        next_oid = candidate
                        break
                if next_oid is None:
                    raise _smi_error.EndOfMibViewError(idx=idx, name=name)
                next_name = _rfc1902.ObjectName(next_oid)
                result.append((next_name, self._make_val(next_oid, self._values[next_oid])))
            return tuple(result)

        def write_variables(self, *varBinds, **context):
            raise _smi_error.NoSuchObjectError(idx=0)

    class _V3OnlyGetResponder(_cmdrsp.GetCommandResponder):  # type: ignore[misc]
        """GET responder that silently drops non-SNMPv3 requests."""

        def process_pdu(self, snmpEngine, messageProcessingModel, *args, **kwargs):
            if messageProcessingModel != 3:
                logger.debug("SNMPAdapter: dropped non-v3 request (model=%d)", messageProcessingModel)
                return
            super().process_pdu(snmpEngine, messageProcessingModel, *args, **kwargs)

    class _V3OnlyNextResponder(_cmdrsp.NextCommandResponder):  # type: ignore[misc]
        """GETNEXT responder that silently drops non-SNMPv3 requests."""

        def process_pdu(self, snmpEngine, messageProcessingModel, *args, **kwargs):
            if messageProcessingModel != 3:
                return
            super().process_pdu(snmpEngine, messageProcessingModel, *args, **kwargs)

    class _V3OnlyBulkResponder(_cmdrsp.BulkCommandResponder):  # type: ignore[misc]
        """GETBULK responder that silently drops non-SNMPv3 requests."""

        def process_pdu(self, snmpEngine, messageProcessingModel, *args, **kwargs):
            if messageProcessingModel != 3:
                return
            super().process_pdu(snmpEngine, messageProcessingModel, *args, **kwargs)


# ---------------------------------------------------------------------------
# SNMPAdapter
# ---------------------------------------------------------------------------

class SNMPAdapter(ProtocolAdapter):
    """Read-only SNMPv3 agent exposing LLMesh node health metrics.

    Args:
        username:       SNMPv3 user name (default "llmesh").
        auth_key:       Authentication passphrase (≥8 chars).
        priv_key:       Privacy passphrase (≥8 chars).
        auth_protocol:  "sha" (default) or "md5".
        priv_protocol:  "aes" (default) or "des".
        stats_provider: Optional callable returning a dict of metric values.
        node_id:        Node identifier placed in the nodeId OID.
    """

    def __init__(
        self,
        username: str = "llmesh",
        auth_key: str = "llmesh-auth-key",
        priv_key: str = "llmesh-priv-key",
        auth_protocol: str = "sha",
        priv_protocol: str = "aes",
        stats_provider: StatsProvider | None = None,
        node_id: str = "",
        **_kwargs: object,
    ) -> None:
        if not _PYSNMP_AVAILABLE:
            raise ImportError(
                "pysnmp is required for SNMPAdapter: pip install llmesh[mgmt]"
            )
        self._username = username
        self._auth_key = auth_key
        self._priv_key = priv_key
        self._auth_protocol = auth_protocol.lower()
        self._priv_protocol = priv_protocol.lower()
        self._stats_provider = stats_provider
        self._node_id = node_id
        self._handler: MessageHandler | None = None
        self._snmp_engine = None
        self._mib_controller: "_LlmeshMibController | None" = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._running = False

    # --- ProtocolAdapter interface ---

    @property
    def protocol_name(self) -> str:
        return "snmp"

    @property
    def is_running(self) -> bool:
        return self._running

    def on_message(self, handler: MessageHandler) -> None:
        self._handler = handler

    async def start(self, host: str, port: int) -> None:
        snmpEngine = _snmp_engine_mod.SnmpEngine()

        # Transport (pysnmp 7.x API)
        _snmp_config.add_transport(
            snmpEngine,
            _udp_transport.DOMAIN_NAME,
            _udp_transport.UdpAsyncioTransport().open_server_mode((host, port)),
        )

        # SNMPv3 user (pysnmp 7.x API)
        auth_proto = (
            _snmp_config.usmHMACSHAAuthProtocol
            if self._auth_protocol == "sha"
            else _snmp_config.usmHMACMD5AuthProtocol
        )
        priv_proto = (
            _snmp_config.usmAesCfb128Protocol
            if self._priv_protocol == "aes"
            else _snmp_config.usmDESPrivProtocol
        )
        _snmp_config.add_v3_user(
            snmpEngine,
            self._username,
            auth_proto,
            self._auth_key,
            priv_proto,
            self._priv_key,
        )
        _snmp_config.add_vacm_user(
            snmpEngine,
            3,                                    # SNMPv3
            self._username,
            "authPriv",
            readSubTree=(1, 3, 6, 1, 4, 1, 99999),  # enterprises.llmesh
            writeSubTree=(),                       # read-only
        )

        # MIB controller with initial nodeId
        def _provider() -> dict:
            base: dict = {}
            if self._stats_provider:
                base = self._stats_provider()
            if self._node_id and "nodeId" not in base:
                base["nodeId"] = self._node_id.encode()
            return base

        self._mib_controller = _LlmeshMibController(_provider)

        # Replace the default context's MIB controller with our custom one
        snmp_ctx = _snmp_context.SnmpContext(snmpEngine)
        snmp_ctx.context_names[b""] = self._mib_controller

        # Read-only command responders (v3-only)
        _V3OnlyGetResponder(snmpEngine, snmp_ctx)
        _V3OnlyNextResponder(snmpEngine, snmp_ctx)
        _V3OnlyBulkResponder(snmpEngine, snmp_ctx)

        self._snmp_engine = snmpEngine
        self._running = True

        # Run the asyncio transport loop in a background thread so that
        # SNMPAdapter.start() is non-blocking.
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever,
            daemon=True,
            name="llmesh-snmp",
        )
        self._thread.start()
        # Hand the asyncio transport to the new loop
        asyncio.get_event_loop()  # touch to avoid ResourceWarning in tests

        logger.info("SNMPAdapter: listening on %s:%d (SNMPv3 only)", host, port)

    async def stop(self) -> None:
        self._running = False
        if self._snmp_engine is not None:
            try:
                _snmp_config.delete_transport(
                    self._snmp_engine, _udp_transport.DOMAIN_NAME
                )
            except Exception:
                pass
            self._snmp_engine = None
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None
        if self._loop is not None:
            self._loop.close()
            self._loop = None
        logger.info("SNMPAdapter: stopped")

    async def send(
        self,
        message: UnifiedMessage,
        target: NodeAddress,
    ) -> UnifiedMessage | None:
        raise TransportError(
            "SNMPAdapter is read-only; use snmpget/snmpwalk to query this agent.",
            protocol="snmp",
            target=str(target),
        )

    async def broadcast(
        self,
        message: UnifiedMessage,
        targets: list[NodeAddress] | None = None,
    ) -> None:
        pass

    # --- Helpers for testing ---

    def update_stats(self, stats: dict) -> None:
        """Directly update the MIB OID values (for testing or manual push)."""
        if self._mib_controller is not None:
            for oid, (key, _) in _OID_DEFS.items():
                if key in stats:
                    self._mib_controller._values[oid] = stats[key]

    @property
    def mib_controller(self) -> "_LlmeshMibController | None":
        return self._mib_controller

    @property
    def oid_base(self) -> tuple:
        return _MIB_BASE
