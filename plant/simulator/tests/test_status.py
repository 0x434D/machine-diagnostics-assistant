"""The ledger never crosses the OPC UA wire (§4.5); Clock.SimulatedTime/Phase/Speed
now do (§4.1), but this file remains the only way to see either without an OPC UA
client, which is what these tests pin."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from simulator.clock import Phase, SimulatedClock
from simulator.config import ClockConfig, Settings
from simulator.historian import Ledger
from simulator.status import publish, snapshot


def _clock(wall: list[datetime]) -> SimulatedClock:
    return SimulatedClock(
        ClockConfig(history_depth=timedelta(hours=33), catchup_speed=700.0),
        wall_fn=lambda: wall[0],
    )


def test_the_snapshot_reports_the_phase_and_the_ledger_it_was_taken_at() -> None:
    boot = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)
    wall = [boot]
    clock = _clock(wall)
    ledger = Ledger(takt=7, part_count=7, events=6, images=1, image_bytes=4096)

    during_catchup = snapshot(clock, Settings(), ledger)
    wall[0] = boot + timedelta(seconds=600)
    once_live = snapshot(clock, Settings(), ledger)

    assert during_catchup["phase"] == Phase.CATCHUP.value
    assert once_live["phase"] == Phase.LIVE.value
    assert once_live["simulated_now"] == wall[0].isoformat()
    # written_wall is the only field that tells a reader whether a snapshot is this
    # boot's or the last one's, so it has to come from the injected clock -- a direct
    # datetime.now() would make exactly this assertion impossible to write.
    assert during_catchup["written_wall"] == boot.isoformat()
    assert once_live["written_wall"] == wall[0].isoformat()
    assert during_catchup["ledger"] == {
        "takt": 7,
        "part_count": 7,
        "events": 6,
        "images": 1,
        "image_bytes": 4096,
    }


@pytest.mark.asyncio
async def test_the_published_file_is_always_complete_json(tmp_path: Path) -> None:
    """A reader is a separate process (`docker compose exec`) with no lock to take, so
    the file is written whole and renamed into place. Reading a partial line back as
    JSON would look like a crashed plant rather than a race."""
    path = tmp_path / "status.json"
    ledger = Ledger()
    settings = Settings(status_interval_seconds=0.01)
    task = asyncio.create_task(
        publish(path, _clock([datetime.now(UTC)]), settings, ledger)
    )
    try:
        async with asyncio.timeout(5):
            while not path.exists():
                await asyncio.sleep(0.005)
        for _ in range(50):
            ledger.events += 1
            assert json.loads(path.read_text())["phase"] in {"catchup", "live"}
            await asyncio.sleep(0.001)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert not list(tmp_path.glob("*.tmp")), (
        "the temporary file must not be left behind"
    )
