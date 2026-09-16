"""§1.8 — *every diagnostics endpoint refuses an unauthenticated request, MCP included.*

§14's sentence in full: *"Unauthenticated requests to every diagnostics endpoint return 401,
MCP included; an admin-only action performed as `user` returns 403."*

**Why this file is in the `auth` package and not in one of the three services.** Each service
already proves its own half in the gate (`test_authorisation.py`, three copies, one per
service, in process against `TestClient`). None of them can say §1.8's actual sentence, which
is about *every* endpoint: the analysis service cannot answer for the agent, neither can
answer for the MCP server, and none of the three holds the `agent.sessions` row that records
whose question it was. The claim spans all of them, so it is made where the rule they share
lives.

**What is running when these assertions run.** A Postgres container carrying the gateway's
own migrations and the agent's Alembic history, the analysis service and the agent served by
uvicorn on loopback sockets, and §6.11's MCP server on a third — each over real TCP, spoken
to with a plain HTTP client that holds no reference to any of them. The same shape M4's two
proofs use, for the same reason: what is being proved is the behaviour of the door a request
actually meets, and a `TestClient` dispatching into an ASGI app in the same process is a
shortcut past the half that would be wrong in a deployment.

**The enumeration is the whole point.** The endpoints are taken off the applications and the
tools off the server, never written down here. A list of paths in a proof passes on the day
it is written and goes on passing while the endpoint somebody adds six months from now is
open by default — and that endpoint is precisely the one §1.8 is about. `auth.testing._walk`
is what makes the omission fail rather than pass: a surface it cannot see the guard on (a
mounted sub-application, a raw Starlette route, the interactive docs switched back on) raises
at collection instead of being skipped by pattern.

**Every refusal proof here is two-sided.** A service that refused everything, or that was
simply broken, satisfies "no token is refused" perfectly. So each endpoint is also called
*with* a `user` token and must not answer 401 — the door has to be a door.

**What this does not establish.** That the issuer is one. There is no issuer in this
milestone: `auth.testing` mints from a keypair in memory and `scripts/mint-token.py` from one
on disk, both standing where Zitadel will. So §14's other identity line — *"adding Google or
Microsoft as a login option is demonstrably an admin-UI task: no code change, no redeploy"* —
is not demonstrated by anything here and is not demonstrated anywhere else either. The M5
plan ruled that out deliberately and `measurements/authenticity/README.md` records it.

Marked `authenticity` and run by `make verify`.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Final

import httpx
import psycopg
import pytest
import pytest_asyncio
import uvicorn
from agent.app import app as agent_app
from alembic import command
from alembic.config import Config
from analysis.app import app as analysis_app
from analysis.config import Settings as AnalysisSettings
from analysis.db import reset_pool
from analysis.dependencies import settings_dependency
from auth.testing import AUDIENCE, ISSUER, PUBLIC_PEM, concrete, mint, served
from fastapi import FastAPI
from mcp import Client
from mcp_server import server
from mcp_server.config import PROTOCOL_REVISION
from mcp_server.config import Settings as McpSettings
from testcontainers.postgres import PostgresContainer

pytestmark = pytest.mark.authenticity

DIAGNOSTICS = Path(__file__).resolve().parents[2]
MIGRATIONS = DIAGNOSTICS / "gateway" / "Gateway" / "Migrations"
"""The gateway's embedded SQL, read from the directory rather than listed — a migration
added and not listed here would leave these proofs passing against a schema nothing
deploys."""

AGENT_ALEMBIC = DIAGNOSTICS / "agent"
"""`agent.sessions` lives here (§8's ownership split: Alembic owns `agent.*`, the gateway's
embedded SQL owns everything else). The last proof below reads that table."""

# Pinned by digest, not tag (§10.7). scripts/pin-images.sh re-resolves it.
POSTGRES_IMAGE = (
    "postgres:17-bookworm@sha256:"
    "051f7b7b3abdd564d5d1bd1e8c4b9c1b6e77087d1dd22020ede611c096a272e0"
)

ADMIN_ONLY: Final[frozenset[tuple[str, str]]] = frozenset(
    {("GET", "/prompts"), ("POST", "/knowledge/reload")}
)
"""§10.5's matrix, as far as it exists to be gated: the raw model exchange and the system
prompts, and reloading the knowledge base. Its other two admin rows — approving
improvement-loop proposals, and managing users — are endpoints nowhere, and inventing one to
gate would be gating a thing that does not exist.

Named here so the proof below can assert the set *from behaviour over the wire* — which
endpoints actually answer 403 to a `user` — rather than from the dependency tree the service
was built with. The gate's `test_authorisation.py` makes the structural claim; this one makes
the observable one, and a decorator that was declared and then bypassed by a router would
pass the first and fail this."""

ENDPOINT_FLOOR = 19
"""Today's surface: sixteen analysis operations and the agent's three. A floor rather than an
equality, because the walk's job is to grow on its own when an endpoint is added. What it
guards is the failure a parametrisation cannot report for itself — a walk that found nothing
iterates nothing and passes loudest of all."""

TOOL_FLOOR = 15
"""§5.3's read operations plus §6.11's `diagnose`, for the same reason."""

MCP_HEADERS: Final[dict[str, str]] = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
    "MCP-Protocol-Version": PROTOCOL_REVISION,
}

TIMEOUT = 120.0
"""Generous: `POST /ask` under a token runs the whole staged pipeline."""


# --- the stack, running -------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def configured() -> Iterator[None]:
    """The three services given the key, the audience and the issuer a deployment gives them.

    Through the environment rather than through `dependency_overrides`, because that is where
    the deployment states it and because the one thing this file must not do is prove the
    guard against a stand-in for the guard. `auth.requests` reads `Settings()` per request, so
    setting these is the whole of the configuration all three services need.
    """
    with pytest.MonkeyPatch.context() as environment:
        environment.setenv("AUTH_PUBLIC_KEY", PUBLIC_PEM)
        environment.setenv("AUTH_AUDIENCE", AUDIENCE)
        environment.setenv("AUTH_ISSUER", ISSUER)
        yield


@pytest.fixture(scope="module")
def database() -> Iterator[str]:
    """Postgres with both owners' schemas applied, and no rows in it.

    **Deliberately unseeded.** §1.8 is a claim about refusal, not about data: what a proof of
    the door needs is that a request reaches the handler, and a 404 out of an empty table
    says that as clearly as a row would. Seeding here would be a third copy of a fixture that
    already exists twice (`agent/tests/test_authenticity.py`, `mcp/tests/test_authenticity.py`)
    for proofs that genuinely need rows, and this one does not.
    """
    with PostgresContainer(POSTGRES_IMAGE, driver=None) as container:
        # str(), because testcontainers ships no py.typed and mypy cannot take its word for
        # the return type -- see the [mypy-testcontainers.*] override.
        url = str(container.get_connection_url())
        with psycopg.connect(url) as conn:
            for migration in sorted(MIGRATIONS.glob("*.sql")):
                conn.execute(migration.read_text())
            conn.commit()
        with pytest.MonkeyPatch.context() as environment:
            environment.setenv("AGENT_DATABASE_URL", url)
            alembic = Config(str(AGENT_ALEMBIC / "alembic.ini"))
            alembic.set_main_option("script_location", str(AGENT_ALEMBIC / "alembic"))
            command.upgrade(alembic, "head")
        yield url


async def _serve(app: object) -> tuple[str, uvicorn.Server, asyncio.Task[None]]:
    """One ASGI application on an ephemeral loopback port.

    Ephemeral rather than the configured port: the gate must not fail because a developer has
    something on 8001, and §10.3's point is that the number is configuration — not that this
    particular one is free.
    """
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")  # type: ignore[arg-type]  # uvicorn types its app parameter as its own ASGIApplication alias
    running = uvicorn.Server(config)
    bound = config.bind_socket()
    port = int(bound.getsockname()[1])
    serving = asyncio.create_task(running.serve(sockets=[bound]))
    while not running.started:
        await asyncio.sleep(0.01)
    return f"http://127.0.0.1:{port}", running, serving


async def _stop(running: uvicorn.Server, serving: asyncio.Task[None]) -> None:
    running.should_exit = True
    await serving


@pytest_asyncio.fixture
async def analysis_service(database: str) -> AsyncIterator[str]:
    """§5.3 over a socket, as the owner rather than as the `analysis` role.

    Per test, not per module, and that is not a preference. A uvicorn server started inside a
    module-scoped async fixture runs its accept loop in the fixture's event loop, while each
    test body runs in its own — so the listening socket is bound, the kernel accepts the
    connection, and nothing ever reads it. The symptom is a request that hangs rather than a
    fixture that errors, which is the worst shape a test-harness bug can take. Starting the
    three services per test costs under a second: they are in-process ASGI applications, and
    the expensive thing here is the Postgres container, which is module-scoped and stays up.

    The grant boundary is `analysis/tests/test_read_layer.py`'s claim and is proven there
    against both identities. Borrowing it here would make §1.8 fail whenever that one did,
    which is two proofs wearing one failure.
    """
    analysis_app.dependency_overrides[settings_dependency] = lambda: AnalysisSettings(
        database_url=database
    )
    reset_pool()
    url, running, serving = await _serve(analysis_app)
    try:
        yield url
    finally:
        await _stop(running, serving)
        analysis_app.dependency_overrides.clear()
        reset_pool()


@pytest_asyncio.fixture
async def agent_service(analysis_service: str, database: str) -> AsyncIterator[str]:
    """The agent, pointed at that analysis service the way `diagnostics/compose.yml` points
    it — through the environment, so the path the deployment takes is the path under proof."""
    with pytest.MonkeyPatch.context() as environment:
        environment.setenv("AGENT_ANALYSIS_URL", analysis_service)
        environment.setenv("AGENT_DATABASE_URL", database)
        url, running, serving = await _serve(agent_app)
        try:
            yield url
        finally:
            await _stop(running, serving)


@pytest.fixture
def mcp_settings(analysis_service: str, agent_service: str) -> McpSettings:
    return McpSettings(analysis_url=analysis_service, agent_url=agent_service)


@pytest_asyncio.fixture
async def mcp_endpoint(mcp_settings: McpSettings) -> AsyncIterator[str]:
    url, running, serving = await _serve(server.build_app(mcp_settings))
    try:
        yield f"{url}{mcp_settings.path}"
    finally:
        await _stop(running, serving)


# --- what the enumeration walks -----------------------------------------------------------


def endpoints(services: dict[str, FastAPI]) -> list[tuple[str, str, str]]:
    """Every (method, url, path) the diagnostics stack's two FastAPI services serve.

    Off the applications, never off a list. `served` yields the route templates and
    `concrete` fills their parameters with a value no handler ever sees — the request is not
    supposed to get that far.
    """
    return [
        (method, f"{url}{concrete(path)}", path)
        for url, app in services.items()
        for method, path in served(app)
    ]


async def tool_names(settings: McpSettings) -> list[str]:
    """Every tool this server serves, asked of the server rather than of the contract.

    In process, and that is not the shortcut it looks like: what is being enumerated is the
    tool set, and the refusals below are driven over HTTP against the same `build()` the
    served application wraps. Asking for the list over HTTP would need a token, which is the
    thing under proof.
    """
    async with Client(server.build(settings)) as client:
        listing = await client.list_tools()
    return sorted(tool.name for tool in listing.tools)


def rpc(method: str, params: dict[str, object] | None = None) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}


# --- the proofs ---------------------------------------------------------------------------


async def test_every_diagnostics_endpoint_refuses_an_unauthenticated_request(
    analysis_service: str, agent_service: str
) -> None:
    """§14's first clause, over TCP, against every route the two services actually serve.

    Both sides of it. Without a token each endpoint is a 401 carrying RFC 6750's challenge —
    a refusal that does not say how to authenticate is a dead end for any client. With a
    `user` token none of them is, which is what separates a guarded service from a broken
    one: every assertion above this line is satisfied perfectly by a stack that refuses
    everything, and that stack fails here.

    The 401 arrives even for the endpoints whose own parameters this request is missing.
    That is not incidental: FastAPI resolves dependencies before it validates query
    parameters, so an endpoint answering 422 first would be telling an anonymous caller what
    it expects to be asked.
    """
    surface = endpoints({analysis_service: analysis_app, agent_service: agent_app})

    assert len(surface) >= ENDPOINT_FLOOR

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        for method, url, path in surface:
            anonymous = await client.request(method, url)

            assert anonymous.status_code == 401, (
                f"{method} {path} answered with no token"
            )
            assert anonymous.headers["WWW-Authenticate"] == "Bearer"
            assert anonymous.json()["detail"] == "authentication required"

            known = await client.request(
                method, url, headers={"Authorization": f"Bearer {mint(role='user')}"}
            )

            assert known.status_code != 401, f"{method} {path} refuses a valid token"


async def test_the_mcp_server_refuses_an_unauthenticated_request_too(
    mcp_settings: McpSettings, mcp_endpoint: str
) -> None:
    """§14 names MCP specifically, so it is proved specifically rather than by implication.

    Every tool the server advertises, the protocol methods that would tell a caller what
    those tools are, and a path this server does not serve at all — a 404 handed out without
    a token is still an answer about which paths exist, and the guard is a middleware
    precisely so there is none to hand out.

    Driven with a plain HTTP client rather than an MCP one, and deliberately: what a caller
    without a token meets is an HTTP 401 raised before any of this server's protocol handling
    runs, and an MCP client would be the wrong instrument for looking at that.
    """
    tools = await tool_names(mcp_settings)

    assert len(tools) >= TOOL_FLOOR

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        for name in tools:
            refused = await client.post(
                mcp_endpoint,
                json=rpc("tools/call", {"name": name, "arguments": {}}),
                headers=MCP_HEADERS,
            )

            assert refused.status_code == 401, f"{name} was called without a token"
            assert refused.headers["WWW-Authenticate"] == "Bearer"

        for method in ("initialize", "tools/list", "resources/list"):
            listed = await client.post(
                mcp_endpoint, json=rpc(method), headers=MCP_HEADERS
            )

            assert listed.status_code == 401, f"{method} answered without a token"

        # A path this server does not serve. Refused at the door like every other.
        assert (await client.get(f"{mcp_endpoint}-not-a-path")).status_code == 401

        # And the other side, without which every assertion above is satisfied perfectly by
        # a server that is simply down: the same request with a `user` token is not refused,
        # and what answers it is the protocol rather than the middleware — a JSON-RPC body
        # can only have come from behind the door.
        #
        # Not asserted as a 200, and not by completing the handshake. This client is
        # hand-rolled and is not a conformant MCP client: §6.11 pins a revision whose
        # `initialize` carries a `_meta` envelope, and spelling that out here would put a
        # second, hand-written reading of a versioned wire protocol in the file whose subject
        # is the door in front of it. That a real client carrying a `user` token then reaches
        # every tool and every resource is `mcp/tests/test_authorisation.py`'s claim, made
        # with the SDK's own client where it belongs.
        opened = await client.post(
            mcp_endpoint,
            json=rpc("tools/list"),
            headers={**MCP_HEADERS, "Authorization": f"Bearer {mint(role='user')}"},
        )

        assert opened.status_code != 401
        assert opened.json()["jsonrpc"] == "2.0"


async def test_an_admin_only_action_as_user_is_403_and_not_401(
    analysis_service: str, agent_service: str
) -> None:
    """§14's second clause, and the reason the two codes must stay apart.

    401 means *this request carries no identity I accept*; 403 means *I know who you are and
    this is not yours*. Collapsed into one, a permission bug reads as a login bug for a day —
    the operator re-pastes a perfectly good token and the UI goes on saying "not signed in".
    So each of §10.5's two live admin rows is asked three times: as nobody, as `user`, as
    `admin`, and the three answers must be three different things.

    The last assertion is the one that could not be made in process. `ADMIN_ONLY` is checked
    against *what the running stack does* — the set of endpoints across both services that
    answer 403 to a `user` — rather than against the dependency tree the application was
    assembled from. An `admin` dependency declared and then bypassed by the router that
    served it would satisfy the gate's structural test and fail this one.
    """
    user = {"Authorization": f"Bearer {mint(subject='operator-7', role='user')}"}
    administrator = {"Authorization": f"Bearer {mint(subject='eng-1', role='admin')}"}

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        for method, path in sorted(ADMIN_ONLY):
            url = f"{agent_service}{path}"

            anonymous = await client.request(method, url)
            refused = await client.request(method, url, headers=user)
            allowed = await client.request(method, url, headers=administrator)

            assert anonymous.status_code == 401, f"{method} {path} as nobody"
            assert refused.status_code == 403, f"{method} {path} as user"
            assert allowed.status_code == 200, f"{method} {path} as admin"
            # It says which role was needed and which one the token carried, because §7.2's
            # reader has to be able to act on it — "forbidden" alone is a dead end.
            detail = refused.json()["detail"]
            assert "admin" in detail
            assert "'user'" in detail

        forbidden = set()
        for method, url, path in endpoints(
            {analysis_service: analysis_app, agent_service: agent_app}
        ):
            response = await client.request(method, url, headers=user)
            if response.status_code == 403:
                forbidden.add((method, path))

    assert forbidden == ADMIN_ONLY


async def test_the_session_row_carries_the_sub_off_the_token(
    agent_service: str, database: str
) -> None:
    """§10.5: there is no `users` table, and `sessions.subject` carries the OIDC `sub`.

    That sentence is what makes §5.2's trace tables an audit trail rather than a log of
    questions nobody can attribute, and it is checkable in exactly one place: the row. So two
    questions are asked by two different subjects and the two rows are read back out of
    Postgres.

    Two rather than one, because a single row equal to its own token's subject is also what a
    column defaulting to that string would look like — and `subject` was nullable and unwritten
    until this milestone, so that is the state this proof has to be able to tell apart. Two
    subjects that differ can only have come off the tokens.

    The session ids are the caller's, which is `Question.session_id`'s ordinary use: it is how
    a second question continues a conversation, and here it is how the row a given token wrote
    is identified without the endpoint having to hand one back.
    """
    subjects = {uuid.uuid4(): "auth0|proof-of-1.8", uuid.uuid4(): "google|someone-else"}

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        for session, subject in subjects.items():
            answered = await client.post(
                f"{agent_service}/ask",
                json={
                    "question": "how many rejects in the last hour?",
                    "session_id": str(session),
                },
                headers={
                    "Authorization": f"Bearer {mint(subject=subject, role='user')}"
                },
            )

            assert answered.status_code == 200

    with psycopg.connect(database) as conn:
        recorded = {
            str(row[0]): row[1]
            for row in conn.execute("SELECT id, subject FROM agent.sessions").fetchall()
        }

    assert recorded == {str(session): who for session, who in subjects.items()}
