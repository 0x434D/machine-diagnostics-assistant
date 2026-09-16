"""§1.7 — *an external MCP client reaches the same tools and gets the same results.*

§6.11: *"The method transfers to an agent we did not build — which is the project's central
claim, demonstrated rather than asserted."*

**What each proof below is worth, stated before it is read.**

*The two answers agree.* This is the weakest of the three and it is written down as such.
`diagnose` calls the agent service's own `POST /ask` (`mcp_server.diagnose`, and that file
argues at length why it must), so the two callers reach one pipeline in one process and
agreement is close to guaranteed by construction. What it does establish is not nothing: the
answer object crosses the binding **unchanged** — not re-summarised, not re-derived, not
flattened into prose — which is the property §6.3 exists for and the one a helpful tool
wrapper would quietly break. Combined with `test_parity.py`, which holds the MCP tool list
against the live OpenAPI operation set in both directions, that is the real content of the
claim: one surface, two bindings, one answer.

*The same query answers alike over both bindings.* Stronger, because these two paths do not
share code below the transport: an MCP `tools/call` and a REST `GET` reach `analysis` through
different bindings and meet only at the service. A binding that dropped a parameter, coerced
a window or lost a field fails here and would pass the first proof.

*An agent following the SOP it read over MCP reaches the same figures.* Strongest, and the
sentence §6.11 actually makes. Nothing of ours drives it: the procedure is read by URI, its
steps are carried out with the tools the server advertises, and the figures that come back are
compared against what the pipeline stated on its own. SOP-05's own failure condition is *"a
number in the prose that does not appear in any tool result"*, and this is that check, made
from outside by a client that was given a document and a tool list and nothing else.

**What none of them establishes.** That a *model* driving this binding would follow the
procedure, or choose those tools. Everything here runs against `ScriptedProvider`; there is no
API key in this environment. The binding is proven; the judgement of whatever binds to it is
not — the same boundary `agent/tests/test_authenticity.py` draws for §1.6.

Marked `authenticity` and run by `make verify`: three services and a Postgres container.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

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
from analysis.dependencies import now_dependency, settings_dependency
from auth.testing import mint
from mcp_server import server
from mcp_server.config import Settings
from mcp_server.diagnose import DIAGNOSE_TOOL
from psycopg import Connection, sql
from psycopg.conninfo import make_conninfo
from testcontainers.postgres import PostgresContainer

from .clients import authorised

pytestmark = pytest.mark.authenticity

DIAGNOSTICS = Path(__file__).resolve().parents[2]
AGENT_ALEMBIC = DIAGNOSTICS / "agent"
"""Where the agent's own migrations live. `POST /ask` writes §5.2's `sessions` row since
M5, so `agent.*` has to exist before this file's agent can answer anything."""

MIGRATIONS = DIAGNOSTICS / "gateway" / "Gateway" / "Migrations"

# Pinned by digest, not tag (§10.7). scripts/pin-images.sh re-resolves it.
POSTGRES_IMAGE = (
    "postgres:17-bookworm@sha256:"
    "051f7b7b3abdd564d5d1bd1e8c4b9c1b6e77087d1dd22020ede611c096a272e0"
)

ANALYSIS_PASSWORD = "analysis-under-proof"

FROZEN_NOW = datetime(2026, 9, 16, 9, 0, tzinfo=UTC)
"""The clock both bindings resolve *last hour* against.

Frozen, and that is load-bearing rather than tidy: the two answers below are compared field
by field, and `last hour` asked twice a minute apart is two different windows. A proof about
two bindings must not be able to fail because of a clock — nor to *pass* because the
comparison was loosened until the clock stopped mattering.
"""

STATIONS = (("S1", "Feeding", 1), ("S2", "Joining", 2), ("S3", "Inspection", 3))
DEFECT_CLASSES = (
    "gap",
    "crack",
    "misalignment",
    "missing_part",
    "scratch",
    "contamination",
)
BASELINE_SCORE = 0.03
BOOSTED_SCORE = 0.82
PARTS = 12
REJECT_EVERY = 4

QUESTION = "how many rejects in the last hour?"
"""A statistics question, because it is the one whose answer is a *figure*.

§1.7's claim is that two callers get the same results, and a figure is the shape of result
that can differ in a way a reader would act on. It also routes to SOP-05, whose procedure is
explicit enough about its steps to be followed from outside — which is what the third proof
does.
"""


def serial(index: int) -> str:
    return f"A-{index:08d}"


# --- the stack, all of it real ------------------------------------------------------------


@pytest.fixture(scope="module")
def postgres_container() -> Iterator[PostgresContainer]:
    with PostgresContainer(POSTGRES_IMAGE, driver=None) as container:
        # str(), because testcontainers ships no py.typed and mypy cannot take its word for
        # the return type -- see the [mypy-testcontainers.*] override.
        with psycopg.connect(str(container.get_connection_url())) as conn:
            for migration in sorted(MIGRATIONS.glob("*.sql")):
                conn.execute(migration.read_text())
            conn.execute(
                sql.SQL("ALTER ROLE analysis PASSWORD {}").format(
                    sql.Literal(ANALYSIS_PASSWORD)
                )
            )
            conn.commit()
        yield container


@pytest.fixture
def seeded(postgres_container: PostgresContainer) -> Iterator[str]:
    """A dozen parts in the hour before `FROZEN_NOW`, three of them rejected.

    Small on purpose. What §1.7 compares is two paths to one answer, so the data has to be
    enough for the answer to carry a figure and a citation and no more than that; the
    queries themselves are proven against `analysis/tests/`, over a fixture built to make
    them wrong.
    """
    reset_pool()
    url = str(postgres_container.get_connection_url())
    with psycopg.connect(url) as conn:
        _truncate(conn)
        _seed(conn)
        conn.commit()
    yield make_conninfo(url, user="analysis", password=ANALYSIS_PASSWORD)
    reset_pool()


def _truncate(conn: Connection) -> None:
    tables = [
        row[0]
        for row in conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'ingest'"
        ).fetchall()
    ]
    assert tables, "nothing to truncate: the ingest schema holds no tables"
    conn.execute(
        sql.SQL("TRUNCATE {} RESTART IDENTITY CASCADE").format(
            sql.SQL(", ").join(sql.Identifier("ingest", table) for table in tables)
        )
    )


def _seed(conn: Connection) -> None:
    for code, name, position in STATIONS:
        conn.execute(
            "INSERT INTO stations (code, name, position_in_line) VALUES (%s, %s, %s)",
            (code, name, position),
        )
    inspection = conn.execute("SELECT id FROM stations WHERE code = 'S3'").fetchone()
    assert inspection is not None

    for index in range(PARTS):
        at = FROZEN_NOW - timedelta(minutes=30 - index)
        reject = index % REJECT_EVERY == 0
        scores = [BASELINE_SCORE] * len(DEFECT_CLASSES)
        if reject:
            scores[DEFECT_CLASSES.index("misalignment")] = BOOSTED_SCORE
        conn.execute(
            "INSERT INTO assemblies (serial, created_at) VALUES (%s, %s)",
            (serial(index), at),
        )
        conn.execute(
            "INSERT INTO inspection_results (assembly_serial, source_ts, station_id,"
            " result, confidence, model_version, defect_classes, confidences)"
            " VALUES (%s, %s, %s, %s, 0.91, 'sim-1', %s, %s)",
            (
                serial(index),
                at,
                inspection[0],
                "reject" if reject else "good",
                list(DEFECT_CLASSES),
                scores,
            ),
        )


async def _serve(app: object) -> tuple[str, uvicorn.Server, asyncio.Task[None]]:
    """One ASGI application on an ephemeral loopback port.

    Ephemeral rather than the configured port, for `test_transport.py`'s reason: the gate
    must not fail because a developer has something on 8002, and §10.3's point is that the
    number is configuration and not that this one is free.
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
async def analysis_service(seeded: str) -> AsyncIterator[str]:
    analysis_app.dependency_overrides[settings_dependency] = lambda: AnalysisSettings(
        database_url=seeded
    )
    analysis_app.dependency_overrides[now_dependency] = lambda: FROZEN_NOW
    url, running, serving = await _serve(analysis_app)
    try:
        yield url
    finally:
        await _stop(running, serving)
        analysis_app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def agent_service(
    analysis_service: str,
    postgres_container: PostgresContainer,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[str]:
    """The agent, over a socket, pointed at the analysis service by its own setting.

    Through the environment rather than a constructor argument, because that is where the
    deployment states it (`AGENT_ANALYSIS_URL`, `diagnostics/compose.yml`): the pipeline
    builds its `Settings` per request, and a seam invented here would leave the path Compose
    takes exercised by nothing.

    Since M5 `POST /ask` writes §5.2's `sessions` row carrying the `sub` off the caller's
    token, so `agent.*` has to exist. Applied here as the database owner, where
    `agent/tests/test_agent_schema.py` applies it as two identities on purpose: that file
    proves the grant boundary, and this one proves §1.7. One fixture doing both would make
    each proof depend on the other's setup.
    """
    owner = str(postgres_container.get_connection_url())
    monkeypatch.setenv("AGENT_ANALYSIS_URL", analysis_service)
    monkeypatch.setenv("AGENT_DATABASE_URL", owner)
    _migrate_agent_schema()
    url, running, serving = await _serve(agent_app)
    try:
        yield url
    finally:
        await _stop(running, serving)


@pytest_asyncio.fixture
async def mcp_endpoint(analysis_service: str, agent_service: str) -> AsyncIterator[str]:
    settings = Settings(analysis_url=analysis_service, agent_url=agent_service)
    url, running, serving = await _serve(server.build_app(settings))
    try:
        yield f"{url}{settings.path}"
    finally:
        await _stop(running, serving)


def _migrate_agent_schema() -> None:
    """`alembic upgrade head` against whatever `AGENT_DATABASE_URL` currently names."""
    config = Config(str(AGENT_ALEMBIC / "alembic.ini"))
    config.set_main_option("script_location", str(AGENT_ALEMBIC / "alembic"))
    command.upgrade(config, "head")


async def ask_over_rest(agent_url: str, question: str) -> dict[str, object]:
    """`POST /ask`, read as a caller that has never heard of this repository would.

    Deliberately **not** `mcp_server.diagnose.AgentClient`, which is the code the other side
    of the comparison runs: two callers that share their HTTP client are one caller, and the
    agreement would then be a fact about that class rather than about the two bindings.
    """
    async with (
        httpx.AsyncClient(timeout=120.0) as client,
        client.stream(
            "POST",
            f"{agent_url}/ask",
            json={"question": question},
            # Since M5 this endpoint refuses an unauthenticated request like every other
            # one. A caller that has never heard of this repository still has a token.
            headers={"Authorization": f"Bearer {mint(role='user')}"},
        ) as response,
    ):
        response.raise_for_status()
        event = ""
        async for line in response.aiter_lines():
            if line.startswith("event:"):
                event = line.removeprefix("event:").strip()
            elif line.startswith("data:") and event == "answer":
                parsed = json.loads(line.removeprefix("data:").strip())
                assert isinstance(parsed, dict)
                return parsed
    raise AssertionError("the agent's stream carried no answer")


# --- the proofs ---------------------------------------------------------------------------


async def test_the_same_question_answered_over_mcp_and_over_rest_agrees(
    agent_service: str, mcp_endpoint: str
) -> None:
    """§1.7's own sentence, and the one this file is most careful about overstating.

    `diagnose` reaches `POST /ask`, so the two answers come out of one pipeline in one
    process: agreement is close to guaranteed by construction and is *not* evidence that two
    implementations converged, because there is only one. What it does prove is that the
    binding hands the answer object over intact — every finding, every citation, every
    caveat, the method and its provider — rather than summarising it into a tool result. A
    wrapper that returned "3 rejects in the last hour" as text would pass a looser reading of
    §1.7 and fail here, which is the failure this proof exists for.
    """
    over_rest = await ask_over_rest(agent_service, QUESTION)
    async with authorised(mcp_endpoint, mint(role="user")) as client:
        result = await client.call_tool(DIAGNOSE_TOOL, {"question": QUESTION})

    assert result.structured_content == over_rest
    # Not an empty agreement: the answer both callers got is an answer, with a figure in it
    # that came out of Postgres.
    findings = over_rest["findings"]
    assert isinstance(findings, list) and findings
    assert f"{PARTS} parts were inspected" in str(over_rest["answer_markdown"])
    assert over_rest["method"] == result.structured_content["method"]


async def test_an_analysis_query_answers_alike_over_both_bindings(
    analysis_service: str, mcp_endpoint: str
) -> None:
    """The half that is not guaranteed by construction.

    An MCP `tools/call` and a REST `GET` share nothing below the transport — the tool schema
    is generated from the contract, the query string is built by `AnalysisTools`, and the two
    meet only at the service. A binding that dropped an argument, widened a window or lost a
    field of the response fails here and passes the proof above.

    The window is passed explicitly and identically to both, because the thing being compared
    is the binding and not the calendar.
    """
    window = {
        "from": (FROZEN_NOW - timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        "to": FROZEN_NOW.isoformat().replace("+00:00", "Z"),
    }

    async with httpx.AsyncClient(
        timeout=30.0, headers={"Authorization": f"Bearer {mint(role='user')}"}
    ) as client:
        rest = (
            await client.get(f"{analysis_service}/inspection/stats", params=window)
        ).json()
    async with authorised(mcp_endpoint, mint(role="user")) as client:
        tool = await client.call_tool("inspectionStats", window)

    assert tool.structured_content == rest
    assert rest["total"] == PARTS
    assert rest["rejects"] == len(range(0, PARTS, REJECT_EVERY))


async def test_an_agent_following_the_sop_it_read_over_mcp_reaches_the_same_figures(
    mcp_endpoint: str, agent_service: str
) -> None:
    """§6.11's actual sentence: the method transfers to an agent we did not build.

    Everything this proof does, it does over MCP and with nothing else: it reads the
    procedure the pipeline says it followed, by the URI §6.11 names; it carries out that
    procedure's first two steps with the tools the server advertises — *"resolve the window
    and check coverage"*, then *"get the figures from the analysis"*; and it compares the
    figures it obtained against the numbers the pipeline stated on its own.

    That comparison is SOP-05's own failure condition — *"a number in the prose that does not
    appear in any tool result"* — checked from outside. It is the proof that an external agent
    reading these documents and calling these tools lands where our pipeline landed, rather
    than merely being able to call it.
    """
    answer = await ask_over_rest(agent_service, QUESTION)
    method = answer["method"]
    assert isinstance(method, dict)
    sops = method["sops_used"]
    assert isinstance(sops, list)
    assert "SOP-05" in sops, sops

    async with authorised(mcp_endpoint, mint(role="user")) as client:
        # Discovered from the listing rather than constructed from the id: an external agent
        # has the protocol and nothing else, and a URI spelled here would be this test
        # agreeing with a rule it had been told. `name` is the document id (`resources.py`).
        listing = await client.list_resources()
        uris = {resource.name: str(resource.uri) for resource in listing.resources}

        # Every document the pipeline routed is readable, so the external agent can reach the
        # same method — not only the one procedure it happened to ask for. CORE-01 and
        # CORE-02 are the two §6.2 never leaves to retrieval, and they are in this list.
        for document in sops:
            assert str(document) in uris, uris
            contents = (await client.read_resource(uris[str(document)])).contents[0]
            # The URI is what identifies the document, not its prose: CORE-01's heading is
            # "How to work" and never says its own id. A scheme a client normalised would
            # come back as `core://core-01` and resolve nothing.
            assert str(contents.uri) == uris[str(document)]
            assert getattr(contents, "text", "").strip()

        procedure = getattr(
            (await client.read_resource("sop://SOP-05")).contents[0], "text", ""
        )
        assert "Resolve the window and check coverage" in procedure
        assert "Get the figures from the analysis" in procedure
        assert (
            "A number in the prose that does not appear in any tool result" in procedure
        )

        window = (
            await client.call_tool("resolveTime", {"expression": "last hour"})
        ).structured_content["window"]
        assert isinstance(window, dict)
        bounds = {"from": str(window["from_ts"]), "to": str(window["to_ts"])}
        coverage = (await client.call_tool("coverage", bounds)).structured_content
        figures = (await client.call_tool("inspectionStats", bounds)).structured_content

    # Step 1 of the procedure, and what it is for: the figures below are only worth quoting
    # because this said the window holds no gap.
    assert coverage["gaps"] == []
    assert coverage["fully_covered"] is True

    # Step 2, and SOP-05's failure condition: every number the pipeline put in front of a
    # reader is a number this client also obtained, over MCP, for itself.
    prose = str(answer["answer_markdown"])
    assert f"{figures['total']} parts were inspected" in prose
    assert f"of which {figures['rejects']} were rejected" in prose
