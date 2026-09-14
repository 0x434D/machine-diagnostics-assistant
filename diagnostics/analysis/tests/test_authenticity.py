"""The authenticity proofs §1 asks for: each link tested so it would fail if it were a facade.

Four of §1's nine are reachable in M1. These stop and start real containers and take minutes,
so they are marked `authenticity` and run by `make verify`, never by `make check` — the same
mechanism the plant stack already uses for its own proofs.

The remaining five need later milestones and are named at the bottom of this file so the gap
is explicit rather than implied by absence.
"""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest

REPO = Path(__file__).resolve().parents[3]
PLANT_CONTAINER = "machine-agent-plant-line-simulator-1"
GATEWAY_IMAGE = "machine-agent/edge-gateway:dev"
POSTGRES_IMAGE = (
    "postgres:17-bookworm@sha256:"
    "051f7b7b3abdd564d5d1bd1e8c4b9c1b6e77087d1dd22020ede611c096a272e0"
)
SIGNAL_POLICY = REPO / "diagnostics" / "gateway" / "Gateway" / "config" / "signals.json"
"""The same file diagnostics/compose.yml mounts. Named here rather than spelled inline
because the gateway refuses to start without it, and the two mounts must stay the same
file: a proof run against a different signal policy is a proof about a different gateway."""

STATUS = "http://localhost:18082/status"
DSN = "postgresql://postgres:auth@localhost:15434/postgres"

pytestmark = pytest.mark.authenticity


def _sh(*args: str) -> str:
    return subprocess.run(
        args, capture_output=True, text=True, check=False
    ).stdout.strip()


def _container_logs(name: str, lines: int = 20) -> str:
    """A container's last words, both streams.

    Not `_sh("docker", "logs", ...)`: `docker logs` reproduces the container's own
    stdout and stderr on the corresponding stream, and a .NET process that dies of an
    unhandled exception writes every word of it to stderr. `_sh` keeps stdout only, so
    the version of this that used it rendered the crash that had broken these four
    proofs as a blank line -- reporting the symptom, which is the thing it was added to
    stop doing. Merged here rather than in `_sh`, because `_status` parses `_sh`'s
    output as JSON and must not be handed a stray diagnostic.
    """
    # stdout=PIPE with stderr=STDOUT, not capture_output=True: the two cannot be
    # combined -- subprocess raises "stdout and stderr arguments may not be used with
    # capture_output" -- and it is the merge that is the point here.
    completed = subprocess.run(
        ["docker", "logs", "--tail", str(lines), name],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    return completed.stdout.strip() or "(nothing on either stream)"


def _status() -> dict[str, object]:
    raw = _sh("curl", "-s", "--max-time", "5", STATUS)
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _wait_for(predicate_key: str, value: object, timeout_s: int) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if _status().get(predicate_key) == value:
            return True
        time.sleep(2)
    return False


def _plant_setting(name: str) -> str:
    """One value of the plant's own `Settings`, read from the plant that is running.

    Every number in this project is configuration, so a literal restated here would make
    the weekly job red on a plant that had merely been retuned — red for something nobody
    did, which is how a scheduled job becomes one nobody reads. That is the failure M2a
    spent a task removing from these same four proofs, and it is not worth reintroducing
    for the sake of two short strings.

    `exec` into the running container rather than `run` on the image: the image carries
    the defaults and the container carries what this plant was actually started with.

    :raises AssertionError: the plant answered with nothing, so there is no setting to
        state the assertions in.
    """
    value = _sh(
        "docker",
        "exec",
        PLANT_CONTAINER,
        "python",
        "-c",
        f"from simulator.config import Settings; print(Settings().{name})",
    )
    if not value:
        raise AssertionError(
            f"the plant did not answer for Settings().{name}; it said:\n"
            + _container_logs(PLANT_CONTAINER)
        )
    return value


def _count(table: str) -> int:
    with psycopg.connect(DSN) as conn:
        row = conn.execute(f"SELECT count(*) FROM {table}").fetchone()
    return int(row[0]) if row else 0


@pytest.fixture(scope="module")
def stack() -> Iterator[None]:
    """A gateway and a Postgres of our own, against the plant that is already running."""
    subprocess.run(
        [
            "docker",
            "build",
            "-q",
            "-f",
            "diagnostics/gateway/Dockerfile",
            "-t",
            GATEWAY_IMAGE,
            ".",
        ],
        cwd=REPO,
        check=True,
        capture_output=True,
    )
    _sh("docker", "rm", "-f", "auth-pg", "auth-gw")
    queue = REPO / ".authenticity-queue"
    subprocess.run(["rm", "-rf", str(queue)], check=False)
    queue.mkdir(parents=True, exist_ok=True)

    # No --rm: proof 2 stops this container and starts it again, and --rm would remove it on
    # stop so the restart would have nothing to start. Removed explicitly in teardown instead.
    _sh(
        "docker",
        "run",
        "-d",
        "--name",
        "auth-pg",
        "-e",
        "POSTGRES_PASSWORD=auth",
        "-p",
        "15434:5432",
        POSTGRES_IMAGE,
    )
    time.sleep(8)
    _sh(
        "docker",
        "run",
        "-d",
        "--name",
        "auth-gw",
        "--network",
        "field-net",
        "--add-host=host.docker.internal:host-gateway",
        "-v",
        f"{REPO / 'pki'}:/pki:ro",
        # §5.1's deadbands, mounted exactly as diagnostics/compose.yml mounts them. M2a
        # Task 9 made the policy mandatory -- SignalPolicy.Load raises rather than
        # defaulting, because a gateway that silently subscribes to everything at no
        # deadband is a different gateway from the configured one -- and this fixture was
        # not updated. Every one of §1's four proofs has been erroring at setup since, with
        # "gateway never reached live" standing in for a container that had exited 139 with
        # FileNotFoundException on its first line. `make verify` is the only thing that runs
        # them, and it is not in `make check`, so nothing was red.
        "-v",
        f"{SIGNAL_POLICY}:/config/signals.json:ro",
        "-v",
        f"{queue}:/queue",
        "--user",
        f"{queue.stat().st_uid}:{queue.stat().st_gid}",
        "-p",
        "18082:8080",
        "-e",
        "GATEWAY_HISTORY_DEPTH_HOURS=2",
        "-e",
        "GATEWAY_POSTGRES=Host=host.docker.internal;Port=15434;Username=postgres;"
        "Password=auth;Database=postgres",
        GATEWAY_IMAGE,
    )
    if not _wait_for("state", "live", timeout_s=300):
        # The container's own last words, not just "it never got there". The failure above
        # was a startup crash, and a fixture that reports only the symptom costs an hour
        # finding that out -- the logs are gone as soon as teardown removes the container.
        raise AssertionError(
            "gateway never reached live; auth-gw said:\n" + _container_logs("auth-gw")
        )
    yield
    _sh("docker", "rm", "-f", "auth-gw", "auth-pg")
    _sh("docker", "start", PLANT_CONTAINER)


@pytest.mark.usefixtures("stack")
def test_a_foreign_client_can_browse_the_address_space() -> None:
    """§1 row 1. The boundary is a real OPC UA server, not gateway-specific glue — a client
    that is not our gateway can browse it and find the station's signals."""
    probe = _sh(
        "docker",
        "run",
        "--rm",
        "--network",
        "field-net",
        "--user",
        f"{(REPO / 'pki').stat().st_uid}:{(REPO / 'pki').stat().st_gid}",
        "-v",
        f"{REPO / 'pki'}:/pki:ro",
        "-v",
        f"{REPO / 'measurements'}:/measurements:ro",
        "machine-agent-plant-line-simulator",
        "python",
        "/measurements/run_r3.py",
        "--probe",
        "opc.tcp://line-simulator:4840/plant",
        "--pki",
        "/pki",
        "--secure",
    )
    assert "Good" in probe, probe


@pytest.mark.usefixtures("stack")
def test_the_gateway_buffers_while_postgres_is_down_and_loses_nothing() -> None:
    """§1 row 2. Stop Postgres, watch the local queue fill, start it, watch the queue drain —
    with no row lost and no gap recorded. This is what the local queue is for (§5.1)."""
    before = _count("raw_events")

    _sh("docker", "stop", "auth-pg")
    time.sleep(60)  # ten takts
    depth = _status().get("queueDepth")
    assert isinstance(depth, int) and depth > 0, f"the queue is not filling: {depth}"

    _sh("docker", "start", "auth-pg")
    assert _wait_for("queueDepth", 0, timeout_s=240), "the queue never drained"

    assert _count("raw_events") > before, "nothing was written after Postgres came back"
    assert _count("ingest_gaps") == 0, (
        "an outage the queue absorbed was recorded as a gap"
    )


@pytest.mark.usefixtures("stack")
def test_history_read_closes_an_upstream_outage() -> None:
    """§1 row 3. Stop the plant, let it be away, start it — and the gap closes by HistoryRead
    rather than staying a hole. §4.3's one mechanism, three situations.

    This failed for four measured runs and was kept failing rather than weakened, which is
    the only reason it was still here to be fixed. Everything visible pointed away from the
    cause: the reconnect worked, the state machine moved disconnected -> waiting_for_history
    -> backfilling in order, live data resumed, and the gap-closing backfill reported success
    having found nothing to close. It found nothing because HistoryBackfill was handed an
    ISession at construction and kept it, while a reconnect against a restarted plant must
    build a new session and dispose the old — so it was reading a disposed object, which
    returns nothing and raises nothing (979bc49).

    Note the timings. The plant rebuilds 33 h of history at ~750x on restart, so the gateway
    correctly sits in waiting_for_history for ~160 s before it may read any of it; the 420 s
    allowed here is that plus backfill plus margin, not padding.
    """
    _sh("docker", "stop", PLANT_CONTAINER)
    time.sleep(90)
    _sh("docker", "start", PLANT_CONTAINER)

    assert _wait_for("state", "live", timeout_s=420), (
        "the gateway never came back to live"
    )
    # The window the plant was away for is closed from history, not left empty.
    with psycopg.connect(DSN) as conn:
        row = conn.execute(
            "SELECT count(*) FROM inspection_results "
            "WHERE source_ts > now() - interval '10 minutes'"
        ).fetchone()
    assert row is not None and row[0] > 0, "no parts recorded across the outage window"


@pytest.mark.usefixtures("stack")
def test_the_diagnostics_stack_answers_with_the_plant_shut_down() -> None:
    """§1 row 4, and §2.1's hard requirement. The proof that decides whether the two-stack
    split is real or decorative: with the plant gone, history still answers.

    M2b's identity chain is asserted here rather than in a test of its own because this is
    the only thing in the repository that runs a real gateway against a real plant, and it
    is therefore the only place the two stacks' independent statements of §4.1 are held
    against each other at all. `make check` cannot: it has no plant.

    **What the lot assertion catches is a value under the wrong key, not a field order.**
    Both ends address by name -- the gateway builds its SelectClauses from the same list it
    decodes positionally against, and asyncua resolves each clause with `getattr` -- so
    reordering either file alone re-orders request and decode together and mis-assigns
    nothing. What is silent is `s1_feeding.py` publishing `lot.supplier` under `"LotCode"`,
    or the writer reading the wrong payload key: both are Strings, so nothing decodes
    wrong, nothing raises, and `/reconcile` agrees, because it compares the event stream
    against `raw_events` -- written verbatim before any of this is derived. Every component
    would then record its supplier as its lot code with all three gates green.
    """
    # Read while the plant is up, because it is the plant's *configuration* that the
    # assertions below have to be stated in. The history they then check is answered with
    # the plant stopped, which is the proof itself.
    lot_prefix = _plant_setting("lot_code_prefix")
    curve_samples = int(_plant_setting("curve_samples"))

    _sh("docker", "stop", PLANT_CONTAINER)
    try:
        assert _count("inspection_results") > 0
        with psycopg.connect(DSN) as conn:
            row = conn.execute(
                "SELECT count(*), min(source_ts), max(source_ts) FROM inspection_results"
            ).fetchone()
            chain = conn.execute(
                """
                SELECT (SELECT count(*) FROM genealogy),
                       (SELECT count(*) FROM components WHERE lot_id IS NOT NULL),
                       (SELECT count(*) FROM components
                         JOIN component_lots ON component_lots.id = components.lot_id
                         WHERE NOT starts_with(lot_code, %s)
                            OR starts_with(supplier, %s)),
                       (SELECT count(*) FROM part_process_curves),
                       (SELECT count(*) FROM part_process_curves
                         WHERE cardinality(samples) <> %s),
                       (SELECT count(*) FROM part_dispositions
                         WHERE disposition = 'reject'),
                       (SELECT count(*) FROM part_dispositions
                         WHERE disposition = 'reject' AND reason IS NULL)
                """,
                (lot_prefix, lot_prefix, curve_samples),
            ).fetchone()
        assert row is not None and row[0] > 0
        assert row[1] is not None and row[2] is not None, (
            "history has no span to answer from"
        )

        assert chain is not None
        built, lotted, misfiled, curves, wrong_length, rejects, nameless = chain
        assert built > 0, "no as-built genealogy survived the plant going away"
        assert lotted > 0, "no component resolves to a supplier lot"
        assert misfiled == 0, (
            f"{misfiled} of {lotted} lotted components have a lot code not starting "
            f"{lot_prefix!r}, or a supplier that does — §4.1 gives LotCode and Supplier the "
            "same type, so the two carrying each other's value is silent everywhere else"
        )
        assert curves > 0 and wrong_length == 0, (
            f"{wrong_length} of {curves} press curves are not {curve_samples} samples long"
        )
        assert rejects > 0, "no reject was sorted, so no disposition reason was tested"
        assert nameless == 0, (
            f"{nameless} of {rejects} rejects were sorted with no reason"
        )
    finally:
        _sh("docker", "start", PLANT_CONTAINER)


# §1's remaining five proofs, named so the gap is explicit rather than implied by absence:
#
#   row 5  an external MCP client reaches the same tools            -> M4
#   row 6  every cited id resolves to a real row                    -> M4 (the agent cites)
#   row 7  unauthenticated requests are refused everywhere          -> M5
#   row 8  the harness scores answers against ground truth          -> M7
#   row 9  a knowledge edit changes an answer without a restart     -> M4
