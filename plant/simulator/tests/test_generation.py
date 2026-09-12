from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from asyncua import Server
from simulator.address_space import build_address_space
from simulator.clock import SimulatedClock
from simulator.config import ClockConfig, Settings
from simulator.historian import Ledger, attach_historian
from simulator.station_s3 import PartOutcome, generate_history


async def _stub_produce(part_id: str, _ts: datetime) -> PartOutcome:
    reject = part_id.endswith("7")
    return PartOutcome(
        disposition="reject" if reject else "good",
        defect_class="gap" if reject else None,
        confidence=0.91,
        image=b"\x89PNG" + b"\x00" * 4096 if reject else None,
    )


@pytest.mark.asyncio
async def test_catchup_writes_every_expected_row(tmp_path: Path) -> None:
    """R1's core assertion: the ledger and the historian agree exactly.
    Silent loss here is invisible to every layer above."""
    settings = Settings(history_depth_hours=1.0, takt_seconds=6.0, catchup_speed=600.0)
    server = Server()
    await server.init()
    idx = await server.register_namespace("http://machine-agent/plant")
    space = await build_address_space(server, idx)
    await attach_historian(server, space, tmp_path / "h.db", settings.history_page_size)

    clock = SimulatedClock(ClockConfig(timedelta(hours=1), 600.0))
    ledger = Ledger()
    async with server:
        await generate_history(space, clock, settings, _stub_produce, ledger)

    expected = int(3600 / 6.0)  # 600 parts in one hour at 6 s takt
    assert ledger.takt == expected
    assert ledger.part_count == expected
    assert ledger.events == expected
    assert ledger.images == sum(
        1 for i in range(expected) if f"A-{i:08d}".endswith("7")
    )


@pytest.mark.asyncio
async def test_source_timestamps_are_simulated_not_wall_clock(tmp_path: Path) -> None:
    """§4.2: SourceTimestamp is simulated time. During catch-up the two diverge
    sharply, which is the whole point."""
    settings = Settings(history_depth_hours=1.0, takt_seconds=6.0)
    server = Server()
    await server.init()
    idx = await server.register_namespace("http://machine-agent/plant")
    space = await build_address_space(server, idx)
    await attach_historian(server, space, tmp_path / "h.db", settings.history_page_size)

    clock = SimulatedClock(ClockConfig(timedelta(hours=1), 600.0))
    async with server:
        await generate_history(space, clock, settings, _stub_produce, Ledger())
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
