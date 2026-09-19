"""The issuer as a deployment configures it: through the environment, never through an
override of its own dependency functions.

`app.dependency_overrides` would let a test reach past the settings and hand the endpoints
whatever it liked -- and the thing most worth knowing about this service is that its users,
their roles and its key come from configuration. A test that supplied them another way would
be checking a path no deployment takes.

The keypair is `auth.testing`'s, which exists for exactly this and is generated in memory
once per process. Using the validators' own test keypair is also what makes the central
assertion possible: a token minted here is verified by `auth.tokens.verify` against the
public half, which is the same function the four services run.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from auth.testing import AUDIENCE, ISSUER, PRIVATE_PEM, PUBLIC_PEM
from fastapi.testclient import TestClient
from issuer.app import app

OPERATOR = ("operator-7", "operator-development")
"""A `user`. The subject the rest of the repository already uses in its examples."""

LEAD = ("quality-lead", "quality-lead-development")
"""An `admin`, because §10.5's role is a claim and a test that only ever saw one value
would not notice a service that put the same one on every token."""

USERS = {
    OPERATOR[0]: {"password": OPERATOR[1], "role": "user"},
    LEAD[0]: {"password": LEAD[1], "role": "admin"},
}


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """An issuer with a key and two accounts, and the claim shape the services validate."""
    monkeypatch.setenv("ISSUER_PRIVATE_KEY", PRIVATE_PEM)
    monkeypatch.setenv("ISSUER_USERS", json.dumps(USERS))
    monkeypatch.setenv("AUTH_PUBLIC_KEY", PUBLIC_PEM)
    monkeypatch.setenv("AUTH_AUDIENCE", AUDIENCE)
    monkeypatch.setenv("AUTH_ISSUER", ISSUER)
    with TestClient(app) as client:
        yield client


@pytest.fixture
def unconfigured(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """An issuer nobody has given a key or an account: the resting state of a checkout."""
    monkeypatch.delenv("ISSUER_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("ISSUER_USERS", raising=False)
    with TestClient(app) as client:
        yield client
