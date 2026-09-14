import asyncio
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path

import pytest
from conftest import new_server
from simulator.address_space import build_address_space
from simulator.clock import SimulatedClock
from simulator.config import ClockConfig, Settings
from simulator.historian import Ledger, attach_historian
from simulator.station_s3 import (
    PartOutcome,
    _historian_row_counts,
    generate_history,
    run_live,
)


async def _stub_produce(part_id: str, _ts: datetime) -> PartOutcome:
    reject = part_id.endswith("7")
    return PartOutcome(
        disposition="reject" if reject else "good",
        defect_class="gap" if reject else None,
        confidence=0.91,
        image=b"\x89PNG" + b"\x00" * 4096 if reject else None,
        model_version="stub-1",
    )


@pytest.mark.asyncio
async def test_catchup_reconciles_ledger_with_historian_at_production_depth(
    tmp_path: Path,
) -> None:
    """R1's core assertion: the ledger and the historian agree exactly -- checked
    at the configured production depth (33 h), not a smaller one. Three defects
    only showed up at this scale: asyncua's per-stream notification queue caps at
    10,000 and silently drops the oldest entry past that (19,800 parts overflowed
    it with no pacing at all, losing the oldest 9,800 rows per stream); a bare
    constant TaktTime is dropped by asyncua's own change filter regardless of
    SourceTimestamp; and the OPC UA priming notification each variable's
    subscription fires at subscribe time carries the real wall clock unless given a
    deliberate simulated one first. A 1 h test would have hidden all three, as it
    did the first time. Silent loss here is invisible to every layer above.
    """
    settings = Settings(
        history_depth_hours=ClockConfig.DEFAULT_HISTORY_DEPTH / timedelta(hours=1)
    )
    server = new_server()
    await server.init()
    idx = await server.register_namespace("http://machine-agent/plant")
    space = await build_address_space(server, idx, settings.buffer_capacity)

    clock = SimulatedClock(
        ClockConfig(ClockConfig.DEFAULT_HISTORY_DEPTH, settings.catchup_speed)
    )
    ledger = Ledger()
    storage = await attach_historian(
        server,
        space,
        tmp_path / "h.db",
        settings.history_page_size,
        clock.history_start - timedelta(seconds=settings.takt_seconds),
        ledger,
    )

    expected_parts = int(clock.history_depth.total_seconds() // settings.takt_seconds)
    async with server:
        await generate_history(space, clock, settings, _stub_produce, ledger, storage)

        historian_counts = await _historian_row_counts(space, storage)
        # The last few minutes' worth is enough to find the newest row without
        # paging the whole 33 h back (that's exercised separately, below).
        tail = await space.part_count.read_raw_history(
            clock.history_start + clock.history_depth - timedelta(minutes=5),
            datetime.now(UTC) + timedelta(days=1),
            0,
        )

    expected_rejects = sum(
        1 for i in range(expected_parts) if f"A-{i:08d}".endswith("7")
    )
    # +1 on takt/part_count for the priming row every historised variable's
    # subscription fires at subscribe time (accounted for, not excluded -- see
    # historian.attach_historian). Events get no priming row: there is no "current
    # value" to report for something that isn't stateful.
    assert ledger.takt == expected_parts + 1
    assert ledger.part_count == expected_parts + 1
    assert ledger.events == expected_parts
    assert ledger.images == expected_rejects

    assert historian_counts == {
        "takt": ledger.takt,
        "part_count": ledger.part_count,
        "events": ledger.events,
    }

    newest_timestamps = [
        r.SourceTimestamp for r in tail if r.SourceTimestamp is not None
    ]
    assert newest_timestamps, (
        "expected at least one row in the last 5 simulated minutes"
    )
    newest = max(newest_timestamps)
    if newest.tzinfo is None:
        # HistorySQLite round-trips SourceTimestamp through sqlite3's
        # PARSE_DECLTYPES "timestamp" converter, which always comes back naive --
        # confirmed against the installed asyncua 2.0.1. The instant itself is
        # still the UTC one this station wrote.
        newest = newest.replace(tzinfo=UTC)
    expected_end = clock.history_start + clock.history_depth
    # Cumulative jitter drift (TaktTime's Gaussian jitter, resampled on collision --
    # see station_s3._next_takt) plus up to one nominal takt from the final
    # incomplete cycle. Measured at ~7 s over 19,800 steps at the default sigma;
    # 60 s leaves ample margin without being wide enough to hide runaway drift.
    assert abs((expected_end - newest).total_seconds()) <= 60


@pytest.mark.asyncio
async def test_source_timestamps_are_simulated_not_wall_clock(tmp_path: Path) -> None:
    """§4.2: SourceTimestamp is simulated time. During catch-up the two diverge
    sharply, which is the whole point."""
    settings = Settings(history_depth_hours=1.0, takt_seconds=6.0)
    server = new_server()
    await server.init()
    idx = await server.register_namespace("http://machine-agent/plant")
    space = await build_address_space(server, idx, settings.buffer_capacity)

    clock = SimulatedClock(ClockConfig(timedelta(hours=1), 600.0))
    ledger = Ledger()
    storage = await attach_historian(
        server,
        space,
        tmp_path / "h.db",
        settings.history_page_size,
        clock.history_start - timedelta(seconds=settings.takt_seconds),
        ledger,
    )

    async with server:
        await generate_history(space, clock, settings, _stub_produce, ledger, storage)
        rows = await space.takt.read_raw_history(
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
async def test_live_production_resumes_where_catch_up_stopped(tmp_path: Path) -> None:
    """The catch-up/live seam: no gap in the simulated timeline between the last
    generated part and the first live one.

    generate_history fills the whole configured depth in far less wall time than the
    clock needs to reach Phase.LIVE (~9 s against ~170 s at the production defaults),
    and the two are independent -- the generator never reads clock.now(). A first live
    part stamped clock.now() therefore lands a whole catch-up wall's worth of
    simulated time after history ends, and every part that should have filled that
    interval is simply never produced. Nothing above the simulator can see that: the
    history is internally consistent, just missing tens of parts in the minutes
    immediately after boot -- the freshest window the flagship question asks about.

    The wall clock is injected and jumped straight past catch-up so the seam this
    exercises is ten minutes wide with no ten minutes of waiting.
    """
    settings = Settings(history_depth_hours=60.0 / 3600.0, takt_seconds=6.0)
    depth = timedelta(seconds=60)
    seam = timedelta(seconds=600)

    stamps: list[datetime] = []

    async def _recording_produce(part_id: str, ts: datetime) -> PartOutcome:
        stamps.append(ts)
        return await _stub_produce(part_id, ts)

    server = new_server()
    await server.init()
    idx = await server.register_namespace("http://machine-agent/plant")
    space = await build_address_space(server, idx, settings.buffer_capacity)

    boot = datetime.now(UTC)
    wall = [boot]
    clock = SimulatedClock(
        ClockConfig(history_depth=depth, catchup_speed=settings.catchup_speed),
        wall_fn=lambda: wall[0],
    )
    ledger = Ledger()
    storage = await attach_historian(
        server,
        space,
        tmp_path / "h.db",
        settings.history_page_size,
        clock.history_start - timedelta(seconds=settings.takt_seconds),
        ledger,
    )

    async with server:
        history_end = await generate_history(
            space, clock, settings, _recording_produce, ledger, storage
        )
        generated = len(stamps)
        wall[0] = boot + seam

        live = asyncio.create_task(
            run_live(
                space,
                clock,
                settings,
                _recording_produce,
                ledger,
                start_index=ledger.events,
                start_ts=history_end,
            )
        )
        try:
            # Well inside the seam: once the cursor passes the wall clock,
            # run_live paces at 1.0 and every further part costs a real takt.
            # Bounded so a regression fails here instead of hanging the suite.
            async with asyncio.timeout(30):
                while len(stamps) < generated + 50:
                    await asyncio.sleep(0.01)
        finally:
            live.cancel()
            with pytest.raises(asyncio.CancelledError):
                await live

    gaps = [(later - earlier).total_seconds() for earlier, later in pairwise(stamps)]
    assert min(gaps) > 0, "the simulated timeline must never run backwards"
    # One takt plus the jitter's own room. The defect this guards against produces a
    # single gap of `seam` (600 s) at the crossover, two orders of magnitude out.
    assert max(gaps) < settings.takt_seconds * 2
