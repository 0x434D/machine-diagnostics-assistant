"""The development issuer: one keypair on disk and a token minted from it.

**It stands where Zitadel will.** §10.5's identity story is one OIDC issuer that the
application is a client of; M5 was ruled slim and does not build that issuer, so this script
occupies its place -- it is not a service, it is not in `diagnostics/compose.yml`, and it
must not become a fourth container. What the milestone proves is the *validation* side:
`diagnostics/auth` checks a signature, an audience and an issuer, and it does not care
whether the thing that signed was a script or a broker.

What this deliberately does not do is what an issuer does: no login, no session, no refresh,
no user store, no revocation, no JWKS endpoint. A token it mints is good until it expires.

    # once -- writes .dev-issuer/, which is gitignored
    cd diagnostics && uv run --frozen python ../scripts/mint-token.py --public-key

    # then, per token
    cd diagnostics && uv run --frozen python ../scripts/mint-token.py --role admin

Give the services the public key it prints as `AUTH_PUBLIC_KEY` and send the token as
`Authorization: Bearer <token>`.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
from datetime import UTC, datetime, timedelta

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

# The same reach `scripts/generate-contract.py` makes for the services it reads: this
# directory is outside both uv workspaces, so the package has to be put on the path
# before it can be imported.
sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parents[1] / "diagnostics/auth/src")
)

from auth.config import Settings
from auth.tokens import ROLES

KEYS = pathlib.Path(__file__).resolve().parents[1] / ".dev-issuer"
"""Gitignored, and `*.pem` in .gitignore covers it twice. A committed private key would be
a credential in the repository that mints admin tokens for every checkout of it."""

PRIVATE = KEYS / "private.pem"
PUBLIC = KEYS / "public.pem"


def keypair() -> tuple[str, str]:
    """The issuer's keypair, generated on first use and reused after.

    Regenerating per run would be tidier and wrong: the public half has to be configured
    into three services, and a key that changed underneath them would make every token
    refused for a reason nobody would look for.
    """
    if PRIVATE.exists() and PUBLIC.exists():
        return PRIVATE.read_text(), PUBLIC.read_text()

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public = (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )

    KEYS.mkdir(exist_ok=True)
    PRIVATE.write_text(private)
    PRIVATE.chmod(0o600)
    PUBLIC.write_text(public)
    return private, public


def main() -> None:
    # The defaults come from the validating side's own settings rather than being written
    # again here: a token minted for an audience the services do not check is a token that
    # fails for a reason the two files would have to be read together to see.
    settings = Settings()

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subject", default="operator-7", help="the OIDC `sub`")
    parser.add_argument("--role", default="user", choices=sorted(ROLES))
    parser.add_argument("--audience", default=settings.audience)
    parser.add_argument("--issuer", default=settings.issuer)
    parser.add_argument("--minutes", type=int, default=60, help="token lifetime")
    parser.add_argument(
        "--public-key",
        action="store_true",
        help="print the public key to configure as AUTH_PUBLIC_KEY, and exit",
    )
    arguments = parser.parse_args()

    private, public = keypair()
    if arguments.public_key:
        print(public, end="")
        return

    issued = datetime.now(UTC)
    print(
        jwt.encode(
            {
                "sub": arguments.subject,
                "aud": arguments.audience,
                "iss": arguments.issuer,
                "iat": issued,
                "exp": issued + timedelta(minutes=arguments.minutes),
                settings.role_claim: arguments.role,
            },
            private,
            algorithm=settings.algorithm,
        )
    )


if __name__ == "__main__":
    main()
