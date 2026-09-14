"""What the plant is doing, for a human and for Task 11's reconciler.

Clock.SimulatedTime/Phase/Speed are on the OPC UA wire deliberately (§4.1) -- the
gateway's own startup handshake reads Clock.Phase there (§4.3). The ledger (the row
counts this process tracks) never is: it is not plant data, and §4.5 keeps everything
that is not plant data off the wire. There is no OPC UA client at a shell prompt, so
this is read the way an operator reaches a container with nothing else to ask:

    docker compose -f plant/compose.yml exec line-simulator python -m simulator.status

That is a *separate process* from the server, with no access to its ledger, which is
why the server publishes it to a file on the history volume (alongside a plain-text
copy of the clock, so both are visible without an OPC UA client) and this reads it
back. The file carries the wall-clock instant it was written so staleness is visible
rather than assumed; it is refreshed every `status_interval_seconds`.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from simulator.clock import SimulatedClock
from simulator.config import Settings
from simulator.historian import Ledger
from simulator.line import Line

# On the plant-history volume rather than beside the code: /src is read-only in the
# container, and this is state, not source.
STATUS_FILE = Path("/data/status.json")


def snapshot(
    clock: SimulatedClock, settings: Settings, ledger: Ledger, line: Line
) -> dict[str, object]:
    """The plant's current phase, simulated time, row counts and what the line is doing.

    `written_wall` is the real clock, not simulated time -- the ServerTimestamp analogue
    of §4.2, for diagnostics only. Nothing in it is ever an input to analysis. It is read
    through the clock's injected wall_fn rather than datetime.now(), so a reader can tell
    a live snapshot from one a previous boot left behind and a test can prove it.

    Per-station state and per-buffer level are here because this file is what the demo
    prints and what Task 11's HMI falls back to, and because a line that has starved is
    invisible in a row count -- the counts keep rising on the stations still running.
    """
    return {
        "written_wall": clock.wall.isoformat(),
        "endpoint": settings.endpoint_url,
        "phase": clock.phase.value,
        "simulated_now": clock.now().isoformat(),
        "history_start": clock.history_start.isoformat(),
        "history_depth_hours": settings.history_depth_hours,
        "catchup_speed": clock.catchup_speed,
        "stations": {
            code: {"state": state.value, "reason": reason}
            for code, (state, reason) in line.station_states.items()
        },
        "buffers": {buffer.buffer_id: buffer.level for buffer in line.buffers},
        # Spelled out rather than asdict(ledger): the ledger keys its rows by
        # (owner, signal), and a tuple is not a JSON object key -- json.dumps refuses
        # it outright. rows_by_name is that key rendered the way the reconciliation's
        # own error message renders it, so the two read the same.
        "ledger": {
            "rows": ledger.rows_by_name(),
            "events": ledger.events,
            "images": ledger.images,
            "image_bytes": ledger.image_bytes,
        },
    }


async def publish(
    path: Path,
    clock: SimulatedClock,
    settings: Settings,
    ledger: Ledger,
    line: Line,
) -> None:
    """Rewrite `path` with a fresh snapshot forever, every
    `settings.status_interval_seconds`.

    Runs alongside production rather than as a callback threaded through it: the
    station has no business knowing anything is watching.
    """
    while True:
        # Written whole and renamed into place, so a reader never sees a half-written
        # file -- os.replace is atomic within a filesystem and the temporary sits on
        # the same volume.
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(snapshot(clock, settings, ledger, line), indent=2) + "\n"
        )
        tmp.replace(path)
        await asyncio.sleep(settings.status_interval_seconds)


def main() -> int:
    if not STATUS_FILE.exists():
        print(
            f"{STATUS_FILE} does not exist -- is the simulator running?",
            file=sys.stderr,
        )
        return 1
    print(STATUS_FILE.read_text(), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
