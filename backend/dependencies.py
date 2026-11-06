"""The gates: a token for each kind of caller, and the Host and Origin checks."""

import asyncio
import time
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from backend import auth, build_mode

TAURI_ORIGIN = "tauri://localhost"
BEARER = "Bearer "


def token_in(authorization: str | None) -> str | None:
    if authorization and authorization.startswith(BEARER):
        return authorization.removeprefix(BEARER)
    return None


def check_app_token(request: Request) -> None:
    given = token_in(request.headers.get("authorization"))
    if given is None:
        raise HTTPException(401, "This request needs a token.")
    tokens: auth.Tokens = request.app.state.tokens
    if not auth.same(given, tokens.app):
        raise HTTPException(403, "This token doesn't open the app's routes.")


AppToken = Annotated[None, Depends(check_app_token)]


def allowed_origins() -> set[str]:
    return {TAURI_ORIGIN} | build_mode.dev_origins()


WAIT_POLL_SECONDS = 0.05
RESTORING = "SecureFolio is restoring a backup. It will be back shortly."


def allowed_hosts(port: int) -> set[str]:
    return {f"127.0.0.1:{port}", f"localhost:{port}"}


def _is_a_stream(scope: Scope) -> bool:
    path = scope["path"]
    return scope["method"] == "GET" and (
        path.endswith("/events") or path.startswith("/mcp")
    )


class MaintenanceGate:
    """While a restore replaces the library, no request gets in, and it first waits for
    those inside, some of which write. A stream of events never ends, and only reads."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.inside = 0
        self.closed = False

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if self.closed:
            answer = JSONResponse({"detail": RESTORING}, status_code=503)
            await answer(scope, receive, send)
            return
        scope["app"].state.gate = self
        finite = not _is_a_stream(scope)
        self.inside += finite
        try:
            await self.app(scope, receive, send)
        finally:
            self.inside -= finite

    async def close(self, wait_seconds: float) -> bool:
        """False if another restore is running, or a request won't finish. The request
        asking for this is itself one of those inside."""
        if self.closed:
            return False
        self.closed = True
        deadline = time.monotonic() + wait_seconds
        while self.inside > 1:
            if time.monotonic() > deadline:
                self.closed = False
                return False
            await asyncio.sleep(WAIT_POLL_SECONDS)
        return True

    def open(self) -> None:
        self.closed = False


class LoopbackGate:
    """Refuses a request with the wrong Host or Origin, before any route, and
    the MCP routes without their own token."""

    def __init__(self, app: ASGIApp, mcp_prefix: str) -> None:
        self.app = app
        self.mcp_prefix = mcp_prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        refusal = self.refusal(scope)
        if refusal is None:
            await self.app(scope, receive, send)
            return
        status, detail = refusal
        await JSONResponse({"detail": detail}, status_code=status)(scope, receive, send)

    def refusal(self, scope: Scope) -> tuple[int, str] | None:
        headers = {k.decode(): v.decode() for k, v in scope["headers"]}
        state = scope["app"].state
        if headers.get("host") not in allowed_hosts(state.port):
            return 403, "This server answers only on its loopback address."
        origin = headers.get("origin")
        if origin is not None and origin not in allowed_origins():
            return 403, "Requests from this origin aren't accepted."
        if scope["path"].startswith(self.mcp_prefix):
            return mcp_refusal(headers, state.tokens)
        return None


def mcp_refusal(headers: dict[str, str], tokens: auth.Tokens) -> tuple[int, str] | None:
    given = token_in(headers.get("authorization"))
    if given is None:
        return 401, "This request needs a token."
    if not auth.same(given, tokens.mcp):
        return 403, "This token doesn't open the MCP routes."
    return None
