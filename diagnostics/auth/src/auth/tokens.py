"""§10.5's *"one shared module of roughly thirty lines"*, and the smallness is the point.

A token is validated against the configured public key, its audience and issuer are checked,
and its role claim is read. What comes back is a subject and a role and nothing else: there
is no permission matrix, no group, no per-resource rule and no user table for any of it to
live in (§10.5), so anything larger than this would be a model of something the system does
not have.

**Every refusal returns `None`.** The caller answers 401 and says nothing about which check
failed -- a response that distinguished "expired" from "wrong audience" would tell whoever
is holding the token which half to fix. The reason goes to the log instead, where an
operator reads it, because seven refusals that are indistinguishable from the inside are
seven hours of somebody's afternoon.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Final

import jwt

from auth.config import Settings

LOG = logging.getLogger(__name__)

ADMIN: Final = "admin"
USER: Final = "user"

ROLES: Final = frozenset({ADMIN, USER})
"""§10.5's two, closed. A third value is not a third role; it is a claim with no rule."""


@dataclass(frozen=True)
class Principal:
    """Who is asking, as far as this system is allowed to care."""

    subject: str
    """The OIDC `sub`. It is what `agent.sessions.subject` records (§5.2)."""

    role: str
    """One of `ROLES`."""


def verify(token: str | None, settings: Settings) -> Principal | None:
    """The token, or `None` and a logged reason. Never raises on a bad token."""
    if not token:
        return _refused("no token presented")
    if not settings.public_key:
        return _refused("no public key is configured, so no token can be trusted")

    try:
        claims = jwt.decode(
            token,
            settings.public_key,
            algorithms=[settings.algorithm],
            audience=settings.audience,
            issuer=settings.issuer,
            leeway=settings.clock_skew_seconds,
            # Absent is not the same as wrong, and without this it would be: PyJWT checks a
            # claim it finds and ignores one it does not, so a token with no `exp` would
            # verify and never expire.
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except jwt.InvalidTokenError as error:
        # A branch on a known condition, not a swallowed error (CLAUDE.md): every subclass
        # of this means one specific thing about the token and all of them mean 401. The
        # type name is logged because it is the only place the seven refusals stay apart.
        return _refused(f"{type(error).__name__}: {error}")

    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        return _refused("the `sub` claim is not a non-empty string")

    role = claims.get(settings.role_claim)
    if role is None:
        return _refused(f"no role claim {settings.role_claim!r}")
    if not isinstance(role, str) or role not in ROLES:
        return _refused(f"role {role!r} is not one of {sorted(ROLES)}")

    return Principal(subject=subject, role=role)


def _refused(reason: str) -> Principal | None:
    LOG.warning("token refused: %s", reason)
    return None
