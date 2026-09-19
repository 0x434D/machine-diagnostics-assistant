"""The key set, checked by using it rather than by reading it.

A test that compared the document's `n` and `e` against the numbers of the configured key
would be arithmetic agreeing with itself. What a verifier does is fetch the set, pick the key
the token's `kid` names, and check a signature with it -- so that is what happens here, with
PyJWT's own JWKS reader standing in for the verifier that fetches.
"""

from __future__ import annotations

import jwt
import pytest
from auth.config import Settings as ClaimSettings
from auth.testing import OTHER_PRIVATE_PEM, mint
from fastapi.testclient import TestClient

from .conftest import OPERATOR

JWKS = "/.well-known/jwks.json"


def served_key(client: TestClient) -> jwt.PyJWK:
    response = client.get(JWKS)
    assert response.status_code == 200, response.text
    key_set = jwt.PyJWKSet.from_dict(response.json())
    assert len(key_set.keys) == 1, "one signing key, one entry"
    return key_set.keys[0]


def test_the_key_set_serves_the_key_the_tokens_are_signed_with(
    configured: TestClient,
) -> None:
    minted = configured.post(
        "/token", json={"username": OPERATOR[0], "password": OPERATOR[1]}
    )
    assert minted.status_code == 200, minted.text
    token = minted.json()["access_token"]

    key = served_key(configured)
    assert jwt.get_unverified_header(token)["kid"] == key.key_id, (
        "the token names a key the set does not hold, so a verifier that fetched the set "
        "would have nothing to try"
    )

    claims = ClaimSettings()
    decoded = jwt.decode(
        token,
        key,
        algorithms=[claims.algorithm],
        audience=claims.audience,
        issuer=claims.issuer,
    )
    assert decoded["sub"] == OPERATOR[0]


def test_the_served_key_refuses_a_token_from_somewhere_else(
    configured: TestClient,
) -> None:
    """The half that keeps the test above from passing on any key at all.

    `auth.testing.OTHER_PRIVATE_PEM` is a keypair no service is ever given: a token signed
    with it is well-formed and from an issuer nobody trusts, which is precisely what the
    served key has to reject.
    """
    foreign = mint(subject=OPERATOR[0], key=OTHER_PRIVATE_PEM)
    claims = ClaimSettings()

    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(
            foreign,
            served_key(configured),
            algorithms=[claims.algorithm],
            audience=claims.audience,
            issuer=claims.issuer,
        )


def test_an_issuer_with_no_key_serves_an_empty_set(unconfigured: TestClient) -> None:
    """Not a 404 and not a fabricated key: a verifier gets no key rather than one that
    signs nothing, which is the same fail-closed resting state `auth.Settings` takes when
    nobody has configured a public key."""
    response = unconfigured.get(JWKS)
    assert response.status_code == 200
    assert response.json() == {"keys": []}
