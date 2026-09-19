"""The development issuer: credentials in, a signed token out, and the key set beside it.

**This is the one diagnostics service whose endpoints do not refuse an unauthenticated
request, and it has to be.** §14's sentence -- *"unauthenticated requests to every
diagnostics endpoint return 401, MCP included"* -- is about the endpoints that answer
questions about the plant; an issuer whose token endpoint required a token would be a door
that can only be opened from inside. The two routes here are the whole of the exception:
`POST /token`, which is where a token comes from, and the key set, which is public by
definition. Nothing else is served, and nothing here reads the plant's history.

**The services still read their key from configuration.** `AUTH_PUBLIC_KEY` is what the
analysis service, the agent, the MCP server and the edge gateway verify against today, and
this task did not change that -- the key set below is served so that a verifier *can* fetch
the key rather than be handed it, and so `auth.config`'s "when Zitadel arrives, this is where
its JWKS URL goes" has something to point at in the meantime. Moving four validators onto a
fetched key set is a change with a startup ordering, a cache and a failure mode of its own;
doing half of it would leave the stack with two answers to "which key is trusted".

**It stands where Zitadel will (§10.5), and no further.** No redirect flow, no refresh token,
no revocation, no self-registration, no password reset, no user store -- §10.5 rules out the
last three for the real issuer too. The login screen says all of this to the person using it,
which is the difference between a demo that is honest and one that implies a security story
it does not have.
"""

from __future__ import annotations

import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated, Final

import jwt
from auth.config import Settings as ClaimSettings
from fastapi import Depends, FastAPI, HTTPException

from issuer.config import Settings, User
from issuer.keys import jwk, signing_key
from issuer.models import Credentials, KeySet, Token

REFUSAL: Final = "these credentials are not valid"
"""One sentence for both halves. An issuer that said "no such user" would answer the
question an attacker asks first, and one that said "wrong password" would answer the
second -- so the response, like `auth.tokens.verify`'s, distinguishes nothing."""

_ABSENT_PASSWORD: Final = secrets.token_urlsafe(32)
"""What an unknown username is compared against, so that it costs the same as a known one.

A per-process random value rather than a constant: no configured password can equal it, and
nothing that reaches a log or a core dump is reusable after this process exits."""


def _settings() -> Settings:
    return Settings()


def _claims() -> ClaimSettings:
    return ClaimSettings()


SettingsDep = Annotated[Settings, Depends(_settings)]
ClaimsDep = Annotated[ClaimSettings, Depends(_claims)]

app = FastAPI(
    title="machine-agent development issuer",
    version="0.1.0",
    # The same subtraction the other two FastAPI services make: the interactive docs are not
    # APIRoutes, there is no entry for this service in `contracts/`, and a served schema
    # nobody generated is a second description of two endpoints.
    openapi_url=None,
    docs_url=None,
    redoc_url=None,
)


@app.post("/token")
def token(credentials: Credentials, settings: SettingsDep, claims: ClaimsDep) -> Token:
    """A token for the named account, or 401 without saying which half was wrong.

    The key is checked before the credentials, and the order is the useful one: an issuer
    with no key refuses every login anyway, so checking credentials first would hide the
    one fact a developer needs behind a message about their password.
    """
    key = signing_key(settings.private_key)
    if key is None:
        raise HTTPException(
            status_code=503,
            detail="this issuer has no signing key configured (ISSUER_PRIVATE_KEY)",
        )

    account = _authenticated(credentials, settings.users)
    lifetime = timedelta(minutes=settings.token_lifetime_minutes)
    issued = datetime.now(UTC)
    public = jwk(key.public_key(), claims.algorithm)

    return Token(
        access_token=jwt.encode(
            {
                "sub": credentials.username,
                "aud": claims.audience,
                "iss": claims.issuer,
                "iat": issued,
                "exp": issued + lifetime,
                claims.role_claim: account.role,
            },
            key,
            algorithm=claims.algorithm,
            # So a verifier holding several keys can pick this one without trying each.
            headers={"kid": public["kid"]},
        ),
        expires_in=int(lifetime.total_seconds()),
    )


@app.get("/.well-known/jwks.json")
def jwks(settings: SettingsDep, claims: ClaimsDep) -> KeySet:
    """The key the tokens above are signed with, at the address a verifier looks for it.

    Empty when no key is configured. An empty set is the honest document -- a verifier gets
    no key rather than a key that signs nothing.
    """
    key = signing_key(settings.private_key)
    if key is None:
        return KeySet(keys=[])
    return KeySet(keys=[jwk(key.public_key(), claims.algorithm)])


def _authenticated(credentials: Credentials, users: dict[str, User]) -> User:
    """The account, or 401.

    The comparison runs even when there is no such account, and against a value no
    configuration can hold. Returning early on an unknown username would make the two
    refusals take measurably different times, which discloses by the clock exactly what the
    response body is careful not to say.

    No `WWW-Authenticate` header, unlike `auth.requests`' 401s: a `Bearer` challenge here
    would tell the client to retry with the token this endpoint exists to hand out.
    """
    account = users.get(credentials.username)
    expected = _ABSENT_PASSWORD if account is None else account.password
    matched = hmac.compare_digest(credentials.password.encode(), expected.encode())
    if account is None or not matched:
        raise HTTPException(status_code=401, detail=REFUSAL)
    return account
