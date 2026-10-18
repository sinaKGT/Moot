"""Keep the local server local.

The server has no login and uses your API keys, so it only answers requests meant for
this machine (blocking DNS rebinding) and refuses requests that web pages on other sites
make from your browser (cross-site requests).
"""

from __future__ import annotations

from collections.abc import Sequence
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


def host_name(host_header: str) -> str:
    """'127.0.0.1:8765' -> '127.0.0.1', '[::1]:8765' -> '::1'."""
    return urlsplit(f"//{host_header}").hostname or ""


class LocalOnly:
    """ASGI middleware. `hosts=None` accepts any host (when serving on the network).
    `origins` adds web addresses allowed to call the API, e.g. a UI dev server."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        hosts: Sequence[str] | None = LOCAL_HOSTS,
        origins: Sequence[str] = (),
    ) -> None:
        self.app = app
        self.hosts = {h.lower() for h in hosts} if hosts is not None else None
        self.origins = {o.rstrip("/").lower() for o in origins}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        host = headers.get("host", "")
        if self.hosts is not None and host_name(host).lower() not in self.hosts:
            await PlainTextResponse("Invalid host", status_code=400)(scope, receive, send)
            return
        origin = headers.get("origin")
        if origin is not None and not self._origin_allowed(origin, host):
            await PlainTextResponse("Cross-site request refused", status_code=403)(
                scope, receive, send
            )
            return
        await self.app(scope, receive, send)

    def _origin_allowed(self, origin: str, host: str) -> bool:
        origin = origin.rstrip("/").lower()
        if origin in self.origins:
            return True
        parts = urlsplit(origin)
        return parts.scheme in ("http", "https") and parts.netloc == host.lower()
