"""Catch-up and live across §4.1's twenty-five streams, and R1's pass condition:
the historian's row count equals the ledger, exactly, per stream.

Also the timestamp round trip everything above rests on: `HistorySQLite` stores both
of its `TIMESTAMP` columns through sqlite3's own deprecated adapter, and sqlite3's own
converter cannot read half of what that adapter writes. See
`simulator.historian.register_timestamp_converter`.
"""

from __future__ import annotations

import asyncio
import importlib
import sqlite3
import sqlite3.dbapi2
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import NamedTuple

import pytest
from asyncua import Server, ua
from asyncua.server.history_sql import HistorySQLite
from conftest import new_server
from simulator.address_space import (
    AddressSpace,
    build_address_space,
    historised_streams,
)
from simulator.clock import SimulatedClock
from simulator.config import ClockConfig, Settings
from simulator.historian import (
    Ledger,
    LedgerWriter,
    attach_historian,
    register_timestamp_converter,
)
from simulator.line import Line, run_catchup, run_live
from simulator.server import build_line
from simulator.stations.base import PartOutcome, ProduceFn

# Every station and buffer §4.1 declares, so a stream that stops being written shows
# up here as a missing key rather than as a count that merely looks small.
EXPECTED_STREAMS = 25


def _stamp(stamp: datetime | None) -> datetime:
    """A row's SourceTimestamp, with the "the column could be NULL" case asserted
    rather than typed away. Aware: `historian.register_timestamp_converter` keeps the
    UTC offset the adapter wrote."""
    assert stamp is not None
    return stamp


async def _stub_produce(part_id: str, _ts: datetime) -> PartOutcome:
    reject = part_id.endswith("7")
    return PartOutcome(
        disposition="reject" if reject else "good",
        defect_class="gap" if reject else None,
        confidence=0.91,
        image=b"\x89PNG" + b"\x00" * 4096 if reject else None,
        model_version="stub-1",
    )


class Plant(NamedTuple):
    server: Server
    space: AddressSpace
    line: Line
    writer: LedgerWriter
    ledger: Ledger
    storage: HistorySQLite


async def _build_plant(
    settings: Settings,
    clock: SimulatedClock,
    db: Path,
    produce: ProduceFn = _stub_produce,
) -> Plant:
    """The plant `server.main` builds, minus the endpoint and the inspection service.

    Deliberately through `server.build_line` rather than a second assembly of the same
    four stations: what this file asserts about is the wiring, and a copy of it here
    would reconcile perfectly while the shipped one wrote to the wrong nodes.
    """
    server = new_server()
    await server.init()
    idx = await server.register_namespace("http://machine-agent/plant")
    space = await build_address_space(server, idx, settings.buffer_capacity)

    ledger = Ledger()
    storage = await attach_historian(
        server,
        space,
        db,
        settings.history_page_size,
        clock.history_start - timedelta(seconds=settings.takt_seconds),
        ledger,
    )
    writer = LedgerWriter(space, ledger)
    line = build_line(writer, settings, produce)
    return Plant(server, space, line, writer, ledger, storage)


@pytest.mark.asyncio
async def test_every_stream_reconciles_exactly(tmp_path: Path) -> None:
    """R1's criterion, at twenty-five streams instead of three. Not "approximately":
    the three defects M1 found all present as a count that is short and a run that
    reports success.

    Checked at the configured production depth (33 h), not a smaller one, because all
    three only appear at that scale: asyncua's per-stream notification queue caps at
    10,000 and silently drops the oldest entry past that (19,800 parts overflowed it
    with no pacing at all, losing the oldest 9,800 rows per stream); a bare constant
    TaktTime is dropped by asyncua's own change filter regardless of SourceTimestamp;
    and the priming notification each variable's subscription fires at subscribe time
    carries the real wall clock unless given a deliberate simulated one first. A 1 h
    test hid all three, as it did the first time.

    `run_catchup` raises if the two disagree, so a plain return already carries the
    guarantee; the assertions below are what says *which* claim was checked, and pin
    the two counts a second, independent count of this test's own stub can confirm.
    """
    settings = Settings(
        history_depth_hours=ClockConfig.DEFAULT_HISTORY_DEPTH / timedelta(hours=1)
    )
    clock = SimulatedClock(
        ClockConfig(ClockConfig.DEFAULT_HISTORY_DEPTH, settings.catchup_speed)
    )
    plant = await _build_plant(settings, clock, tmp_path / "h.db")
    line, writer, ledger = plant.line, plant.writer, plant.ledger

    async with plant.server:
        history_end = await run_catchup(line, writer, clock, settings, plant.storage)
        counts = await writer.historian_row_counts(plant.storage)

    streams = {
        (stream.owner, stream.signal) for stream in historised_streams(plant.space)
    }
    assert len(streams) == EXPECTED_STREAMS
    # Every declared stream carries at least its priming row, so a stream missing from
    # the ledger is one nothing primed rather than one nothing wrote.
    assert set(ledger.rows) == streams

    expected = dict(ledger.rows)
    expected[("S3_Inspection", "InspectionResult")] = ledger.events
    assert counts == expected

    # The two counts this test knows independently of the ledger. S3 inspects every
    # part that reaches it exactly once, so its PartCount stream is one row per part
    # plus the priming row, and its event stream is one row per part with none.
    parts = ledger.rows[("S3_Inspection", "PartCount")] - 1
    assert ledger.events == parts
    assert parts > 10_000, (
        "the queue cap this test exists for is 10,000 rows per stream; a depth that "
        f"produces only {parts} parts cannot reach it"
    )

    # The line ran to the horizon rather than stopping somewhere inside it: live
    # resumes within one takt of where the configured depth ends.
    horizon = clock.history_start + clock.history_depth
    assert 0 <= (history_end - horizon).total_seconds() < settings.takt_seconds


@pytest.mark.asyncio
async def test_source_timestamps_are_simulated_not_wall_clock(tmp_path: Path) -> None:
    """§4.2: SourceTimestamp is simulated time. During catch-up the two diverge
    sharply, which is the whole point."""
    settings = Settings(history_depth_hours=1.0)
    clock = SimulatedClock(ClockConfig(timedelta(hours=1), 600.0))
    plant = await _build_plant(settings, clock, tmp_path / "h.db")

    async with plant.server:
        await run_catchup(plant.line, plant.writer, clock, settings, plant.storage)
        takt = plant.space.stations["S3_Inspection"].historised["TaktTime"]
        rows = await takt.read_raw_history(
            clock.history_start - timedelta(minutes=1),
            datetime.now(UTC) + timedelta(days=1),
            0,
        )

    timestamps = [r.SourceTimestamp for r in rows if r.SourceTimestamp is not None]
    assert timestamps, "history is empty"
    oldest = min(timestamps)
    assert oldest < datetime.now(UTC) - timedelta(minutes=50)


@pytest.mark.asyncio
async def test_the_priming_rows_carry_a_simulated_timestamp(tmp_path: Path) -> None:
    """OPC UA mandates an initial-value notification at subscribe time, so all 25
    streams get a row before any station has written anything. Left to the wall clock
    that `add_variable` stamped them with, those 25 rows break the invariant that
    SourceTimestamp is simulated time -- and nothing else fails when they do: the rows
    are there, the counts reconcile, and the timeline simply carries 25 entries from a
    clock that is not the plant's.

    Asserted for all 25 rather than for one stream: the priming loop is a loop, and a
    version of it that primed the stations and forgot the buffers would leave three
    wall-clock rows that any single-stream check passes straight over.
    """
    settings = Settings(history_depth_hours=60.0 / 3600.0)
    clock = SimulatedClock(ClockConfig(timedelta(seconds=60), settings.catchup_speed))
    priming = clock.history_start - timedelta(seconds=settings.takt_seconds)
    plant = await _build_plant(settings, clock, tmp_path / "h.db")

    oldest: dict[tuple[str, str], tuple[datetime, object]] = {}
    async with plant.server:
        await run_catchup(plant.line, plant.writer, clock, settings, plant.storage)
        for stream in historised_streams(plant.space):
            rows = await stream.node.read_raw_history(
                priming - timedelta(minutes=1),
                datetime.now(UTC) + timedelta(days=1),
                0,
            )
            stamps = [
                (row.SourceTimestamp, row.Value.Value if row.Value else None)
                for row in rows
                if row.SourceTimestamp is not None
            ]
            assert stamps, f"{stream.owner}.{stream.signal} has no history at all"
            oldest[stream.owner, stream.signal] = min(stamps)

    for key, (stamp, _value) in sorted(oldest.items()):
        assert stamp == priming, (
            f"{key[0]}.{key[1]} was primed at {stamp}, not {priming}"
        )

    # And each priming value is one the plant was genuinely in before its driver ran,
    # not a generated zero: a station that has never been cleared is Aborted.
    assert oldest[("S2_Joining", "State")][1] == "Aborted"
    assert oldest[("B1_2", "Level")][1] == 0


@pytest.mark.asyncio
async def test_the_historian_says_a_station_is_running_from_the_part_it_starts_on(
    tmp_path: Path,
) -> None:
    """The state history has to be true from the first part, not from the first
    suspension.

    S1_Feeding is the case that was wrong: nothing starves it, so before the line
    published its bring-up the first `State` row after priming was whatever its first
    *blockage* wrote -- 894.9 simulated seconds and ~157 parts into a shipped-settings
    run. Until then the historian answered "was S1 running?" with `Aborted` while
    `PartCount` climbed beside it, and the reconciliation was silent because the ledger
    and the historian were wrong together.

    Asserted against the rows in sqlite rather than against the Line's own state:
    what is wrong in that defect is what reached the historian.
    """
    settings = Settings(history_depth_hours=60.0 / 3600.0)
    clock = SimulatedClock(ClockConfig(timedelta(seconds=60), settings.catchup_speed))
    plant = await _build_plant(settings, clock, tmp_path / "h.db")

    async with plant.server:
        await run_catchup(plant.line, plant.writer, clock, settings, plant.storage)
        state = plant.space.stations["S1_Feeding"].historised["State"]
        rows = await state.read_raw_history(
            clock.history_start - timedelta(minutes=1),
            datetime.now(UTC) + timedelta(days=1),
            0,
        )
        parts = plant.space.stations["S1_Feeding"].historised["PartCount"]
        part_rows = await parts.read_raw_history(
            clock.history_start - timedelta(minutes=1),
            datetime.now(UTC) + timedelta(days=1),
            0,
        )

    history = [
        (_stamp(row.SourceTimestamp), row.Value.Value) for row in rows if row.Value
    ]
    assert [value for _, value in history[:7]] == [
        # The priming row, then PackML's real sequence. Not an Aborted -> Execute jump:
        # that transition does not exist, and state_changes records the pair.
        "Aborted",
        "Clearing",
        "Stopped",
        "Resetting",
        "Idle",
        "Starting",
        "Execute",
    ]
    stamps = [stamp for stamp, _ in history[:7]]
    assert stamps == sorted(set(stamps)), (
        "each transition needs its own SourceTimestamp"
    )

    # The claim that matters: S1 is on record as running before it is on record as
    # having made anything.
    running_at = next(stamp for stamp, value in history if value == "Execute")
    first_part = min(_stamp(row.SourceTimestamp) for row in part_rows[1:])
    assert running_at < first_part


@pytest.mark.asyncio
async def test_live_production_resumes_where_catch_up_stopped(tmp_path: Path) -> None:
    """The catch-up/live seam: no gap in the simulated timeline between the last
    generated part and the first live one.

    Catch-up fills the whole configured depth in far less wall time than the clock
    needs to reach Phase.LIVE (~9 s against ~170 s at the production defaults), and the
    two are independent -- the generator never reads clock.now(). A first live part
    stamped clock.now() therefore lands a whole catch-up wall's worth of simulated time
    after history ends, and every part that should have filled that interval is simply
    never produced. Nothing above the simulator can see that: the history is internally
    consistent, just missing tens of parts in the minutes immediately after boot -- the
    freshest window the flagship question asks about.

    The wall clock is injected and jumped straight past catch-up so the seam this
    exercises is ten minutes wide with no ten minutes of waiting.
    """
    settings = Settings(history_depth_hours=60.0 / 3600.0)
    depth = timedelta(seconds=60)
    seam = timedelta(seconds=600)

    stamps: list[datetime] = []

    async def _recording_produce(part_id: str, ts: datetime) -> PartOutcome:
        stamps.append(ts)
        return await _stub_produce(part_id, ts)

    boot = datetime.now(UTC)
    wall = [boot]
    clock = SimulatedClock(
        ClockConfig(history_depth=depth, catchup_speed=settings.catchup_speed),
        wall_fn=lambda: wall[0],
    )

    plant = await _build_plant(settings, clock, tmp_path / "h.db", _recording_produce)
    line, writer = plant.line, plant.writer

    async with plant.server:
        await run_catchup(line, writer, clock, settings, plant.storage)
        generated = len(stamps)
        assert generated, "catch-up produced no parts at all"
        wall[0] = boot + seam

        live = asyncio.create_task(run_live(line, writer, clock, settings))
        try:
            # Well inside the seam: once the cursor passes the wall clock, run_live
            # paces at 1.0 and every further part costs a real takt. Bounded so a
            # regression fails here instead of hanging the suite.
            async with asyncio.timeout(30):
                while len(stamps) < generated + 50:
                    await asyncio.sleep(0.01)
        finally:
            live.cancel()
            with pytest.raises(asyncio.CancelledError):
                await live

    gaps = [(later - earlier).total_seconds() for earlier, later in pairwise(stamps)]
    assert min(gaps) > 0, "the simulated timeline must never run backwards"
    # A cycle S3 spends suspended produces no part, so the gap between two parts is a
    # small multiple of the takt rather than exactly one. The defect this guards
    # against produces a single gap of `seam` -- 600 s, a hundred takts -- so ten
    # leaves room for a starvation episode without letting the hole through.
    assert max(gaps) < settings.takt_seconds * 10


@pytest.mark.asyncio
async def test_the_reconciliation_names_the_stream_that_disagrees(
    tmp_path: Path,
) -> None:
    """At three streams "the historian disagrees with the ledger" was a usable
    message. At twenty-five it is an hour of bisecting, which is the cost this
    project keeps paying for messages that state the failure without locating it."""
    settings = Settings(history_depth_hours=60.0 / 3600.0)
    clock = SimulatedClock(ClockConfig(timedelta(seconds=60), settings.catchup_speed))
    plant = await _build_plant(settings, clock, tmp_path / "h.db")

    async with plant.server:
        # A row the historian will never hold, recorded as if S4 had written it.
        plant.ledger.record("S4_Outfeed", "OutfeedFill", -1.0)
        with pytest.raises(RuntimeError, match=r"S4_Outfeed\.OutfeedFill") as raised:
            await run_catchup(plant.line, plant.writer, clock, settings, plant.storage)

    message = str(raised.value)
    assert "on 1 of 26 streams" in message
    assert "S1_Feeding" not in message, "only the offender is named"


# --- the timestamp round trip (§12) -------------------------------------------------

_WHOLE = 0
"""Microseconds. The value `datetime.isoformat(" ")` omits, which is the whole
defect: one row in 386,782 of a 33 h run, measured in Task 10."""

_SOME = 123_456
"""Microseconds. Any non-zero value round-trips through sqlite3's own converter, which
is why 380,000 rows can pass and the 380,001st kill the run."""


@pytest.fixture
def sqlite3s_own_timestamp_converter() -> Iterator[None]:
    """Put sqlite3's own deprecated `TIMESTAMP` converter back for one test.

    `attach_historian` replaces it process-wide, and every other test in this file
    builds a plant -- so without this the defect below is unreachable and the test
    would pass on the fix it exists to measure. Reloading `sqlite3.dbapi2` re-runs the
    standard library's own registration, which is the only way to get that exact
    converter back: it is a closure inside `register_adapters_and_converters`, not a
    module attribute anything can import.

    Restored by re-registering rather than by putting back whatever was there before,
    so a session in which this test happens to run first does not leave sqlite3's own
    in place for everything after it.
    """
    importlib.reload(sqlite3.dbapi2)
    try:
        yield
    finally:
        register_timestamp_converter()


async def _history_of_one_row(
    db: Path, source_microsecond: int, server_microsecond: int
) -> tuple[HistorySQLite, ua.NodeId]:
    """One historised node holding exactly one row, with both of its `TIMESTAMP`
    columns placed to order.

    Constructed directly rather than generated: the offending row appears once in
    ~386,782, so waiting for one means a 33 h run and the odds.
    """
    storage = HistorySQLite(str(db))
    await storage.init()
    node_id = ua.NodeId(ua.Int32(1), ua.Int16(2))
    await storage.new_historized_node(node_id, None, 0)
    await storage.save_node_value(
        node_id,
        ua.DataValue(
            ua.Variant(6.0, ua.VariantType.Double),
            # Both suppressions for the reason address_space.write_at records: the
            # fields are typed ua.DateTime, a datetime subclass asyncua's own runtime
            # never constructs and sqlite3's parameter binder refuses.
            SourceTimestamp=datetime(  # type: ignore[arg-type]
                2026, 9, 13, 6, 0, 58, source_microsecond, tzinfo=UTC
            ),
            ServerTimestamp=datetime(  # type: ignore[arg-type]
                2026, 9, 13, 6, 1, 2, server_microsecond, tzinfo=UTC
            ),
        ),
    )
    return storage, node_id


@pytest.mark.asyncio
@pytest.mark.usefixtures("sqlite3s_own_timestamp_converter")
@pytest.mark.parametrize(
    ("source_microsecond", "server_microsecond"),
    [
        pytest.param(_SOME, _WHOLE, id="whole-second ServerTimestamp"),
        # §12 names ServerTimestamp only. SourceTimestamp is declared TIMESTAMP in the
        # same table and fails identically -- and it is simulated time, the one field
        # here that may not be nudged to suit a storage layer. Recorded because it
        # roughly doubles the exposure the risk row states.
        pytest.param(_WHOLE, _SOME, id="whole-second SourceTimestamp"),
    ],
)
async def test_sqlite3s_own_converter_cannot_read_what_its_adapter_wrote(
    tmp_path: Path, source_microsecond: int, server_microsecond: int
) -> None:
    """§12's found risk, constructed rather than waited for.

    The failure is not an `aiosqlite.Error`, so it escapes `read_node_history`'s own
    try/except: every `HistoryRead` whose window contains the row raises, and raises
    again on every retry. A backfill does not come back short -- it stops.
    """
    storage, node_id = await _history_of_one_row(
        tmp_path / "broken.db", source_microsecond, server_microsecond
    )
    try:
        with pytest.raises(ValueError, match="invalid literal for int"):
            await storage.read_node_history(
                node_id,
                datetime(2026, 9, 13, tzinfo=UTC),
                datetime(2026, 9, 14, tzinfo=UTC),
                0,
            )
    finally:
        await storage.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source_microsecond", "server_microsecond"),
    [
        pytest.param(_SOME, _WHOLE, id="whole-second ServerTimestamp"),
        pytest.param(_WHOLE, _SOME, id="whole-second SourceTimestamp"),
        pytest.param(_SOME, _SOME, id="the case that already worked"),
    ],
)
async def test_the_registered_converter_reads_every_row_back_unchanged(
    tmp_path: Path, source_microsecond: int, server_microsecond: int
) -> None:
    """The fix, over the same three rows -- including the one that always worked, so
    that a converter which repairs the whole second by mangling everything else fails
    here rather than in production.

    Both instants come back aware. sqlite3's own converter discards the offset and
    returns a naive datetime that is really UTC, which is M1's F3: a continuation
    point that cannot be compared against the aware datetime it came from.
    """
    register_timestamp_converter()
    storage, node_id = await _history_of_one_row(
        tmp_path / "fixed.db", source_microsecond, server_microsecond
    )
    try:
        rows, _ = await storage.read_node_history(
            node_id,
            datetime(2026, 9, 13, tzinfo=UTC),
            datetime(2026, 9, 14, tzinfo=UTC),
            0,
        )
    finally:
        await storage.stop()

    assert len(rows) == 1
    assert rows[0].SourceTimestamp == datetime(
        2026, 9, 13, 6, 0, 58, source_microsecond, tzinfo=UTC
    )
    assert rows[0].ServerTimestamp == datetime(
        2026, 9, 13, 6, 1, 2, server_microsecond, tzinfo=UTC
    )


@pytest.mark.asyncio
@pytest.mark.usefixtures("sqlite3s_own_timestamp_converter")
async def test_attaching_the_historian_registers_the_converter(
    tmp_path: Path,
) -> None:
    """The wiring, not the parsing: it is `attach_historian` that has to do this, and
    it has to do it before any query can run against sqlite3's own.

    Asserted by what the registered converter *does* with the offending text rather
    than by which function object is registered, so moving the fix somewhere else in
    the same call still passes.
    """
    whole_second = b"2026-09-13 06:00:58+00:00"
    with pytest.raises(ValueError, match="invalid literal for int"):
        sqlite3.converters["TIMESTAMP"](whole_second)

    settings = Settings(history_depth_hours=60.0 / 3600.0)
    clock = SimulatedClock(ClockConfig(timedelta(seconds=60), settings.catchup_speed))
    await _build_plant(settings, clock, tmp_path / "h.db")

    assert sqlite3.converters["TIMESTAMP"](whole_second) == datetime(
        2026, 9, 13, 6, 0, 58, tzinfo=UTC
    )
