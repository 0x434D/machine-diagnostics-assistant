"""What the plant is doing, for a human and for Task 11's reconciler.

There is nothing to curl: the plant's only port speaks OPC UA (§2.1), and the address
space deliberately carries no clock or ledger nodes -- neither is plant data, and §4.5
keeps everything that is not plant data off the wire. So this is read the way an
operator reaches a container that exposes nothing:

    docker compose -f plant/compose.yml exec line-simulator python -m simulator.status

That is a *separate process* from the server, with no access to its clock or its ledger,
which is why the server publishes both to a file on the history volume and this reads
it back. The file carries the wall-clock instant it was written so staleness is visible
rather than assumed; it is refreshed every `status_interval_seconds`.
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path

from simulator.clock import SimulatedClock
from simulator.config import Settings
from simulator.historian import Ledger

# On the plant-history volume rather than beside the code: /src is read-only in the
# container, and this is state, not source.
STATUS_FILE = Path("/data/status.json")


def snapshot(
    clock: SimulatedClock, settings: Settings, ledger: Ledger
) -> dict[str, object]:
    """The plant's current phase, simulated time and row counts.

    `written_wall` is the real clock, not simulated time -- the ServerTimestamp analogue
    of §4.2, for diagnostics only. Nothing in it is ever an input to analysis. It is read
    through the clock's injected wall_fn rather than datetime.now(), so a reader can tell
    a live snapshot from one a previous boot left behind and a test can prove it.
    """
    return {
        "written_wall": clock.wall.isoformat(),
        "endpoint": settings.endpoint_url,
        "phase": clock.phase.value,
        "simulated_now": clock.now().isoformat(),
        "history_start": clock.history_start.isoformat(),
        "history_depth_hours": settings.history_depth_hours,
        "catchup_speed": clock.catchup_speed,
        "ledger": asdict(ledger),
    }


async def publish(
    path: Path,
    clock: SimulatedClock,
    settings: Settings,
    ledger: Ledger,
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
        tmp.write_text(json.dumps(snapshot(clock, settings, ledger), indent=2) + "\n")
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
