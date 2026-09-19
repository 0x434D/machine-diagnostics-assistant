"""What the token endpoint does, stated as what a caller gets back.

The central one is the first: a token this issuer mints is a token `auth.tokens.verify`
accepts. Everything downstream of the login screen rests on that one sentence, and it is the
sentence a hand-written assertion about the JWT's fields would *not* have made -- the
services do not read fields, they verify a signature, an audience, an issuer and a role.
"""

from __future__ import annotations

import json

import jwt
import pytest
from auth.config import Settings as ClaimSettings
from auth.testing import PUBLIC_PEM
from auth.tokens import verify
from fastapi.testclient import TestClient
from issuer.app import REFUSAL

from .conftest import LEAD, OPERATOR


def sign_in(client: TestClient, username: str, password: str) -> str:
    response = client.post("/token", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["token_type"] == "Bearer"
    token: str = body["access_token"]
    return token


def test_correct_credentials_yield_a_token_the_services_accept(
    configured: TestClient,
) -> None:
    who = verify(sign_in(configured, *OPERATOR), ClaimSettings(public_key=PUBLIC_PEM))
    assert who is not None, "the services refused a token their own issuer minted"
    assert who.subject == OPERATOR[0]
    assert who.role == "user"


def test_the_role_is_the_one_configuration_gives_the_account(
    configured: TestClient,
) -> None:
    """§10.5's two roles decide what a request may reach, so the difference between them
    has to come from the deployment and not from the token being asked for."""
    who = verify(sign_in(configured, *LEAD), ClaimSettings(public_key=PUBLIC_PEM))
    assert who is not None
    assert who.role == "admin"


def test_a_caller_cannot_ask_for_a_role(configured: TestClient) -> None:
    """The obvious privilege escalation, closed: the request body is a username and a
    password, and a `role` in it is not a field this endpoint has."""
    response = configured.post(
        "/token",
        json={
            "username": OPERATOR[0],
            "password": OPERATOR[1],
            "role": "admin",
        },
    )
    assert response.status_code == 200, response.text
    who = verify(response.json()["access_token"], ClaimSettings(public_key=PUBLIC_PEM))
    assert who is not None
    assert who.role == "user"


@pytest.mark.parametrize(
    "credentials",
    [
        (OPERATOR[0], "not the password"),
        ("nobody-by-that-name", OPERATOR[1]),
        ("nobody-by-that-name", "not the password"),
    ],
)
def test_a_refusal_never_says_which_half_was_wrong(
    configured: TestClient, credentials: tuple[str, str]
) -> None:
    """A wrong password, an unknown username and both at once are one answer.

    An issuer that distinguished them would answer the two questions an attacker asks
    first: does this account exist, and is this the password. `auth.tokens.verify` makes
    the same refusal on the other side of the door for the same reason.
    """
    username, password = credentials
    response = configured.post(
        "/token", json={"username": username, "password": password}
    )
    assert response.status_code == 401
    assert response.json()["detail"] == REFUSAL


def test_the_token_lives_as_long_as_configuration_says(
    monkeypatch: pytest.MonkeyPatch, configured: TestClient
) -> None:
    """§10.3: every number is configuration, and a token lifetime is the number an operator
    most wants to turn down."""
    monkeypatch.setenv("ISSUER_TOKEN_LIFETIME_MINUTES", "5")

    response = configured.post(
        "/token", json={"username": OPERATOR[0], "password": OPERATOR[1]}
    )
    assert response.status_code == 200, response.text
    assert response.json()["expires_in"] == 300

    claims = jwt.decode(
        response.json()["access_token"],
        PUBLIC_PEM,
        algorithms=["RS256"],
        audience=ClaimSettings().audience,
        issuer=ClaimSettings().issuer,
    )
    assert claims["exp"] - claims["iat"] == 300


def test_an_issuer_with_no_signing_key_mints_nothing(
    unconfigured: TestClient,
) -> None:
    """The resting state of a checkout, and it fails closed: no key, no token, and the
    refusal names the setting rather than leaving a developer to guess at a 401."""
    response = unconfigured.post(
        "/token", json={"username": OPERATOR[0], "password": OPERATOR[1]}
    )
    assert response.status_code == 503
    assert "ISSUER_PRIVATE_KEY" in response.json()["detail"]


def test_an_issuer_with_no_accounts_admits_nobody(
    monkeypatch: pytest.MonkeyPatch, configured: TestClient
) -> None:
    """There is no user store and no default account (§10.5). A deployment that configured
    none has nobody to admit, which is the only safe thing an empty list can mean."""
    monkeypatch.setenv("ISSUER_USERS", json.dumps({}))

    response = configured.post(
        "/token", json={"username": OPERATOR[0], "password": OPERATOR[1]}
    )
    assert response.status_code == 401
    assert response.json()["detail"] == REFUSAL


def test_a_role_outside_the_two_is_refused_before_a_token_exists(
    monkeypatch: pytest.MonkeyPatch, configured: TestClient
) -> None:
    """§10.5's roles are `admin` and `user`, closed. A third value is not a third role; it
    is a claim with no rule, and every service would refuse the token it went into. Failing
    where the typo is beats failing four services away from it."""
    monkeypatch.setenv(
        "ISSUER_USERS",
        json.dumps({OPERATOR[0]: {"password": OPERATOR[1], "role": "supervisor"}}),
    )

    with pytest.raises(ValueError, match="supervisor"):
        configured.post(
            "/token", json={"username": OPERATOR[0], "password": OPERATOR[1]}
        )
