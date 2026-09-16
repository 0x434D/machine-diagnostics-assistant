"""The two ways a service refuses: a dependency for the FastAPI ones, a middleware for the
Starlette one.

Both sit on `tokens.verify` and neither repeats a rule. What differs is only where they hang
-- the analysis service and the agent declare an application-wide dependency, so an endpoint
added tomorrow is closed by the same line that closed the ones added today; the MCP server
has no dependency injection and no routes of its own to decorate, so the door is the only
place to stand.

Nothing here imports FastAPI. `Depends(principal)` works on a plain callable that asks for a
Starlette `Request`, FastAPI's `Request` *is* Starlette's, and FastAPI answers a Starlette
`HTTPException` with the same handler it uses for its own -- so the MCP server's image does
not gain a web framework it does not run.
"""

from __future__ import annotations

from typing import Final

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from auth.config import Settings
from auth.tokens import ADMIN, Principal, verify

_BEARER: Final = "bearer"

_CHALLENGE: Final = {"WWW-Authenticate": "Bearer"}
"""RFC 6750. A 401 that does not say how to authenticate is a dead end for any client."""


def presented(request: Request) -> str | None:
    """The bearer token on the request, or `None` if it carries nothing that is one."""
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != _BEARER:
        return None
    return token.strip() or None


def principal(request: Request) -> Principal:
    """Who is asking. Raises 401 when the answer is nobody this deployment accepts.

    The settings are read per request rather than held, the way every other service in this
    repository reads its own: it is how an operator moves a number without a rebuild, and it
    is what lets a test configure the service the way the deployment does.
    """
    who = verify(presented(request), Settings())
    if who is None:
        # Deliberately uniform, and deliberately saying nothing: `verify` has already logged
        # which of the seven checks refused, and a response that repeated it would tell
        # whoever holds the token which half to fix.
        raise HTTPException(
            status_code=401, detail="authentication required", headers=_CHALLENGE
        )
    return who


def admin(request: Request) -> Principal:
    """§10.5's four admin rows. A `user` here is 403, which is not a 401 (§14)."""
    who = principal(request)
    if who.role != ADMIN:
        raise HTTPException(
            status_code=403,
            detail=f"this action requires the {ADMIN} role; this token carries "
            f"{who.role!r}",
        )
    return who


class RequireToken:
    """Refuse an unauthenticated request before it reaches the application behind it.

    §6.11's server is the MCP SDK's own ASGI application: its routes are the protocol's, not
    ours, and there is nothing to hang a dependency on. Standing at the door also answers
    for the paths it does *not* serve -- a 404 given without a token is still an answer about
    which paths exist.
    """

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            # Lifespan (and a websocket, which this server has none of). Neither carries a
            # request to refuse, and swallowing the lifespan messages would hang startup.
            await self._app(scope, receive, send)
            return

        if verify(presented(Request(scope)), Settings()) is None:
            refusal = JSONResponse(
                {"detail": "authentication required"},
                status_code=401,
                headers=_CHALLENGE,
            )
            await refusal(scope, receive, send)
            return

        await self._app(scope, receive, send)
