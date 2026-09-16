"""The token fixtures both implementations of §10.5's rule are tested against.

M5 ends with the rule written twice -- `diagnostics/auth/tokens.py` in Python, and
`Gateway.Auth.TokenGuard` in C#, because the edge gateway cannot import a Python module.
That is the cost of §2.3's language split, and the thing that keeps it honest is that the
two are checked against *the same bytes*: one file of tokens with the verdict each must
reach, read by `diagnostics/auth/tests/test_fixture_parity.py` and by
`Gateway.Tests/TokenFixtureParityTests.cs`. A pair of implementations tested against a pair
of fixture sets agrees until the day one of the sets is edited.

The output carries the settings as well as the tokens, so a divergence in the *audience* or
the *clock skew default* fails as loudly as a divergence in a verdict.

    cd diagnostics && uv run --frozen python ../scripts/mint-fixtures.py

Rerun it only deliberately: it mints from a keypair generated in this process and never
written to disk, so every run replaces the public key and all twelve tokens. Nothing about
the file is secret -- a public key and tokens signed for an issuer that does not exist --
and there is no private key to leak because there is no private key after this exits.
"""

from __future__ import annotations

import json
import pathlib
import sys
from datetime import UTC, datetime

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

# The same reach `scripts/mint-token.py` makes: this directory is outside both uv
# workspaces, so the package has to be put on the path before it can be imported.
sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parents[1] / "diagnostics/auth/src")
)

from auth.config import Settings

OUTPUT = (
    pathlib.Path(__file__).resolve().parents[1]
    / "diagnostics/auth/fixtures/tokens.json"
)

# Fixed instants rather than `now`, so a rerun changes the signatures and nothing else.
ISSUED = datetime(2026, 1, 1, tzinfo=UTC)

# A century, and it is not a token anyone can use: it is signed for an issuer that exists
# nowhere and by a key that stopped existing when this script exited. What the distance buys
# is a verdict that does not depend on the day the suite runs -- an `accepted` fixture with a
# realistic lifetime turns into a `refused` one at some point, and the failure would read as
# a drift between the two implementations rather than as a stale fixture.
ACCEPTED_UNTIL = datetime(2126, 1, 1, tzinfo=UTC)
EXPIRED_AT = datetime(2026, 1, 1, 0, 15, tzinfo=UTC)

type Claims = dict[str, object]


def _pem(key: rsa.RSAPrivateKey) -> str:
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


def main() -> None:
    settings = Settings()

    issuer_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    # A well-formed token from an issuer nobody trusts, which is a different refusal from a
    # malformed one and the one a service that checks only the claims would let through.
    stranger_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    public = (
        issuer_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )

    def sign(claims: Claims, key: rsa.RSAPrivateKey | None = None) -> str:
        if key is None:
            return jwt.encode(claims, _pem(issuer_key), algorithm=settings.algorithm)
        return jwt.encode(claims, _pem(key), algorithm=settings.algorithm)

    def claims(**overrides: object) -> Claims:
        base: Claims = {
            "sub": "operator-7",
            "aud": settings.audience,
            "iss": settings.issuer,
            "iat": ISSUED,
            "exp": ACCEPTED_UNTIL,
            settings.role_claim: "user",
        }
        base.update(overrides)
        return {name: value for name, value in base.items() if value is not None}

    fixtures = [
        {
            "name": "valid_user",
            "accepted": True,
            "subject": "operator-7",
            "role": "user",
            "why": "§10.5's ordinary caller: everything §14 asks for, and the role it reads.",
            "token": sign(claims()),
        },
        {
            "name": "valid_admin",
            "accepted": True,
            "subject": "shift-lead-2",
            "role": "admin",
            "why": "The other of the two roles. Accepted here; what it additionally opens "
            "is the services' business, not the validator's.",
            "token": sign(claims(sub="shift-lead-2", role="admin")),
        },
        {
            "name": "expired",
            "accepted": False,
            "why": "Signature, audience and issuer all good. Only the clock refuses it, "
            "and at the default skew of zero it is refused the second it expires.",
            # The one fixture whose `exp` is stated as well as signed: the C# suite stands a
            # stopped clock either side of it to test the skew, and reading the instant off
            # the token would mean decoding a JWT in a test whose subject is a JWT decoder.
            "expires_at": EXPIRED_AT.isoformat(),
            "token": sign(claims(exp=EXPIRED_AT)),
        },
        {
            "name": "wrong_audience",
            "accepted": False,
            "why": "§10.5 names the audience check. A token minted for another service in "
            "the same estate must not open this one.",
            "token": sign(claims(aud="some-other-service")),
        },
        {
            "name": "wrong_issuer",
            "accepted": False,
            "why": "The application is a client of exactly one issuer (§10.5).",
            "token": sign(claims(iss="https://issuer.test/somewhere-else")),
        },
        {
            "name": "untrusted_key",
            "accepted": False,
            "why": "Every claim is right and the signature verifies -- against a key this "
            "deployment was never given. The one an implementation that read the "
            "claims before checking the signature would accept.",
            "token": sign(claims(), key=stranger_key),
        },
        {
            "name": "no_role_claim",
            "accepted": False,
            "why": "Authenticated and unauthorisable. Defaulting it to `user` would make "
            "§10.5's two roles three, the third being 'whatever the issuer forgot'.",
            "token": sign(claims(role=None)),
        },
        {
            "name": "role_outside_the_two",
            "accepted": False,
            "why": "`ROLES` is closed (§10.5). A third value is not a third role; it is a "
            "claim with no rule behind it.",
            "token": sign(claims(role="superuser")),
        },
        {
            "name": "no_subject",
            "accepted": False,
            "why": "`sessions.subject` carries the `sub` and there is no user table behind "
            "it (§10.5), so a token with no subject has nothing to record.",
            "token": sign(claims(sub=None)),
        },
        {
            "name": "no_expiry",
            "accepted": False,
            "why": "A library that checks a claim it finds and ignores one it does not "
            "turns this into a token that never expires.",
            "token": sign(claims(exp=None)),
        },
        {
            "name": "unsigned",
            "accepted": False,
            "why": "`alg: none`. The classic, and what an `algorithms` list of exactly one "
            "exists to refuse.",
            "token": jwt.encode(claims(), key="", algorithm="none"),
        },
        {
            "name": "malformed",
            "accepted": False,
            "why": "Not a JWT at all. Refused as a branch on a known condition, not as an "
            "exception that escaped.",
            "token": "not-a-token",
        },
    ]

    document = {
        "_": __doc__.strip().splitlines()[0],
        "generator": "scripts/mint-fixtures.py",
        # Asserted by both suites against their own configured defaults. A gateway that
        # checked a different audience, or tolerated five minutes of skew where the Python
        # services tolerate none, would pass every verdict below and still not be the same
        # rule.
        "settings": {
            "audience": settings.audience,
            "issuer": settings.issuer,
            "algorithm": settings.algorithm,
            "role_claim": settings.role_claim,
            "clock_skew_seconds": settings.clock_skew_seconds,
        },
        "public_key": public,
        "tokens": fixtures,
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(document, indent=2) + "\n")
    print(f"{OUTPUT}: {len(fixtures)} fixtures")


if __name__ == "__main__":
    main()
