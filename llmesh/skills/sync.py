"""Skill chunk Pull / Push / Gossip protocol (RFC Phase 3.4).

Client-side counterpart to ``llmesh.skills.router`` (Phase 3.3): pulls
chunks from a remote peer's ``/skills/*`` HTTP endpoints into a local
``SkillReplica``, posts ``/skills/notify`` for push, and runs a periodic
gossip loop that diffs ``/skills/index`` against the local replica.

Stdlib only (``urllib`` + ``threading``) to mirror
``llmesh.discovery.gossip.GossipClient``. ``HTTPTransport`` is a Protocol
so tests can swap ``urllib`` for an in-process FastAPI ``TestClient``
adapter.

Approval gating (Phase 3.5) wraps the high-level ``sync_with`` call from
the outside — this client deliberately stays policy-agnostic so it can be
reused under different governance regimes.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from llmesh.skills.chunk import SkillChunk
from llmesh.skills.replica import SkillReplica

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 10
_DEFAULT_INTERVAL = 30
_DEFAULT_MAX_PULLS = 8


class SkillSyncError(Exception):
    """Raised on transport failure or malformed remote response."""


class HTTPTransport(Protocol):
    """Minimal JSON-over-HTTP shape needed by SkillSyncClient."""

    def get_json(self, url: str) -> Any: ...
    def post_json(self, url: str, body: dict[str, Any]) -> Any: ...


class UrllibTransport:
    """Default transport: stdlib ``urllib`` only, no extra deps."""

    def __init__(self, timeout: int = _DEFAULT_TIMEOUT) -> None:
        self._timeout = timeout

    def get_json(self, url: str) -> Any:
        req = urllib.request.Request(
            url, method="GET", headers={"Accept": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # noqa: S310
                payload = resp.read()
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
            raise SkillSyncError(f"GET {url}: {exc}") from exc
        try:
            return json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SkillSyncError(f"GET {url}: malformed JSON ({exc})") from exc

    def post_json(self, url: str, body: dict[str, Any]) -> Any:
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # noqa: S310
                payload = resp.read()
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
            raise SkillSyncError(f"POST {url}: {exc}") from exc
        try:
            return json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SkillSyncError(f"POST {url}: malformed JSON ({exc})") from exc


PolicyDecision = Literal["approved", "denied"]
PullPolicyCheck = Callable[[str, str], PolicyDecision]
"""Approval gate signature: ``(peer_url, skill_id) -> "approved" | "denied"``.

Designed to compose with llive's ``@govern`` / ``ApprovalBus``: the caller
wraps an ApprovalBus request inside this callable, returning ``"approved"``
when the bus permits the pull. Kept as a plain Callable (not a Protocol)
so llmesh stays free of any llive dependency."""


LicenseFilter = Callable[["SkillChunk"], bool]
"""License gate signature: ``(chunk) -> True (accept) | False (reject)``.

Runs **after** the chunk is pulled but **before** it is persisted to the
replica. Lets the caller reject chunks whose ``license`` field is not in
an approved set (e.g. proprietary, unknown). See ``allow_licenses`` for
the common case of a static allow-list."""


DEFAULT_ALLOWED_LICENSES: frozenset[str] = frozenset(
    {"Apache-2.0", "MIT", "BSD-3-Clause", "BSD-2-Clause", "CC0-1.0", "CC-BY-4.0"}
)
"""Default permissive licenses recommended by RFC Phase 3 §License filter."""


def allow_licenses(allowed: Iterable[str] = DEFAULT_ALLOWED_LICENSES) -> LicenseFilter:
    """Build a ``LicenseFilter`` that accepts only the given license identifiers.

    Empty / missing licenses are rejected. Comparison is case-sensitive
    (SPDX identifiers are canonical).
    """
    allowed_set = frozenset(allowed)

    def _filter(chunk: SkillChunk) -> bool:
        return bool(chunk.license) and chunk.license in allowed_set

    return _filter


@dataclass(frozen=True)
class SyncResult:
    """Outcome of one ``SkillSyncClient.sync_with`` round."""

    peer_url: str
    pulled: tuple[str, ...] = ()
    skipped_existing: tuple[str, ...] = ()
    denied: tuple[str, ...] = ()
    denied_license: tuple[str, ...] = ()
    failed: tuple[tuple[str, str], ...] = ()  # (skill_id, reason)
    duration_s: float = 0.0


class SkillSyncClient:
    """Pull / Push / Gossip operations against a peer's /skills HTTP API.

    Note: chunks pulled here are stored as-is. Callers that require
    signature verification should fetch the chunk and call
    ``chunk.verify(public_key)`` before trusting it for execution; the
    replica path is intentionally lazy to keep the gossip loop fast.

    ``policy`` (Phase 3.5 approval gate): callable invoked before each
    pull during ``sync_with``. Returning ``"denied"`` skips the pull and
    records the skill_id in ``SyncResult.denied``. ``None`` (default) means
    no gate. Exceptions raised by the policy callable are treated as denials
    so a buggy gate never weakens the trust boundary.
    """

    def __init__(
        self,
        transport: HTTPTransport | None = None,
        *,
        policy: PullPolicyCheck | None = None,
    ) -> None:
        self._http: HTTPTransport = transport or UrllibTransport()
        self._policy = policy

    def _is_approved(self, peer_url: str, skill_id: str) -> bool:
        if self._policy is None:
            return True
        try:
            return self._policy(peer_url, skill_id) == "approved"
        except Exception as exc:
            logger.warning(
                "policy check raised for %s @ %s; treating as denied: %s",
                skill_id,
                peer_url,
                exc,
            )
            return False

    # ------------------------------------------------------------------
    # Low-level operations
    # ------------------------------------------------------------------

    def pull_chunk(self, peer_url: str, skill_id: str) -> SkillChunk | None:
        """GET ``/skills/<skill_id>`` from peer. ``None`` if absent or unreachable."""
        url = f"{peer_url.rstrip('/')}/skills/{skill_id}"
        try:
            data = self._http.get_json(url)
        except SkillSyncError as exc:
            logger.warning("pull_chunk failed: %s", exc)
            return None
        if not isinstance(data, dict):
            return None
        try:
            return SkillChunk.from_json(data)
        except Exception as exc:  # malformed chunk is the peer's fault
            raise SkillSyncError(f"malformed chunk {skill_id} from {peer_url}: {exc}") from exc

    def pull_index(self, peer_url: str) -> list[dict[str, Any]]:
        """GET ``/skills/index`` from peer. Raises on transport / shape failure."""
        url = f"{peer_url.rstrip('/')}/skills/index"
        data = self._http.get_json(url)
        if not isinstance(data, dict) or not isinstance(data.get("chunks"), list):
            raise SkillSyncError(f"malformed index payload from {peer_url}")
        return [row for row in data["chunks"] if isinstance(row, dict)]

    def notify(
        self,
        peer_url: str,
        skill_id: str,
        *,
        version: str | None = None,
        merkle_root: str | None = None,
        peer_endpoint: str | None = None,
        license: str | None = None,  # noqa: A002 - domain term
    ) -> dict[str, Any]:
        """POST ``/skills/notify`` to advertise a new chunk to a peer."""
        url = f"{peer_url.rstrip('/')}/skills/notify"
        body: dict[str, Any] = {"skill_id": skill_id}
        if version is not None:
            body["version"] = version
        if merkle_root is not None:
            body["merkle_root"] = merkle_root
        if peer_endpoint is not None:
            body["peer_endpoint"] = peer_endpoint
        if license is not None:
            body["license"] = license
        result = self._http.post_json(url, body)
        if not isinstance(result, dict):
            raise SkillSyncError(f"notify {url}: unexpected response shape")
        return result

    # ------------------------------------------------------------------
    # High-level operations
    # ------------------------------------------------------------------

    def sync_with(
        self,
        peer_url: str,
        replica: SkillReplica,
        *,
        max_pulls: int | None = _DEFAULT_MAX_PULLS,
    ) -> SyncResult:
        """One round of pull-based sync: index diff → pull missing chunks.

        A chunk is "missing" if absent locally, or if its ``content_sha``
        differs from the remote entry. ``max_pulls`` caps work per round
        so a single chatty peer cannot starve the gossip loop.
        """
        t0 = time.monotonic()
        try:
            remote = self.pull_index(peer_url)
        except SkillSyncError as exc:
            return SyncResult(
                peer_url=peer_url,
                failed=(("<index>", str(exc)),),
                duration_s=time.monotonic() - t0,
            )

        local_index = {
            row["skill_id"]: row.get("content_sha") for row in replica.index()
        }
        pulled: list[str] = []
        skipped: list[str] = []
        denied: list[str] = []
        failed: list[tuple[str, str]] = []

        for row in remote:
            sid = row.get("skill_id")
            if not isinstance(sid, str):
                continue
            remote_sha = row.get("content_sha")
            if sid in local_index and local_index[sid] == remote_sha:
                skipped.append(sid)
                continue
            if max_pulls is not None and len(pulled) >= max_pulls:
                break
            if not self._is_approved(peer_url, sid):
                denied.append(sid)
                continue
            try:
                chunk = self.pull_chunk(peer_url, sid)
            except SkillSyncError as exc:
                failed.append((sid, str(exc)))
                continue
            if chunk is None:
                failed.append((sid, "remote_missing_or_unreachable"))
                continue
            replica.put(chunk)
            pulled.append(sid)

        return SyncResult(
            peer_url=peer_url,
            pulled=tuple(pulled),
            skipped_existing=tuple(skipped),
            denied=tuple(denied),
            failed=tuple(failed),
            duration_s=time.monotonic() - t0,
        )


PeerProvider = Callable[[], Iterable[str]]


class GossipScheduler:
    """Background thread that periodically runs ``sync_with`` over known peers.

    Peers are obtained at every tick from a callable, allowing integration
    with ``llmesh.discovery`` (NodeRegistry / TrustedPeers) or any other
    source without coupling to a specific module.
    """

    def __init__(
        self,
        client: SkillSyncClient,
        replica: SkillReplica,
        peer_provider: PeerProvider,
        *,
        interval_s: int = _DEFAULT_INTERVAL,
        max_pulls_per_peer: int | None = _DEFAULT_MAX_PULLS,
    ) -> None:
        self._client = client
        self._replica = replica
        self._provider = peer_provider
        self._interval = interval_s
        self._max_pulls = max_pulls_per_peer
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_results: dict[str, SyncResult] = {}
        self._lock = threading.RLock()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="llmesh-skill-gossip", daemon=True
        )
        self._thread.start()
        logger.info(
            "GossipScheduler started (interval=%ds, max_pulls=%s)",
            self._interval,
            self._max_pulls,
        )

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def tick(self) -> dict[str, SyncResult]:
        """Run a single sync round across all peers; safe to call from tests."""
        results: dict[str, SyncResult] = {}
        try:
            peers = list(self._provider())
        except Exception as exc:  # provider may be flaky
            logger.warning("peer_provider failed: %s", exc)
            return results
        for peer in peers:
            results[peer] = self._client.sync_with(
                peer, self._replica, max_pulls=self._max_pulls
            )
        with self._lock:
            self._last_results = results
        return results

    def last_results(self) -> dict[str, SyncResult]:
        with self._lock:
            return dict(self._last_results)

    def _loop(self) -> None:
        while True:
            try:
                self.tick()
            except Exception as exc:  # never let the daemon thread die
                logger.exception("skill-gossip tick failed: %s", exc)
            if self._stop.wait(timeout=self._interval):
                return


__all__ = [
    "GossipScheduler",
    "HTTPTransport",
    "PeerProvider",
    "PolicyDecision",
    "PullPolicyCheck",
    "SkillSyncClient",
    "SkillSyncError",
    "SyncResult",
    "UrllibTransport",
]
