"""A keypair and a minting function, for the tests in this package and nowhere else.

The validating side must never be able to mint: a module that both signs and verifies is an
issuer, and §10.5's whole argument is that this application is a *client* of one. So this
lives in tests/, and `scripts/mint-token.py` -- the development issuer that stands where
Zitadel will -- holds its own three lines rather than importing these.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Final

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

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
