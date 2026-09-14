"""Catch-up and live across §4.1's twenty-five streams, and R1's pass condition:
the historian's row count equals the ledger, exactly, per stream."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import NamedTuple

import pytest
from asyncua import Server
from asyncua.server.history_sql import HistorySQLite
from conftest import new_server
from simulator.address_space import (
    AddressSpace,
    build_address_space,
    historised_streams,
)
from simulator.clock import SimulatedClock
from simulator.config import ClockConfig, Settings
from simulator.historian import Ledger, LedgerWriter, attach_historian
from simulator.line import Line, run_catchup, run_live
from simulator.server import build_line
from simulator.stations.base import PartOutcome, ProduceFn

# Every station and buffer §4.1 declares, so a stream that stops being written shows
# up here as a missing key rather than as a count that merely looks small.
EXPECTED_STREAMS = 25


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
    # HistorySQLite round-trips SourceTimestamp through sqlite3's PARSE_DECLTYPES
    # "timestamp" converter, which always comes back naive -- confirmed against the
    # installed asyncua 2.0.1, not assumed. The instant itself is still the UTC one
    # this station wrote, so normalising before comparing is correct, not a fudge.
    if oldest.tzinfo is None:
        oldest = oldest.replace(tzinfo=UTC)
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
            # HistorySQLite round-trips SourceTimestamp through sqlite3's
            # PARSE_DECLTYPES "timestamp" converter, which always comes back naive --
            # confirmed against the installed asyncua 2.0.1. The instant itself is
            # still the UTC one that was written.
            oldest[stream.owner, stream.signal] = min(
                (stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp, value)
                for stamp, value in stamps
            )

    for key, (stamp, _value) in sorted(oldest.items()):
        assert stamp == priming, (
            f"{key[0]}.{key[1]} was primed at {stamp}, not {priming}"
        )

    # And each priming value is one the plant was genuinely in before its driver ran,
    # not a generated zero: a station that has never been cleared is Aborted.
    assert oldest[("S2_Joining", "State")][1] == "Aborted"
    assert oldest[("B1_2", "Level")][1] == 0


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
