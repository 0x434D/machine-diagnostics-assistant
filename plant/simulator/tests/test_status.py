"""The ledger never crosses the OPC UA wire (§4.5); Clock.SimulatedTime/Phase/Speed
now do (§4.1), but this file remains the only way to see either without an OPC UA
client, which is what these tests pin."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from conftest import build_fake_line
from simulator.clock import Phase, SimulatedClock
from simulator.config import ClockConfig, Settings
from simulator.historian import Ledger
from simulator.packml import State
from simulator.status import publish, snapshot


def _clock(wall: list[datetime]) -> SimulatedClock:
    return SimulatedClock(
        ClockConfig(history_depth=timedelta(hours=33), catchup_speed=700.0),
        wall_fn=lambda: wall[0],
    )


def _ledger() -> Ledger:
    ledger = Ledger(events=6, images=1, image_bytes=4096)
    for part in range(7):
        # Distinct values, because equal consecutive ones are what the ledger does
        # not count -- a fixture writing the same number seven times would assert
        # against a row count of one.
        ledger.record("S3_Inspection", "TaktTime", 6.0 + part)
        ledger.record("S3_Inspection", "PartCount", part)
    return ledger


def test_the_snapshot_reports_the_phase_and_the_ledger_it_was_taken_at() -> None:
    boot = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)
    wall = [boot]
    clock = _clock(wall)
    ledger = _ledger()
    line, _ = build_fake_line()

    during_catchup = snapshot(clock, Settings(), ledger, line)
    wall[0] = boot + timedelta(seconds=600)
    once_live = snapshot(clock, Settings(), ledger, line)

    assert during_catchup["phase"] == Phase.CATCHUP.value
    assert once_live["phase"] == Phase.LIVE.value
    assert once_live["simulated_now"] == wall[0].isoformat()
    # written_wall is the only field that tells a reader whether a snapshot is this
    # boot's or the last one's, so it has to come from the injected clock -- a direct
    # datetime.now() would make exactly this assertion impossible to write.
    assert during_catchup["written_wall"] == boot.isoformat()
    assert once_live["written_wall"] == wall[0].isoformat()
    assert during_catchup["ledger"] == {
        "rows": {"S3_Inspection.PartCount": 7, "S3_Inspection.TaktTime": 7},
        "events": 6,
        "images": 1,
        "image_bytes": 4096,
    }


@pytest.mark.asyncio
async def test_the_snapshot_reports_what_the_line_is_doing() -> None:
    """A starved line is invisible in a row count -- the stations still running keep
    raising theirs -- and this file is the only way to see one without an OPC UA
    client."""
    line, _ = build_fake_line()
    await line.step()  # S4 first, and B3_4 is empty

    taken = snapshot(
        _clock([datetime(2026, 9, 12, 12, 0, tzinfo=UTC)]), Settings(), _ledger(), line
    )

    assert taken["stations"] == {
        "S1": {"state": State.EXECUTE.value, "reason": ""},
        "S2": {"state": State.EXECUTE.value, "reason": ""},
        "S3": {"state": State.EXECUTE.value, "reason": ""},
        "S4": {"state": State.SUSPENDED.value, "reason": "starved:B3_4"},
    }
    assert taken["buffers"] == {"B1_2": 0, "B2_3": 0, "B3_4": 0}


def test_the_snapshot_is_json() -> None:
    """The ledger keys its rows by (owner, signal), and json.dumps refuses a tuple
    key outright -- so the field that made this file readable at a shell prompt is
    also the one that can stop it being written at all."""
    line, _ = build_fake_line()
    json.dumps(
        snapshot(
            _clock([datetime(2026, 9, 12, 12, 0, tzinfo=UTC)]),
            Settings(),
            _ledger(),
            line,
        )
    )


@pytest.mark.asyncio
async def test_the_published_file_is_always_complete_json(tmp_path: Path) -> None:
    """A reader is a separate process (`docker compose exec`) with no lock to take, so
    the file is written whole and renamed into place. Reading a partial line back as
    JSON would look like a crashed plant rather than a race."""
    path = tmp_path / "status.json"
    ledger = Ledger()
    line, _ = build_fake_line()
    settings = Settings(status_interval_seconds=0.01)
    task = asyncio.create_task(
        publish(path, _clock([datetime.now(UTC)]), settings, ledger, line)
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
