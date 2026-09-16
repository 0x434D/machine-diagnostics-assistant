"""A keypair and a token, for the four test suites that need one.

**Nothing in a running service imports this**, and it is not an issuer: it holds no
configured key, it is reached by no route, and `scripts/mint-token.py` -- the development
issuer standing where Zitadel will -- keeps its own keypair on disk and does not import it
either. What it is, is the twenty lines that `auth`, `analysis`, `agent` and `mcp` would
otherwise each write for themselves; a keypair helper copied into four conftests is four
readings of one claim shape, which is the thing this whole package exists to prevent.

The keypair is generated once per process and lives only in memory.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from typing import Final

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.routing import APIRoute, _IncludedRouter
from starlette.routing import BaseRoute

AUDIENCE: Final = "machine-agent"
ISSUER: Final = "https://issuer.test/machine-agent"

# 2048 rather than 4096: generated once per test session, and the size that matters here is
# the one a real issuer uses by default.
_PRIVATE = rsa.generate_private_key(public_exponent=65537, key_size=2048)

PRIVATE_PEM: Final = _PRIVATE.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
).decode()

PUBLIC_PEM: Final = (
    _PRIVATE.public_key()
    .public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    .decode()
)

OTHER_PRIVATE_PEM: Final = (
    rsa.generate_private_key(public_exponent=65537, key_size=2048)
    .private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    .decode()
)
"""A second keypair, whose public half no service is ever given: a token signed with it is
a well-formed token from an issuer we do not trust, which is a different refusal from a
malformed one."""


def mint(
    *,
    subject: str = "operator-7",
    role: str | None = "user",
    audience: str = AUDIENCE,
    issuer: str = ISSUER,
    lifetime: timedelta = timedelta(minutes=15),
    now: datetime | None = None,
    key: str = PRIVATE_PEM,
) -> str:
    """One token, with every claim the tests need to move named as a parameter."""
    issued = now or datetime.now(UTC)
    claims: dict[str, object] = {
        "sub": subject,
        "aud": audience,
        "iss": issuer,
        "iat": issued,
        "exp": issued + lifetime,
    }
    if role is not None:
        claims["role"] = role
    return jwt.encode(claims, key, algorithm="RS256")


# --- §1.8's enumeration, shared by the two FastAPI services -------------------------------
#
# `analysis` and `agent` both have to prove that every route they serve refuses an
# unauthenticated request, and both have to take the routes off the application rather than
# from a list. That walk is the same walk twice, and two copies of it would agree until the
# day FastAPI changed shape under one of them. It lives here because `auth` is the one
# package both already depend on.
#
# FastAPI is a *dev* dependency of this package and this module is imported by test suites
# only -- the MCP server's image has no FastAPI in it, and nothing a service runs reaches
# this file.


def api_routes(app: FastAPI) -> Iterator[APIRoute]:
    """Every endpoint behind an application's router tree."""
    yield from _walk(app.routes)


def served(app: FastAPI) -> list[tuple[str, str]]:
    """Every (method, path) the application serves, in registration order."""
    return [
        (method, route.path)
        for route in api_routes(app)
        for method in sorted(route.methods or ())
    ]


def concrete(path: str) -> str:
    """`/stops/{identifier}` -> `/stops/x`. The value never reaches a handler."""
    return re.sub(r"\{[^}]+\}", "x", path)


def _walk(routes: Sequence[BaseRoute]) -> Iterator[APIRoute]:
    """`_IncludedRouter` is FastAPI 0.141's way of keeping an included router as one object
    instead of flattening its routes into the parent; `original_router` walks into it.
    Reaching for a private class is deliberate -- when an upgrade changes this shape, the
    walk must fail loudly rather than quietly enumerate fewer routes than are served.

    Anything else raises. A mounted sub-application, a raw Starlette route or the
    interactive docs switched back on is a surface this walk cannot say the guard on, and
    skipping it by pattern is how §1.8's claim would stop being true without failing.
    """
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
        elif isinstance(route, _IncludedRouter):
            yield from _walk(route.original_router.routes)
        else:
            raise TypeError(
                f"{route!r} is served but is not an APIRoute, so this walk cannot say what "
                f"guards it. Handle it deliberately rather than letting it through."
            )
