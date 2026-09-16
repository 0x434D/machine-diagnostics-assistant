"""The HTTP half: a bearer token off the request, 401 for no principal, 403 for the wrong
role.

The two codes have to stay apart and §14 names both. 401 means *this request carries no
identity I accept*; 403 means *I know who you are and this is not yours*. Collapsing them
into one is the shortcut that makes a permission bug look like a login bug for a day.
"""

from __future__ import annotations

import pytest
from auth.requests import RequireToken, admin, presented, principal
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from .keys import AUDIENCE, ISSUER, PUBLIC_PEM, mint


@pytest.fixture(autouse=True)
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """The deployment's environment, which is how a service is given these (§10.3)."""
    monkeypatch.setenv("AUTH_PUBLIC_KEY", PUBLIC_PEM)
    monkeypatch.setenv("AUTH_AUDIENCE", AUDIENCE)
    monkeypatch.setenv("AUTH_ISSUER", ISSUER)


def _request(authorization: str | None = None) -> Request:
    headers = (
        [(b"authorization", authorization.encode())]
        if authorization is not None
        else []
    )
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers})


def test_a_bearer_token_is_read_off_the_header() -> None:
    assert presented(_request("Bearer abc.def.ghi")) == "abc.def.ghi"
    assert presented(_request("bearer abc.def.ghi")) == "abc.def.ghi"


def test_anything_that_is_not_a_bearer_token_is_no_token() -> None:
    """Basic auth is not a token this system has any rule for, and reading its payload as
    one would hand a malformed string to the verifier and call the refusal a signature
    failure."""
    assert presented(_request()) is None
    assert presented(_request("Basic dXNlcjpwYXNz")) is None
    assert presented(_request("Bearer   ")) is None


def test_a_request_with_no_token_is_401() -> None:
    with pytest.raises(HTTPException) as refusal:
        principal(_request())

    assert refusal.value.status_code == 401
    # RFC 6750: a 401 that does not say how to authenticate is a dead end for any client.
    assert refusal.value.headers == {"WWW-Authenticate": "Bearer"}


def test_a_valid_token_yields_the_principal() -> None:
    who = principal(_request(f"Bearer {mint(subject='operator-7', role='user')}"))

    assert (who.subject, who.role) == ("operator-7", "user")


def test_an_authenticated_user_is_403_on_an_admin_action_not_401() -> None:
    with pytest.raises(HTTPException) as refusal:
        admin(_request(f"Bearer {mint(role='user')}"))

    assert refusal.value.status_code == 403
    assert "admin" in str(refusal.value.detail)


def test_an_admin_passes_the_admin_check() -> None:
    assert admin(_request(f"Bearer {mint(role='admin')}")).role == "admin"


def test_an_unauthenticated_request_to_an_admin_action_is_still_401() -> None:
    """The order matters: a missing token is an authentication failure even when the action
    it was aimed at needed a role it would not have had."""
    with pytest.raises(HTTPException) as refusal:
        admin(_request())

    assert refusal.value.status_code == 401


# --- the middleware, for the server that has no dependency injection ----------------------


def _reached(request: Request) -> PlainTextResponse:
    del request
    return PlainTextResponse("reached")


def _app() -> Starlette:
    """A Starlette application shaped like the MCP server's: routes it did not declare
    itself, and no place to hang a per-route dependency."""
    application = Starlette(routes=[Route("/mcp", _reached)])
    application.add_middleware(RequireToken)
    return application


def test_the_middleware_refuses_before_the_application_is_reached() -> None:
    response = TestClient(_app()).get("/mcp")

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.text != "reached"


def test_the_middleware_lets_a_valid_token_through() -> None:
    response = TestClient(_app()).get(
        "/mcp", headers={"Authorization": f"Bearer {mint()}"}
    )

    assert response.status_code == 200
    assert response.text == "reached"


def test_a_path_the_application_does_not_serve_is_refused_too() -> None:
    """A 404 reached without a token would say which paths exist, and the enumeration this
    milestone rests on is only as good as the requests that never get past the door."""
    assert TestClient(_app()).get("/not-a-route").status_code == 401
