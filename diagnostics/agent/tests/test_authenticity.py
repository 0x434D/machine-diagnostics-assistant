"""§1.6 — *the agent says "I have no data for that window" instead of inventing an answer.*

**What this file proves, and what no file in this repository can prove yet.**

The claim §1.6 makes has two halves. The first is mechanical: *a fabricated answer cannot
reach the reader.* That half is provable without a model, and it is what every proof below
does — each one injects the invention a model might make and shows the pipeline refusing it.
The second half is behavioural: *a model, handed thin data, resists inventing.* Nothing here
can say anything about that. The provider these tests run against is keyword matching wearing
the interface of a model (`providers_scripted`), and there is no API key in this environment.
A scripted provider cannot be asked whether a model would resist inventing; it can only be
asked whether the guards hold when something invents on purpose, which is what it is asked.

**Why this is not `tests/test_pipeline.py` again.** Those tests run against `tests/fakes.py`,
which is right for them: they ask what the pipeline does when the data says a particular
thing, and a fake is the shortest way to make the data say it. It is wrong for a §1 proof.
A claim stripped because a *fake* answered `False` to `exists()` is a statement about the
fake — the facade §1 exists to catch looks exactly like that. So every assertion here runs
against the analysis service that actually serves §5.3, over a socket, over the `read.*`
views, as the `analysis` role, against real Postgres carrying the gateway's own migrations.
A-99999999 does not resolve here because Postgres has never heard of it.

The proofs are two-sided wherever a one-sided one would pass against a pipeline that refused
everything: the invention is removed *and* the true claim beside it survives, the empty
window says so *and* the populated one answers. A guard that strips every claim would satisfy
half of each of these and is not what §6.5 asks for.

Marked `authenticity` and run by `make verify`: it starts a Postgres container and serves two
services, which is minutes rather than the seconds `make check` may cost.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest
import pytest_asyncio
import uvicorn
from agent.answer import Answer
from agent.classify import CLASSIFY_TOOL
from agent.config import Settings
from agent.pipeline import run
from agent.provider import ProviderReply
from agent.providers_scripted import ScriptedProvider
from analysis.app import app as analysis_app
from analysis.config import Settings as AnalysisSettings
from analysis.db import reset_pool
from analysis.dependencies import now_dependency, settings_dependency
from psycopg import Connection, sql
from psycopg.conninfo import make_conninfo
from pydantic import ValidationError
from testcontainers.postgres import PostgresContainer

pytestmark = pytest.mark.authenticity

MIGRATIONS = Path(__file__).resolve().parents[2] / "gateway" / "Gateway" / "Migrations"
"""The gateway's embedded SQL, read from the directory rather than listed — the same
reasoning `PostgresWriter.Migrations()` and the analysis fixtures give: a migration added
and not listed here would leave these proofs passing against a schema nothing deploys."""

# Pinned by digest, not tag (§10.7). scripts/pin-images.sh re-resolves it.
POSTGRES_IMAGE = (
    "postgres:17-bookworm@sha256:"
    "051f7b7b3abdd564d5d1bd1e8c4b9c1b6e77087d1dd22020ede611c096a272e0"
)

ANALYSIS_PASSWORD = "analysis-under-proof"
"""005 creates the role able to log in and with no password, so whoever owns the database
hands it one. Compose does that through the gateway; this fixture does it here."""

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
"""`inspection.classifier`'s two populations, far enough apart that the threshold the
service counts at is not what any assertion here turns on."""

PARTS = 12
REJECT_EVERY = 4

QUESTION = "how many rejects in the last hour?"
EMPTY_QUESTION = "how many rejects last week?"
"""One window the seed fills and one it cannot: *last week* is the previous calendar week
(`time_expressions._resolve_last_week`) and every row below sits in the hour before the
service's clock, so the second window is empty by arithmetic rather than by a fixture that
was told to return nothing."""

INVENTED_SERIAL = "A-99999999"

FROZEN_NOW = datetime(2026, 9, 16, 9, 0, tzinfo=UTC)
"""The instant the service's clock reads, and what every seeded row is placed against.

Frozen rather than real, for the reason the analysis fixtures freeze theirs: *last hour* is
a statement about now, and two runs a minute apart resolve two different windows. The gap
proof below compares one answer against another, and a difference the clock caused would
read as a difference the gap caused.
"""


def serial(index: int) -> str:
    """`simulator.identity`'s spelling for an assembly."""
    return f"A-{index:08d}"


def rejected(index: int) -> bool:
    return index % REJECT_EVERY == 0


# --- the stack these proofs answer against ----------------------------------------------


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
def owner_url(postgres_container: PostgresContainer) -> str:
    return str(postgres_container.get_connection_url())


@pytest.fixture
def seeded(owner_url: str) -> Iterator[str]:
    """A dozen parts in the last hour, three of them rejected, and nothing else.

    Deliberately not `analysis/tests/conftest.py`'s fixture, which seeds 600 parts with
    genealogy, press curves and dispositions: that one exists to give the *queries*
    something to be wrong about, and these proofs are about what the agent does with the
    answers. What §1.6 needs is a window that holds something, a window that holds nothing,
    a serial that resolves and a serial that does not.

    Placed inside the hour before `FROZEN_NOW`, which is the clock the service below reads.
    """
    reset_pool()
    with psycopg.connect(owner_url) as conn:
        _truncate(conn)
        _seed(conn, FROZEN_NOW)
        conn.commit()
    yield make_conninfo(owner_url, user="analysis", password=ANALYSIS_PASSWORD)
    reset_pool()


def _truncate(conn: Connection) -> None:
    """Every `ingest` table, read from the catalogue rather than listed.

    Listed, a table added by a later migration would carry one proof's rows into the next
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


def _seed(conn: Connection, now: datetime) -> None:
    for code, name, position in STATIONS:
        conn.execute(
            "INSERT INTO stations (code, name, position_in_line) VALUES (%s, %s, %s)",
            (code, name, position),
        )
    inspection = conn.execute("SELECT id FROM stations WHERE code = 'S3'").fetchone()
    assert inspection is not None

    for index in range(PARTS):
        at = now - timedelta(minutes=30 - index)
        reject = rejected(index)
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


@pytest_asyncio.fixture
async def analysis_service(seeded: str) -> AsyncIterator[str]:
    """§5.3, on an ephemeral loopback port, connected as the role it deploys with.

    A socket rather than an in-process ASGI dispatch: `AnalysisClient` is what the agent
    reaches the service with, and what these proofs must exercise is that client against
    that service — URL building, status codes and all — not a shortcut around both.
    """
    analysis_app.dependency_overrides[settings_dependency] = lambda: AnalysisSettings(
        database_url=seeded
    )
    analysis_app.dependency_overrides[now_dependency] = lambda: FROZEN_NOW
    config = uvicorn.Config(analysis_app, host="127.0.0.1", port=0, log_level="warning")
    running = uvicorn.Server(config)
    bound = config.bind_socket()
    port = int(bound.getsockname()[1])

    serving = asyncio.create_task(running.serve(sockets=[bound]))
    while not running.started:
        await asyncio.sleep(0.01)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        running.should_exit = True
        await serving
        analysis_app.dependency_overrides.clear()


class InventingProvider:
    """A model that invents, so that the guards have something real to refuse.

    §1 asks for a proof that would fail if the link were a facade, and a guard can only be
    shown to hold by handing it the thing it exists to stop. Classification is delegated to
    the scripted provider rather than stubbed, so the answer these proofs inspect came
    through the same eight stages every other answer does and differs in exactly one place:
    what the model said at the end.
    """

    name = "inventing"

    def __init__(self, final: dict[str, object]) -> None:
        self._final = final
        self.finals = 0

    async def call(
        self,
        system: str,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]],
    ) -> ProviderReply:
        if [str(tool.get("name")) for tool in tools] == [CLASSIFY_TOOL["name"]]:
            return await ScriptedProvider().call(system, messages, tools)
        self.finals += 1
        return ProviderReply(final=dict(self._final))


def finding(
    statement: str,
    basis: str,
    citations: list[dict[str, object]],
    evidence_strength: str | None = None,
) -> dict[str, object]:
    stated: dict[str, object] = {
        "statement": statement,
        "basis": basis,
        "citations": citations,
    }
    if evidence_strength is not None:
        stated["evidence_strength"] = evidence_strength
    return stated


async def ask(
    url: str, question: str = QUESTION, provider: InventingProvider | None = None
) -> Answer:
    return await run(
        question,
        "authenticity",
        settings=Settings(analysis_url=url),
        provider=provider or ScriptedProvider(),
    )


# --- the proofs -------------------------------------------------------------------------


async def test_an_empty_window_says_so_rather_than_answering_from_nothing(
    analysis_service: str,
) -> None:
    """§1.6's own sentence, against a window Postgres really holds nothing in.

    Both halves, because either alone is satisfied by a pipeline that is simply broken: the
    empty window produces no finding at all and says where the data it does have ends, and
    the *same question over a window that holds something* produces a `measured` finding.
    A refusal that is not contingent on the data is not a refusal.
    """
    empty = await ask(analysis_service, EMPTY_QUESTION)

    # No `measured` finding, which §1.6 names — and no finding of any basis, which is the
    # stronger statement and the one worth asserting: a `derived` or `hypothesis` claim over
    # a window holding nothing is the same invention wearing a different label.
    assert empty.findings == []
    assert empty.answer_markdown.startswith("I have no data for that window.")
    assert any("Nothing at all was recorded" in caveat for caveat in empty.caveats)
    # Not "the database is empty" — it says where the data it does hold ends, which is the
    # next step §6.7's last row asks for.
    assert any("most recent data" in caveat for caveat in empty.caveats)

    answered = await ask(analysis_service)

    assert [f for f in answered.findings if f.basis == "measured"]
    assert f"{PARTS} parts were inspected" in answered.answer_markdown


async def test_a_hypothesis_with_no_evidence_strength_cannot_ship(
    analysis_service: str,
) -> None:
    """§6.3: `evidence_strength` is required whenever `basis` is `hypothesis`.

    `ALLOW_HYPOTHESIS` is `True` since M4 — routing loads the knowledge an interpretation
    comes from — so the flag no longer stands between a hypothesis and the reader, and this
    constraint is the whole of what does. It is structural: the answer object refuses to
    exist, rather than a prompt asking for the figures and a reader hoping they came.
    """
    unsupported = InventingProvider(
        {
            "findings": [
                finding(
                    "The rejects are carrier wear.",
                    "hypothesis",
                    [{"kind": "part", "id": serial(0)}],
                )
            ]
        }
    )

    with pytest.raises(ValidationError, match="evidence_strength"):
        await ask(analysis_service, provider=unsupported)

    supported = InventingProvider(
        {
            "findings": [
                finding(
                    "The rejects are carrier wear.",
                    "hypothesis",
                    [{"kind": "part", "id": serial(0)}],
                    evidence_strength=f"3 of {PARTS} parts rejected, all misalignment",
                )
            ]
        }
    )

    answer = await ask(analysis_service, provider=supported)

    assert [f.evidence_strength for f in answer.findings] == [
        f"3 of {PARTS} parts rejected, all misalignment"
    ]


async def test_a_citation_postgres_cannot_open_is_stripped_after_one_retry(
    analysis_service: str,
) -> None:
    """§6.5, against the database rather than against a fake that was told to say no.

    `A-99999999` fails here because `GET /parts/A-99999999` is a 404 from a service reading
    a real table, and `A-00000000` survives because the same endpoint opens it. That pairing
    is the proof: a verification step that stripped everything would satisfy the first
    assertion and fail the second, and one that stripped nothing the other way round.

    The retry is counted rather than assumed. §6.5 gives exactly one, and this provider is
    deterministic — it answers the same way twice — so the claim goes on the second pass,
    which is the behaviour a model that would not correct itself produces.
    """
    provider = InventingProvider(
        {
            "findings": [
                finding(
                    "Part A-00000000 was rejected.",
                    "measured",
                    [{"kind": "part", "id": serial(0)}],
                ),
                finding(
                    f"Part {INVENTED_SERIAL} was rejected too.",
                    "measured",
                    [{"kind": "part", "id": INVENTED_SERIAL}],
                ),
            ]
        }
    )

    answer = await ask(analysis_service, provider=provider)

    assert [f.statement for f in answer.findings] == ["Part A-00000000 was rejected."]
    assert any("could not be verified" in caveat for caveat in answer.caveats)
    assert any(f"part:{INVENTED_SERIAL}" in caveat for caveat in answer.caveats)
    # The removal has to be visible in the field a reader actually reads, not only in the
    # findings list a UI renders beside it — so the stripped *sentence* is gone from the
    # prose while the note naming what went is in it. The serial itself is therefore still
    # in the markdown, and must be: a removal nobody can see is the same as no removal.
    assert "was rejected too" not in answer.answer_markdown
    assert f"part:{INVENTED_SERIAL}" in answer.answer_markdown
    assert provider.finals == 2, "the model was not asked a second time"


async def test_a_contradiction_with_no_reasoning_is_refused(
    analysis_service: str,
) -> None:
    """§6.5 and DP-11: *silent disagreement is indistinguishable from an error.*

    A root that differs from the computed one and says nothing about why is a second
    unexplained verdict standing beside the first, and the reader has no way to choose. So
    the field cannot be populated without the reasoning, and the answer does not ship.
    """
    # A finding the seed actually supports, so the only thing under test here is the
    # contradiction: an answer that failed for its findings would prove nothing about it.
    body = finding(
        "Part A-00000000 was rejected.", "measured", [{"kind": "part", "id": serial(0)}]
    )
    silent = InventingProvider(
        {
            "findings": [body],
            "contradiction": {
                "derived_root": "S2",
                "agent_root": "S1",
                "reasoning": "   ",
            },
        }
    )

    with pytest.raises(ValidationError, match="reasoning"):
        await ask(analysis_service, provider=silent)

    argued = InventingProvider(
        {
            "findings": [body],
            "contradiction": {
                "derived_root": "S2",
                "agent_root": "S1",
                "reasoning": "S1 was starved three minutes before S2 was held.",
            },
        }
    )

    answer = await ask(analysis_service, provider=argued)

    assert answer.contradiction is not None
    assert answer.contradiction.agent_root == "S1"


async def test_a_window_with_an_ingest_gap_says_the_count_is_incomplete(
    analysis_service: str, owner_url: str
) -> None:
    """§4.4 and §6.1 step 3: missing data must never read as a quiet machine.

    The count is not corrected and cannot be — nobody knows what the gap holds. What the
    answer must do is carry the gap beside the number, so *12 parts* is never read as
    twelve parts having been made. Asserted in both directions against the same seed, so
    what moved is the gap row and nothing else.
    """
    before = await ask(analysis_service)

    assert not [c for c in before.caveats if "ingest gap" in c]

    with psycopg.connect(owner_url) as conn:
        conn.execute(
            "INSERT INTO ingest_gaps (from_ts, to_ts, reason) VALUES (%s, %s, %s)",
            (
                FROZEN_NOW - timedelta(minutes=20),
                FROZEN_NOW - timedelta(minutes=15),
                "plant_unreachable",
            ),
        )
        conn.commit()

    after = await ask(analysis_service)

    assert any("1 ingest gap(s)" in caveat for caveat in after.caveats)
    assert any("incomplete" in caveat for caveat in after.caveats)
    assert [f.statement for f in after.findings] == [
        f.statement for f in before.findings
    ], "the gap changed what the answer says about itself, not what it counted"
