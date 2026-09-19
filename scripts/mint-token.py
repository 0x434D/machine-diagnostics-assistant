"""The development issuer's keypair on disk, and tokens minted from it on the command line.

**This file's own docstring used to say the opposite, and the reversal is deliberate.** Until
M6 it read: *"it is not a service, it is not in `diagnostics/compose.yml`, and it must not
become a fourth container."* That was M5's constraint and it was right for M5 -- the
milestone was ruled slim and what it set out to prove was the *validation* side, which
`diagnostics/auth` proves without caring whether a script or a broker signed.

The human ruling of 2026-09-19 closes that, because M6's first line is a login and a
paste-a-token field is not one. **The development issuer is now a service**:
`diagnostics/issuer`, joined to `diag-net`, serving a token endpoint and a JWKS document, and
it is what the UI's login screen posts credentials to. It still stands where Zitadel will
(§10.5) and Zitadel still replaces it by configuration.

What is left here is the half a service cannot do from inside a container: **it owns the
keypair.** `.dev-issuer/` is gitignored, generated on first use, and is where both halves
come from -- the public one configured into the four validators as `AUTH_PUBLIC_KEY`, the
private one into the issuer as `ISSUER_PRIVATE_KEY`. It also stays the fastest way to mint a
token for a `curl`, which is what every demo target in the Makefile does with it.

    # once -- writes .dev-issuer/, which is gitignored
    cd diagnostics && uv run --frozen python ../scripts/mint-token.py --public-key
    cd diagnostics && uv run --frozen python ../scripts/mint-token.py --private-key

    # then, per token
    cd diagnostics && uv run --frozen python ../scripts/mint-token.py --role admin

Send the token as `Authorization: Bearer <token>`. What this still does not do is what the
service does not do either: no session, no refresh, no revocation. A token is good until it
expires.
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
    parser.add_argument(
        "--private-key",
        action="store_true",
        help="print the signing key to configure as ISSUER_PRIVATE_KEY, and exit",
    )
    arguments = parser.parse_args()

    private, public = keypair()
    if arguments.public_key:
        print(public, end="")
        return
    # Printing a private key is what it looks like, and it is deliberate: this is a
    # development keypair that this script generated, `.dev-issuer/` is gitignored twice
    # over, and the alternative -- bind-mounting the directory into the issuer container --
    # would put a host path in `diagnostics/compose.yml` and fail `docker compose up` before
    # the issuer exists to say what is missing. When there is a real key to protect there is
    # a real issuer holding it, and this flag goes with the service it feeds.
    if arguments.private_key:
        print(private, end="")
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
