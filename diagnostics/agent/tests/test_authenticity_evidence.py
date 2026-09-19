"""§7's evidence contract, against services that are running rather than against fakes.

M6's central claim is the one a screenshot can fake: **nothing on screen is drawn from
anything the model typed.** Two of its four halves are provable from this side of the
browser and are proved here; the other two are about what a renderer puts on a screen and
are proved in `diagnostics/ui/src/__tests__/authenticity.test.tsx`, which says so and says
where the seam between them is.

**What is running when these assertions run.** A Postgres container carrying the gateway's
own migrations and the agent's Alembic history, seeded with an hour of parts, a stop, an
alarm, a lot and its components; §5.3's analysis service and §7.2's agent each served by
uvicorn on a loopback socket; and a plain HTTP client that holds no reference to either.
Nothing below the two is a fake.

**Why this is not `tests/test_citations.py` again.** That file asks the same questions of
`tests/fakes.py`, and it is right for it to: what it claims is that `resolves` dispatches
correctly, and a fake is the shortest way to make a referent present or absent. It is wrong
for a §1 proof, because a citation that resolved because a *fake* said so is a statement
about the fake — and "every citation kind resolves" is a claim about ten real endpoints.
The kinds below are enumerated from `contracts/answer.schema.json`, which is the document
the frontend's own union is generated from, so a kind added tomorrow arrives here with no
referent and fails rather than going quietly untested.

**Both sides of every resolution.** A resolver that answered `True` to everything would
satisfy the first proof perfectly, so each kind is also given a referent nothing in the
database holds and must answer `False`. A resolver that answered `False` to everything
fails the first.

**What this file does not establish**, in the same words M4's uses: the provider is
`ScriptedProvider`, because there is no API key in this environment. What a chart citation
proves here is that the pipeline cannot ship one whose figures came from anywhere but a
recorded tool result. Whether a *model* would reach for the right chart, or read the rows
it named, is unproven and unprovable without a key.

Marked `authenticity` and run by `make verify`: it starts a Postgres container and serves
two services, which is minutes rather than the seconds `make check` may cost.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterator, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import httpx
import psycopg
import pytest
import pytest_asyncio
import uvicorn
from agent.answer import ChartOptions, Citation
from agent.app import app as agent_app
from agent.citations import resolves
from agent.records import ToolCallRecord
from agent.tools import AnalysisClient, Window
from analysis.app import app as analysis_app
from analysis.config import Settings as AnalysisSettings
from analysis.db import reset_pool
from analysis.dependencies import now_dependency, settings_dependency
from auth.testing import mint
from knowledge.documents import DEFAULT_ROOT, load
from psycopg import Connection, sql
from psycopg.conninfo import make_conninfo

pytestmark = pytest.mark.authenticity

CONTRACTS = Path(__file__).resolve().parents[3] / "contracts"
ANSWER_SCHEMA = CONTRACTS / "answer.schema.json"
"""§7.3's vocabulary as the frontend receives it.

Read from the generated contract rather than from `agent.answer.Kind`, deliberately: the
union the browser compiles against is generated from this file, so this is the document
both sides agree on. A kind added to the Python enum and not regenerated here is a kind the
frontend cannot render, and `test_contract.py` in the gate is what fails for that.
"""

ANALYSIS_PASSWORD = "analysis-under-evidence-proof"
"""005 creates the role able to log in and with no password, so whoever owns the database
hands it one. Compose does that through the gateway; this fixture does it here."""

FROZEN_NOW = datetime(2026, 9, 16, 9, 0, tzinfo=UTC)
"""The instant the analysis service's clock reads.

Frozen rather than real, for the reason M4's proof freezes its own: *last hour* is a
statement about now, and a window that moved between the fixture and the assertion would
make every referent discovered below a different referent from the one checked.
"""

WINDOW_START = FROZEN_NOW - timedelta(hours=1)

STATIONS = (
    ("S1", "Feeding", 1),
    ("S2", "Joining", 2),
    ("S3", "Inspection", 3),
    ("S4", "Outfeed", 4),
)
BUFFERS = (("B1_2", "S1", "S2"), ("B2_3", "S2", "S3"), ("B3_4", "S3", "S4"))
BUFFER_CAPACITY = 10

CARRIERS = (3, 7)
PARTS = 120
"""Sixty per carrier, which is what puts both of them past `/inspection/patterns`' own
sample gate. A `pattern` citation's referent is a cell of that report, and a dimension the
service declined to test for want of trials holds no cells at all — so this number is what
makes the `pattern` kind resolvable rather than a fixture convenience."""

TAKT = timedelta(seconds=30)
REJECT_EVERY = 5

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

LOT_CODE = "L-4471"
SUPPLIER = "Nordwerk"

STOP_FROM = WINDOW_START + timedelta(minutes=20)
STOP_TO = WINDOW_START + timedelta(minutes=25)
OUTFEED_DELAY = timedelta(seconds=5)
"""How long after its inspection a part leaves S4. Only its existence matters here — what
the stop is derived from is the absence of these instants, not their spacing."""
ALARM_CODE = "A-207"

QUESTION = "how many rejects in the last hour?"
"""A statistics question, which is what routes the scripted provider to `inspection_stats`
and therefore to the one chart citation a keyless run produces (`providers_scripted`)."""

TIMEOUT = 120.0
"""Generous: `POST /ask` under a token runs the whole staged pipeline."""

NOTHING_HOLDS_IT: Mapping[str, dict[str, object]] = {
    "part": {"id": "A-99999999"},
    "stop": {"id": "stop-19990101T000000.000000Z"},
    "alarm": {"id": "99999999"},
    "signal": {"station": "S99", "signal": "JoiningForcePeak"},
    "pattern": {"dimension": "carrier", "key": "999"},
    "sop": {"id": "SOP-99"},
    "serial": {"id": "C-99999999"},
    "lot": {"id": "L-NOSUCH"},
    "containment": {"serials": ["A-99999999"]},
    "chart": {
        "chart_type": "pareto",
        "source": "call_that_was_never_made",
        "options": {"series": "by_defect_class", "x": "defect_class", "y": "count"},
    },
}
"""One referent per kind that the seeded database does not hold.

Keyed by kind and checked against the contract's own enumeration below, so this table
cannot fall behind the vocabulary either: a kind with no entry fails rather than being
skipped. The `sop` row is a document id the knowledge tree does not define *and* is passed
as routed, so what refuses it is the document's absence rather than the routing check —
which is the half `test_citations.py` keeps separate in the gate.
"""


def serial(index: int) -> str:
    """`simulator.identity`'s spelling for an assembly."""
    return f"A-{index:08d}"


def component(index: int) -> str:
    return f"C-{index:06d}"


def declared_kinds() -> list[str]:
    """§7.3's vocabulary, out of the contract the frontend is generated from."""
    schema = json.loads(ANSWER_SCHEMA.read_text())
    kinds = schema["$defs"]["Citation"]["properties"]["kind"]["enum"]
    assert kinds, "the answer contract declares no citation kinds"
    return [str(kind) for kind in kinds]


# --- the stack these proofs answer against ----------------------------------------------


@pytest.fixture(scope="module")
def seeded(owner_url: str) -> Iterator[str]:
    """An hour of history, and the analysis role's own connection string to read it with.

    `owner_url` is `tests/database.py`'s: a container with the gateway's embedded migrations
    applied. What is added here is a password for the `analysis` role — 005 creates it able
    to log in and with no password — and the rows the ten kinds below need referents in.
    """
    reset_pool()
    with psycopg.connect(owner_url) as conn:
        conn.execute(
            sql.SQL("ALTER ROLE analysis PASSWORD {}").format(
                sql.Literal(ANALYSIS_PASSWORD)
            )
        )
        _truncate(conn)
        _seed(conn)
        conn.commit()
    yield make_conninfo(owner_url, user="analysis", password=ANALYSIS_PASSWORD)
    reset_pool()


def _truncate(conn: Connection) -> None:
    """Every `ingest` table, read from the catalogue rather than listed.

    Listed, a table a later migration adds would carry another module's rows into this one
    and the failure would land on whichever ran second.
    """
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
    stations = _station_ids(conn)
    # The line's shape. `/parts/affected` finds the head and the tail of the line here and
    # the propagation walk discovers the topology from these rows, so a database with
    # stations and no buffers is not a state the plant can be in.
    for code, upstream, downstream in BUFFERS:
        conn.execute(
            "INSERT INTO buffers (code, upstream_station_id, downstream_station_id,"
            " capacity) VALUES (%s, %s, %s, %s)",
            (code, stations[upstream], stations[downstream], BUFFER_CAPACITY),
        )
    for carrier in CARRIERS:
        conn.execute("INSERT INTO carriers (id) VALUES (%s)", (carrier,))

    conn.execute(
        "INSERT INTO component_lots (lot_code, lane, supplier, loaded_at)"
        " VALUES (%s, %s, %s, %s)",
        (LOT_CODE, 1, SUPPLIER, WINDOW_START - timedelta(hours=1)),
    )
    lot = conn.execute(
        "SELECT id FROM component_lots WHERE lot_code = %s", (LOT_CODE,)
    ).fetchone()
    assert lot is not None

    inspection = stations["S3"]
    for index in range(PARTS):
        at = WINDOW_START + index * TAKT
        carrier = CARRIERS[index % len(CARRIERS)]
        reject = index % REJECT_EVERY == 0
        conn.execute(
            "INSERT INTO assemblies (serial, created_at, carrier_id) VALUES (%s, %s, %s)",
            (serial(index), at, carrier),
        )
        conn.execute(
            "INSERT INTO components (serial, lot_id, lane, read_at) VALUES (%s, %s, %s, %s)",
            (component(index), lot[0], 1, at),
        )
        conn.execute(
            "INSERT INTO genealogy (assembly_serial, component_serial, position)"
            " VALUES (%s, %s, 0)",
            (serial(index), component(index)),
        )
        if not (STOP_FROM <= at + OUTFEED_DELAY < STOP_TO):
            # A part leaving S4. §5.4 defines a stop as the *absence* of these, so the
            # five minutes left out here are the stop — seeded by taking the output away,
            # which is the only faithful way to seed one.
            conn.execute(
                "INSERT INTO part_dispositions (assembly_serial, at, disposition, reason)"
                " VALUES (%s, %s, %s, %s)",
                (
                    serial(index),
                    at + OUTFEED_DELAY,
                    "reject" if reject else "good",
                    "misalignment" if reject else None,
                ),
            )
        scores = [BASELINE_SCORE] * len(DEFECT_CLASSES)
        if reject:
            scores[DEFECT_CLASSES.index("misalignment")] = BOOSTED_SCORE
        conn.execute(
            "INSERT INTO inspection_results (assembly_serial, source_ts, station_id,"
            " result, confidence, model_version, defect_classes, confidences)"
            " VALUES (%s, %s, %s, %s, 0.91, 'sim-1', %s, %s)",
            (
                serial(index),
                at,
                inspection,
                "reject" if reject else "good",
                list(DEFECT_CLASSES),
                scores,
            ),
        )

    _seed_the_stop(conn, stations)
    conn.execute(
        "INSERT INTO alarms (station_id, code, text, severity, raised_at, acked_at)"
        " VALUES (%s, %s, %s, %s, %s, %s)",
        (
            stations["S2"],
            ALARM_CODE,
            "joining force out of tolerance",
            700,
            STOP_FROM - timedelta(minutes=1),
            STOP_FROM,
        ),
    )


def _seed_the_stop(conn: Connection, stations: dict[str, int]) -> None:
    """One station aborting and coming back, which is what makes a `stop` citation have a
    referent at all.

    Deliberately the smallest stop the derivation will report rather than §5.4's whole
    chain: what is under test here is that the id a stop citation carries opens, and
    `analysis/tests/test_stops.py` is where the chain itself is asserted.
    """
    rows: tuple[tuple[str, datetime, str | None, str], ...] = (
        ("S1", WINDOW_START, None, "Execute"),
        ("S2", WINDOW_START, None, "Execute"),
        ("S2", STOP_FROM, "Execute", "Aborted"),
        ("S2", STOP_TO, "Aborted", "Execute"),
        ("S3", WINDOW_START, None, "Execute"),
        ("S4", WINDOW_START, None, "Execute"),
    )
    for station, at, from_state, to_state in rows:
        conn.execute(
            "INSERT INTO state_changes (station_id, source_ts, from_state, to_state)"
            " VALUES (%s, %s, %s, %s)",
            (stations[station], at, from_state, to_state),
        )


def _station_ids(conn: Connection) -> dict[str, int]:
    return {
        str(code): int(identifier)
        for code, identifier in conn.execute("SELECT code, id FROM stations").fetchall()
    }


async def _serve(app: object) -> tuple[str, uvicorn.Server, asyncio.Task[None]]:
    """One ASGI application on an ephemeral loopback port.

    A socket rather than an in-process ASGI dispatch: what these proofs exercise is the
    client against the service — URL building, status codes and all — and not a shortcut
    around both. Ephemeral, because §10.3's point is that the port is configuration and not
    that this particular one is free on the machine running the proof.
    """
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")  # type: ignore[arg-type]  # uvicorn types its app parameter as its own ASGIApplication alias
    running = uvicorn.Server(config)
    bound = config.bind_socket()
    port = int(bound.getsockname()[1])
    serving = asyncio.create_task(running.serve(sockets=[bound]))
    while not running.started:
        await asyncio.sleep(0.01)
    return f"http://127.0.0.1:{port}", running, serving


@pytest_asyncio.fixture
async def analysis_service(seeded: str) -> AsyncIterator[str]:
    """§5.3, on a loopback socket, connected as the role it deploys with."""
    analysis_app.dependency_overrides[settings_dependency] = lambda: AnalysisSettings(
        database_url=seeded
    )
    analysis_app.dependency_overrides[now_dependency] = lambda: FROZEN_NOW
    url, running, serving = await _serve(analysis_app)
    try:
        yield url
    finally:
        running.should_exit = True
        await serving
        analysis_app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def agent_service(
    analysis_service: str, agent_url: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[str]:
    """§7.2's agent, on a second socket, reaching the analysis service over the first.

    Configured through the environment rather than through overrides, because that is where
    a deployment states it: `agent.config.Settings` is read per request, so these two
    variables are the whole of what the service needs to be the one the browser talks to.
    """
    monkeypatch.setenv("AGENT_ANALYSIS_URL", analysis_service)
    monkeypatch.setenv("AGENT_DATABASE_URL", agent_url)
    url, running, serving = await _serve(agent_app)
    try:
        yield url
    finally:
        running.should_exit = True
        await serving


@pytest_asyncio.fixture
async def reader(analysis_service: str) -> AsyncIterator[AnalysisClient]:
    """The client the pipeline itself verifies citations with, carrying a `user` token."""
    async with httpx.AsyncClient(timeout=30) as client:
        yield AnalysisClient(analysis_service, client=client, token=mint(role="user"))


@pytest_asyncio.fixture
async def window(reader: AnalysisClient) -> Window:
    """The hour the seed sits in, as the *service* resolves it.

    Asked of `/time/resolve` rather than constructed here: §6.1 step 2 is what gives a run
    its window, and a window this file computed for itself would be a different interval
    from the one an answer is checked over.
    """
    resolved = await reader.resolve_time("last hour")
    assert resolved is not None, "the shift calendar no longer understands 'last hour'"
    return resolved


# --- the referents, discovered from the service rather than chosen here ------------------


async def _real_referents(
    reader: AnalysisClient, window: Window
) -> dict[str, dict[str, object]]:
    """One citation payload per kind, every one of them read back out of the service.

    Asked for rather than written down, for the reason M2b's traceability proof resolves a
    serial the database hands it: a referent chosen here would be a statement about the
    seed, and what these proofs are about is that the endpoint a citation names answers for
    the thing the answer cited. A kind whose referent the running service cannot produce
    fails at this step, which is the correct failure — it is a kind nothing could cite.
    """
    affected = await reader.fetch("affected_parts", {}, window)
    serials = _serials(affected)
    assert serials, "the seeded window holds no parts"

    part = await reader.fetch("get_part", {"serial": serials[0]}, window)
    genealogy = cast(list[dict[str, object]], part["genealogy"])
    assert genealogy, f"{serials[0]} has no genealogy to take a component serial from"
    built_from = genealogy[0]

    stops = cast(
        list[dict[str, object]], (await reader.fetch("list_stops", {}, window))["stops"]
    )
    assert stops, "the seeded window holds no stop"

    alarms = cast(
        list[dict[str, object]],
        (await reader.fetch("list_alarms", {}, window))["alarms"],
    )
    assert alarms, "the seeded window holds no alarm"

    status = await reader.fetch("line_status", {}, window)
    stations = cast(list[dict[str, object]], status["stations"])
    assert stations, "the line reports no stations"

    cell = _first_pattern_cell(await reader.fetch("inspection_patterns", {}, window))

    return {
        "part": {"id": serials[0]},
        "stop": {"id": str(stops[0]["id"])},
        "alarm": {"id": str(alarms[0]["id"])},
        "signal": {
            "station": str(stations[0]["station"]),
            "signal": "JoiningForcePeak",
        },
        "pattern": cell,
        # The knowledge tree's first document by id, which is the tree the analysis
        # service serves `GET /knowledge/{id}` from. Lowest rather than arbitrary, so two
        # runs cite the same procedure.
        "sop": {"id": min(load(DEFAULT_ROOT).by_id)},
        "serial": {"id": str(built_from["component_serial"])},
        "lot": {"id": str(built_from["lot_code"])},
        "containment": {"serials": serials[:3]},
        "chart": {
            "chart_type": "pareto",
            "source": "call_inspection_stats",
            "options": {"series": "by_defect_class", "x": "defect_class", "y": "count"},
        },
    }


def _serials(affected: Mapping[str, object]) -> list[str]:
    """Every serial `/parts/affected` returned, whichever outcome group it landed in.

    All three groups rather than one: §5.3 splits the set into rejected, shipped and still
    on the line because a containment answer that collapses them is useless, and a proof
    that read only one of them would go silent the day the seed's parts were dispositioned.
    """
    parts = cast(dict[str, object], affected["parts"])
    found: list[str] = []
    for outcome in ("rejected", "shipped", "on_the_line"):
        group = cast(dict[str, object], parts[outcome])
        found.extend(str(entry) for entry in cast(list[object], group["serials"]))
    return found


def _first_pattern_cell(report: Mapping[str, object]) -> dict[str, object]:
    for section in cast(list[dict[str, object]], report["dimensions"]):
        cells = cast(list[dict[str, object]], section.get("patterns") or [])
        if cells:
            return {
                "dimension": str(section["dimension"]),
                "key": str(cells[0]["value"]),
            }
    raise AssertionError(
        "/inspection/patterns tested no dimension to a cell, so no pattern citation could "
        "have a referent"
    )


async def _calls_this_run_made(
    reader: AnalysisClient, window: Window
) -> dict[str, ToolCallRecord]:
    """The tool call a chart citation is checked against, made against the running service.

    §7.4's referent is not a row but a call, so the only honest way to give a chart citation
    something to resolve to is to make the call — and the id is `providers_scripted`' own,
    which is what the shipped provider cites.
    """
    result = await reader.fetch("inspection_stats", {}, window)
    return {
        "call_inspection_stats": ToolCallRecord(
            id="call_inspection_stats",
            name="inspection_stats",
            arguments={},
            result=dict(result),
            duration_ms=1.0,
            failed=False,
        )
    }


# --- proof: every kind in the contract resolves against the running service --------------


@pytest.mark.parametrize("kind", declared_kinds())
async def test_every_citation_kind_resolves_against_the_running_service(
    kind: str, reader: AnalysisClient, window: Window
) -> None:
    """§7.3: *a citation you cannot open is barely a citation* — over all ten kinds.

    The enumeration is the point. A list of kinds written here would pass on the day it was
    written and go on passing while the eleventh kind, added six months from now with no
    resolver and no renderer, shipped citations nobody could open. It comes out of
    `contracts/answer.schema.json`, which is the same document the frontend's union is
    generated from.
    """
    referents = await _real_referents(reader, window)
    payload = referents.get(kind)
    assert payload is not None, (
        f"the contract declares the kind {kind!r} and this proof has no referent for it: "
        f"a kind was added to the vocabulary without anything that can open one"
    )

    citation = Citation(kind=kind, **payload)  # type: ignore[arg-type]  # the payload is the kind's own, checked by the validator

    assert await resolves(
        citation,
        reader,
        window=window,
        loaded=frozenset({str(referents["sop"]["id"])}),
        calls=await _calls_this_run_made(reader, window),
    ), f"a {kind} citation naming something the database holds did not resolve"


@pytest.mark.parametrize("kind", declared_kinds())
async def test_a_citation_of_every_kind_that_names_nothing_is_refused(
    kind: str, reader: AnalysisClient, window: Window
) -> None:
    """The other side, without which the proof above is satisfied by a resolver that says
    yes to everything.

    Each referent here is one the seeded database does not hold, and every one of them has
    to come back `False` from the same service, over the same window, through the same
    client. §6.5 removes the claim that rests on it, and the answer says that it did.
    """
    payload = NOTHING_HOLDS_IT.get(kind)
    assert payload is not None, (
        f"the contract declares the kind {kind!r} and this proof has no invented referent "
        f"for it, so nothing checks that it can fail"
    )

    citation = Citation(kind=kind, **payload)  # type: ignore[arg-type]  # the payload is the kind's own, checked by the validator

    assert not await resolves(
        citation,
        reader,
        window=window,
        # The invented SOP is passed as routed, so what refuses it is the document not
        # existing rather than the routing half — which is a separate failure and has its
        # own test in the gate.
        loaded=frozenset({str(NOTHING_HOLDS_IT["sop"]["id"])}),
        calls=await _calls_this_run_made(reader, window),
    ), f"a {kind} citation naming nothing the database holds resolved anyway"


# --- proof: nothing on a chart originates outside a recorded tool result -----------------


async def test_a_chart_reaches_the_reader_as_a_reference_to_a_call_the_run_made(
    agent_service: str,
) -> None:
    """§7.4, along the whole path a browser takes: `POST /ask` in, §7.2's trace out.

    The chain a chart is drawn through has four links and every one of them is checked
    here against the running pair of services rather than against a fixture: the answer
    carries a `chart` citation; the citation carries **no figure of any kind**; its `source`
    names a tool call the trace endpoint holds; that call did not fail; and the rows at the
    path the citation names are in that call's *stored result*. What the renderer then does
    with them is the frontend's own proof, and the document the two meet over is this one.

    Everything here runs through `ScriptedProvider`, which is keyword matching wearing the
    interface of a model. That is the only thing in this environment that can produce an
    answer at all, and it is why this proves the mechanism and not the judgement.
    """
    token = mint(role="user")
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        answered = await client.post(
            f"{agent_service}/ask",
            json={"question": QUESTION},
            headers={"Authorization": f"Bearer {token}"},
        )
        answered.raise_for_status()
        events = _sse(answered.text)
        exchange = json.loads(events["session"])
        answer = json.loads(events["answer"])

        recorded = await client.get(
            f"{agent_service}/sessions/{exchange['session_id']}"
            f"/messages/{exchange['seq']}/trace",
            headers={"Authorization": f"Bearer {token}"},
        )
    recorded.raise_for_status()
    trace = recorded.json()

    charts = [
        citation
        for finding in cast(list[dict[str, object]], answer["findings"])
        for citation in cast(list[dict[str, object]], finding["citations"])
        if citation["kind"] == "chart"
    ]
    assert charts, (
        "the answer carries no chart citation, so this proof measured nothing — §7.4's "
        "renderer would ship exercised only by its own tests"
    )

    calls = {
        str(call["id"]): call
        for call in cast(list[dict[str, object]], trace["tool_calls"])
    }
    for chart in charts:
        # Nothing the model typed is a figure: every option on a chart citation is a name,
        # and the whole citation is checked rather than the fields this test remembered.
        assert _figures_in(chart) == [], (
            f"a chart citation reached the reader carrying figures at {_figures_in(chart)}"
        )

        source = str(chart["source"])
        assert source in calls, (
            f"the chart references the tool call {source}, and the trace this reader can "
            f"open holds {sorted(calls)}"
        )
        call = calls[source]
        assert call["failed"] is False, "a chart was drawn from a call that failed"

        options = cast(dict[str, object], chart["options"])
        rows = _rows_at(cast(dict[str, object], call["result"]), str(options["series"]))
        assert rows, (
            f"the stored result of {source} holds nothing at {options['series']}, so "
            f"the chart would draw an empty axis"
        )
        # The fields the chart reads are fields of those rows, so what is drawn is a column
        # of a measurement rather than a name nothing answers to.
        for channel in ("x", "y"):
            named = options.get(channel)
            if named is not None:
                assert any(str(named) in row for row in rows), (
                    f"the chart reads {named} out of rows that carry {sorted(rows[0])}"
                )


async def test_a_chart_naming_a_call_the_run_never_made_takes_its_claim_with_it(
    reader: AnalysisClient, window: Window
) -> None:
    """The other side of the proof above, against the same running service.

    Without it, "the chart's source was in the trace" is satisfied by a verification step
    that never looked. The pairing is what has teeth: the same citation resolves when the
    call is in the run's own record and does not when it is not, and §6.5 is what removes
    the claim that rested on it.
    """
    made = await _calls_this_run_made(reader, window)
    drawn = Citation(
        kind="chart",
        chart_type="pareto",
        source="call_inspection_stats",
        options=ChartOptions(series="by_defect_class", x="defect_class", y="count"),
    )
    invented = drawn.model_copy(update={"source": "call_that_was_never_made"})
    failed = {
        "call_inspection_stats": made["call_inspection_stats"].model_copy(
            update={"failed": True}
        )
    }

    assert await resolves(drawn, reader, window=window, loaded=frozenset(), calls=made)
    assert not await resolves(
        invented, reader, window=window, loaded=frozenset(), calls=made
    )
    # A call that ran and errored is §6.8's error object, and a chart of it would be a
    # chart of the words "connection refused" drawn on real axes.
    assert not await resolves(
        drawn, reader, window=window, loaded=frozenset(), calls=failed
    )


def _sse(body: str) -> dict[str, str]:
    """The named events of a server-sent-event body, last one of each name winning."""
    events: dict[str, str] = {}
    for block in body.strip().split("\n\n"):
        name = data = ""
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line.removeprefix("event:").strip()
            elif line.startswith("data:"):
                data = line.removeprefix("data:").strip()
        if name:
            events[name] = data
    return events


def _figures_in(value: object, path: str = "") -> list[str]:
    """Every place a number appears anywhere inside a citation, by path.

    Whole-object rather than field-by-field, so a figure that arrived through a field this
    test did not think of is still found. The structural guarantee — that every option is
    typed as a name — is `test_answer.py`'s and runs in the gate; this is the same claim
    read off what actually reached a reader.
    """
    if isinstance(value, bool) or value is None:
        return []
    if isinstance(value, int | float):
        return [path or "."]
    if isinstance(value, dict):
        return [
            found
            for key, nested in cast(dict[str, object], value).items()
            for found in _figures_in(nested, f"{path}.{key}" if path else str(key))
        ]
    if isinstance(value, list):
        return [
            found
            for index, item in enumerate(cast(list[object], value))
            for found in _figures_in(item, f"{path}[{index}]")
        ]
    return []


def _rows_at(result: Mapping[str, object], path: str) -> list[dict[str, object]]:
    """The rows a chart names, out of the stored tool result — `charts/rows.ts`' own walk."""
    current: object = result
    for segment in path.split("."):
        if isinstance(current, list):
            current = current[int(segment)] if segment.isdigit() else None
            continue
        if not isinstance(current, dict):
            return []
        current = cast(dict[str, object], current).get(segment)
    if not isinstance(current, list):
        return []
    return [row for row in cast(list[object], current) if isinstance(row, dict)]
