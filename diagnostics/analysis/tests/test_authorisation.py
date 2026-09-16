"""§1.8, for this service: every route it serves refuses an unauthenticated request.

**The routes are taken off the application, not written down here.** A list of paths in a
test file passes on the day it is written and goes on passing while an endpoint added six
months later is open by default -- and the endpoint added later is precisely the one nobody
thought about. Walking what the application actually serves makes the omission fail instead:
a new route joins the parametrisation without anyone editing this file, and a surface that
this walk cannot see the guard on (a mounted sub-application, a raw Starlette route, the
interactive docs switched back on) fails at collection rather than being skipped by pattern.

The same walk answers §10.5's matrix: which routes require `admin` is derived from the
application's own dependency tree and compared against the set named below, so tightening an
endpoint -- or loosening one -- is an edit here and a decision somebody made.
"""

from __future__ import annotations

import pytest
from analysis.app import app
from auth.requests import admin, principal
from auth.testing import api_routes, concrete, mint, served
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

ADMIN_ONLY: frozenset[tuple[str, str]] = frozenset()
"""§10.5's matrix, as it applies to this service: **nothing here is admin-only.**

Every row of the matrix this service serves -- asking questions, viewing answers, evidence,
traces, traceability and containment -- is open to `user`, and that is asserted rather than
assumed so a later tightening is an edit here and a decision somebody made.

Its two admin rows that exist to be gated in M5 both live on the agent: reloading the
knowledge base, and the raw model exchange and system prompts. Reloading was put there
rather than here for a reason that is not arbitrary -- §6.11 generates the MCP tool set from
this service's contract and refuses any operation that is not a GET, so a write endpoint on
this service would either break that binding at load or force it to start skipping
operations, and "neither binding can do anything the other cannot" would stop being
checkable. The other two rows -- approving improvement-loop proposals and managing users --
are endpoints nowhere, and inventing one to gate would be gating a thing that does not
exist."""

OPEN: frozenset[tuple[str, str]] = frozenset()
"""Deliberate exceptions -- routes that answer without a token. There are none.

The interactive docs and `/openapi.json` would have been the obvious candidates, and
`analysis.app` turns them off instead: the schema is committed in `contracts/` and generated
from `app.openapi()`, so a running service serves nothing the repository does not already
hold, and an unauthenticated route that enumerates every other route is an exception §1.8
would have had to carry forever."""


def _requires_admin(route: APIRoute) -> bool:
    return any(dependency.call is admin for dependency in route.dependant.dependencies)


def test_the_walk_finds_the_whole_service() -> None:
    """The enumeration is worth what it enumerates: a test that iterates an empty list
    passes loudest of all. §5.3's operations, and one path serves two of them."""
    assert len(served(app)) >= 15


@pytest.mark.parametrize(("method", "path"), served(app))
def test_every_served_route_refuses_an_unauthenticated_request(
    method: str, path: str
) -> None:
    """Every one, including the ones whose own parameters are missing from this request.

    That is not incidental. FastAPI solves dependencies before it validates query
    parameters, so these arrive as 401 rather than 422 -- an endpoint that answered 422
    first would be telling an anonymous caller what it expects to be asked.
    """
    response = TestClient(app).request(method, concrete(path))

    if (method, path) in OPEN:
        assert response.status_code != 401, (
            f"{method} {path} is listed open but refused"
        )
        return
    assert response.status_code == 401, f"{method} {path} answered without a token"
    # RFC 6750, and the difference between a client that can recover and one that cannot.
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_the_guard_is_declared_once_for_the_whole_application() -> None:
    """The structural half, and the reason a route added tomorrow is closed by default.

    An application-wide dependency is one line that cannot be forgotten per route; the
    alternative is a decision to be repeated sixteen times, of which fifteen would be.
    """
    declared = [dependency.dependency for dependency in app.router.dependencies]

    assert principal in declared


def test_only_the_endpoints_named_here_require_admin() -> None:
    """§10.5: asking questions, viewing answers, evidence, traces, traceability and
    containment are open to `user`, and this asserts it rather than assuming it."""
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


def test_a_user_can_read_a_knowledge_document() -> None:
    """The first row of §10.5's matrix, over the wire: a `user` opens a cited procedure.

    The structural test above says no route on this service requires `admin`; this says the
    same thing from the other side, on the endpoint a citation resolves through.
    """
    assert _client("user").get("/knowledge/SOP-01").status_code == 200
