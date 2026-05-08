"""HTTPAdapter — UnifiedMessage over HTTP using FastAPI (ASGI-compatible).

Server: adds POST /msg to a FastAPI app (new or caller-supplied).
Client: uses stdlib urllib — no additional runtime dependencies.

Swapping the ASGI framework (FastAPI → Starlette / Litestar / etc.) only
requires changing the imports inside this file; the ProtocolAdapter contract
stays identical for all callers.
"""
from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from .adapter import MessageHandler, ProtocolAdapter, TransportError
from .message import UnifiedMessage

if TYPE_CHECKING:
    from .message import NodeAddress

_DEFAULT_TIMEOUT = 30        # seconds
_DEFAULT_MAX_BYTES = 4 * 1024 * 1024  # 4 MiB


class HTTPAdapter(ProtocolAdapter):
    """UnifiedMessage over HTTP.

    Args:
        app:     Existing FastAPI app to attach /msg to.
                 Pass None (default) to create a standalone app.
        timeout: Client-side request timeout in seconds.
    """

    def __init__(
        self,
        app: Any = None,
        timeout: int = _DEFAULT_TIMEOUT,
        max_response_bytes: int = _DEFAULT_MAX_BYTES,
    ) -> None:
        # Lazy import keeps fastapi optional for callers that only use client mode
        from fastapi import FastAPI, Request  # noqa: PLC0415
        from fastapi.responses import JSONResponse  # noqa: PLC0415

        self._timeout = timeout
        self._max_bytes = max_response_bytes
        self._handler: MessageHandler | None = None
        self._server: Any = None
        self._running = False

        if app is None:
            self._app: Any = FastAPI(title="LLMesh Protocol Node")
            self._owns_app = True
        else:
            self._app = app
            self._owns_app = False

        # Register /msg endpoint.
        # `from __future__ import annotations` turns all annotations into
        # strings (PEP 563).  FastAPI resolves them via typing.get_type_hints(),
        # but it can't find "Request" in the closure's module scope.
        # Setting __annotations__ at runtime with the live class bypasses
        # this issue without removing the future import from the whole file.
        adapter_self = self

        async def _msg_endpoint(request):  # annotation injected below
            try:
                body = await request.json()
                msg = UnifiedMessage.from_dict(body)
            except (ValueError, KeyError):
                from fastapi import HTTPException  # noqa: PLC0415
                raise HTTPException(status_code=422, detail="invalid message")
            if adapter_self._handler is not None:
                resp = await adapter_self._handler(msg)
                if resp is not None:
                    return JSONResponse(content=resp.to_dict())
            return JSONResponse(content={})

        _msg_endpoint.__annotations__["request"] = Request
        _msg_endpoint.__annotations__["return"] = JSONResponse
        self._app.post("/msg")(_msg_endpoint)

    # ------------------------------------------------------------------
    # ProtocolAdapter interface
    # ------------------------------------------------------------------

    @property
    def protocol_name(self) -> str:
        return "http"

    @property
    def is_running(self) -> bool:
        return self._running

    def on_message(self, handler: MessageHandler) -> None:
        self._handler = handler

    async def start(self, host: str, port: int) -> None:
        """Start uvicorn in the background (non-blocking)."""
        import uvicorn  # noqa: PLC0415

        config = uvicorn.Config(
            self._app,
            host=host,
            port=port,
            log_level="error",
            access_log=False,
        )
        self._server = uvicorn.Server(config)
        self._running = True
        asyncio.create_task(self._server.serve())
        # Give the server a moment to bind
        await asyncio.sleep(0.05)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
            await asyncio.sleep(0.05)
        self._running = False

    async def send(
        self,
        message: UnifiedMessage,
        target: "NodeAddress",
    ) -> UnifiedMessage | None:
        """POST *message* to http://{target}/msg and return the parsed response."""
        url = f"http://{target.host}:{target.port}/msg"
        data = message.to_bytes()
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                raw = resp.read(self._max_bytes + 1)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:200]
            raise TransportError(
                f"http:{exc.code}:{detail}",
                protocol="http",
                target=str(target),
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise TransportError(
                str(exc),
                protocol="http",
                target=str(target),
            ) from exc

        if len(raw) > self._max_bytes:
            raise TransportError(
                f"response_too_large:{len(raw)}",
                protocol="http",
                target=str(target),
            )
        if not raw:
            return None
        return UnifiedMessage.from_bytes(raw)

    async def broadcast(
        self,
        message: UnifiedMessage,
        targets: "list[NodeAddress] | None" = None,
    ) -> None:
        """Send *message* to each target; errors are logged, not raised."""
        if not targets:
            return
        for target in targets:
            try:
                await self.send(message, target)
            except TransportError:
                pass

    # ------------------------------------------------------------------
    # Internal FastAPI endpoint
    # ------------------------------------------------------------------

    async def _handle_http_request(self, request: Any) -> Any:
        body = await request.json()
        msg = UnifiedMessage.from_dict(body)
        if self._handler is not None:
            response = await self._handler(msg)
            if response is not None:
                return self._JSONResponse(content=response.to_dict())
        return self._JSONResponse(content={})
