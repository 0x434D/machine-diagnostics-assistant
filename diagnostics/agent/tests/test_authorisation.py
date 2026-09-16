"""§1.8, for the agent: both routes refuse an unauthenticated request, and one needs admin.

The routes are taken off the application rather than written down here, for the reason the
analysis service's copy of this file gives: a list passes on the day it is written and goes
on passing while an endpoint added later is open by default. The walk itself lives in
`auth.testing`, shared by the two FastAPI services so that the two proofs cannot drift.
"""

from __future__ import annotations

import pytest
from agent.app import app
from auth.requests import admin, principal
from auth.testing import api_routes, concrete, mint, served
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

ADMIN_ONLY: frozenset[tuple[str, str]] = frozenset(
    {("GET", "/prompts"), ("POST", "/knowledge/reload")}
)
"""§10.5's matrix: both of its four admin rows that exist to be gated in M5 live here.

The raw model exchange and the system prompts are one; reloading the knowledge base is the
other, and `app.reload_knowledge` says why this service rather than the analysis one. The
remaining two rows — approving improvement-loop proposals and managing users — are endpoints
nowhere, and inventing one to gate would be gating a thing that does not exist.

Asking a question is not an admin row: `POST /ask` is the first line of that matrix and is
open to `user`, which the tests below assert rather than assume."""

OPEN: frozenset[tuple[str, str]] = frozenset()
"""Deliberate exceptions. There are none, and `agent.app` says why the interactive docs and
`/openapi.json` are not served at all."""


def _requires_admin(route: APIRoute) -> bool:
    return any(dependency.call is admin for dependency in route.dependant.dependencies)


def test_the_walk_finds_every_route() -> None:
    """A test that iterates an empty list passes loudest of all."""
    assert set(served(app)) == {
        ("POST", "/ask"),
        ("GET", "/prompts"),
        ("POST", "/knowledge/reload"),
    }


@pytest.mark.parametrize(("method", "path"), served(app))
def test_every_served_route_refuses_an_unauthenticated_request(
    method: str, path: str
) -> None:
    response = TestClient(app).request(method, concrete(path))

    if (method, path) in OPEN:
        assert response.status_code != 401, (
            f"{method} {path} is listed open but refused"
        )
        return
    assert response.status_code == 401, f"{method} {path} answered without a token"
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_asking_a_question_without_a_token_is_401_and_not_a_422_about_the_body() -> (
    None
):
    """The one that would rot first. `POST /ask` takes a body, and a service that validated
    it before checking the token would answer an anonymous caller with the shape of its own
    request model."""
    assert TestClient(app).post("/ask", json={}).status_code == 401


def test_the_guard_is_declared_once_for_the_whole_application() -> None:
    declared = [dependency.dependency for dependency in app.router.dependencies]

    assert principal in declared


def test_only_the_endpoints_named_here_require_admin() -> None:
    required = {
        (method, route.path)
        for route in api_routes(app)
        if _requires_admin(route)
        for method in sorted(route.methods or ())
    }

    assert required == ADMIN_ONLY


# --- §10.5's matrix, over the wire --------------------------------------------------------


def _client(role: str) -> TestClient:
    return TestClient(app, headers={"Authorization": f"Bearer {mint(role=role)}"})


def test_the_prompts_need_admin_and_a_user_is_403_not_401() -> None:
    refused = _client("user").get("/prompts")

    assert refused.status_code == 403
    assert "admin" in refused.json()["detail"]


def test_an_admin_reads_the_prompts_the_running_service_sends_the_model() -> None:
    """Off the running service rather than off the source of whatever version somebody
    hopes is deployed — which is the only reading of this that is worth having."""
    served_prompts = _client("admin").get("/prompts")

    assert served_prompts.status_code == 200
    body = served_prompts.json()
    assert "read-only" in body["investigation"]
    assert body["provider"] == "scripted"


@pytest.mark.usefixtures("scripted_stream", "no_database")
def test_a_user_may_ask_a_question() -> None:
    """§10.5's first matrix row is open to both columns, and this is what stops a later
    tightening from being a drift nobody decided on."""
    answered = _client("user").post("/ask", json={"question": "how many rejects?"})

    assert answered.status_code == 200


def test_reloading_the_knowledge_base_needs_admin_and_a_user_is_403_not_401() -> None:
    """§14 names both codes and they have to stay apart: 401 is *I do not know you*, 403 is
    *I know you and this is not yours*. Collapsed into one, a permission bug reads as a
    login bug for a day."""
    refused = _client("user").post("/knowledge/reload")

    assert refused.status_code == 403
    assert "admin" in refused.json()["detail"]


def test_an_admin_can_reload_the_knowledge_base() -> None:
    reloaded = _client("admin").post("/knowledge/reload")

    assert reloaded.status_code == 200
    assert reloaded.json()["documents"] > 0
