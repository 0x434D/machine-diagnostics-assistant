"""The plant HMI's payload, and the server that pushes it (§15, D5).

**Colour by category, not by state.** §15 banks "producing, waiting-on-others,
held-by-own-fault, stopped, transitioning" against M6 and files it against the
diagnostics chat box, which has no PackML states to colour. This is the frontend that
does, and it is the distinction the whole project exists to make: `Suspended` is a
*consequence* -- someone else stopped -- while `Held` and `Aborted` are *cause
candidates*. A viewer glancing at the line sees amber on the stations that are merely
waiting and red on the one that is actually broken, and that reading is the diagnosis.

The mapping is exhaustive over `State` and proved so by test. A state with no category
renders as nothing, which on a plant screen is worse than rendering wrong.

**Colour is never the only channel.** `category` decides the colour and `state` carries
the PackML name the colour stands for; the screen shows both, always (ISA-101, and the
accessibility reason that needs no citation).

**Served in this process, not by a second container.** A container polling
`simulator.status`'s file would make `status_interval_seconds` the screen's frame rate
and would put a second copy of the line's state between the line and the screen. In
process, `line_snapshot` reads the Line itself, and M2c's fault injection reaches the
scenario engine directly rather than needing a channel back in.
"""

from __future__ import annotations

import asyncio
from typing import Final, TypedDict

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from simulator.clock import SimulatedClock
from simulator.config import Settings
from simulator.line import Line
from simulator.packml import State

PRODUCING: Final = "producing"
WAITING_ON_OTHERS: Final = "waiting-on-others"
HELD_BY_OWN_FAULT: Final = "held-by-own-fault"
STOPPED: Final = "stopped"
TRANSITIONING: Final = "transitioning"

CATEGORIES: Final[tuple[str, ...]] = (
    PRODUCING,
    WAITING_ON_OTHERS,
    HELD_BY_OWN_FAULT,
    STOPPED,
    TRANSITIONING,
)
"""§15's five, in the order a screen legend reads them. The frontend derives its CSS
class name from these strings, so they are part of the payload contract."""

_CATEGORY_BY_STATE: Final[dict[State, str]] = {
    State.EXECUTE: PRODUCING,
    # §3.3's consequence side. `Unsuspending` is here rather than under transitioning
    # because the station is still waiting on the buffer it was suspended for until it
    # settles, and a station that flickered green on its way out of a starvation would
    # read as a line that had recovered when it had not.
    State.SUSPENDING: WAITING_ON_OTHERS,
    State.SUSPENDED: WAITING_ON_OTHERS,
    State.UNSUSPENDING: WAITING_ON_OTHERS,
    # §3.3's cause candidates: none of these clears itself, and every one of them is a
    # station to go and look at. `Unholding` included -- it is a station coming out of
    # its own fault, which is still that station's episode.
    State.HOLDING: HELD_BY_OWN_FAULT,
    State.HELD: HELD_BY_OWN_FAULT,
    State.UNHOLDING: HELD_BY_OWN_FAULT,
    State.ABORTING: HELD_BY_OWN_FAULT,
    State.ABORTED: HELD_BY_OWN_FAULT,
    # Not running, and nothing wrong. `Stopping` settles into `Stopped`, and `Idle` is a
    # station that has been reset and not yet started.
    State.STOPPING: STOPPED,
    State.STOPPED: STOPPED,
    State.IDLE: STOPPED,
    # Coming up: the three acting states on the path from `Aborted` to `Execute`.
    State.CLEARING: TRANSITIONING,
    State.RESETTING: TRANSITIONING,
    State.STARTING: TRANSITIONING,
}


def category_for(state: State) -> str:
    """Which of `CATEGORIES` this PackML state renders as.

    Raises KeyError for a state this module does not map, which is how a `State` added
    without a colour becomes a loud failure rather than a blank tile. The exhaustiveness
    test is what makes that case unreachable.
    """
    return _CATEGORY_BY_STATE[state]


class StationView(TypedDict):
    """One station as the screen reads it. `state` travels with `category` because the
    category decides the colour and ISA-101 forbids the colour being the only channel."""

    code: str
    state: str
    category: str
    reason: str


class BufferView(TypedDict):
    """One buffer, with the capacity its level is a fraction of -- so the screen draws a
    fill bar without a copy of `Settings.buffer_capacity` of its own."""

    code: str
    level: int
    capacity: int
    upstream: str
    downstream: str


class LineSnapshot(TypedDict):
    """The frame `/snapshot` and `/ws` both serve.

    A TypedDict rather than a bare dict because the frontend's `snapshot.ts` is a
    hand-written mirror of exactly these names: this is the side of that pair that a
    type checker can hold still, and `tests/test_hmi.py` is what holds the other.
    """

    phase: str
    simulated_now: str
    history_start: str
    catchup_speed: float
    written_wall: str
    stations: list[StationView]
    buffers: list[BufferView]


def line_snapshot(line: Line, clock: SimulatedClock) -> LineSnapshot:
    """Everything the screen draws, in one frame.

    Stations and buffers are lists rather than objects keyed by code because the screen
    draws a line: the order is line order, and a JSON object's order is not something a
    payload should be asked to carry.

    `simulated_now` is the clock every number on this screen belongs to -- the
    `SourceTimestamp` analogue of §4.2. `written_wall` is the real clock and is here
    only so a viewer can tell a live frame from a frozen tab; nothing analyses it.
    """
    return {
        "phase": clock.phase.value,
        "simulated_now": clock.now().isoformat(),
        "history_start": clock.history_start.isoformat(),
        "catchup_speed": clock.catchup_speed,
        "written_wall": clock.wall.isoformat(),
        "stations": [
            StationView(
                code=code,
                state=state.value,
                category=category_for(state),
                reason=reason,
            )
            for code, (state, reason) in line.station_states.items()
        ],
        "buffers": [
            BufferView(
                code=buffer.buffer_id,
                level=buffer.level,
                capacity=buffer.capacity,
                upstream=buffer.upstream,
                downstream=buffer.downstream,
            )
            for buffer in line.buffers
        ],
    }


def build_app(line: Line, clock: SimulatedClock, settings: Settings) -> FastAPI:
    """The screen's three endpoints, reading the live `line` and `clock`.

    `/snapshot` and `/ws` serve the same payload, so a frame the screen renders and one
    a human curls are the same object -- there is no second rendering to disagree.
    """
    app = FastAPI(title="plant HMI", docs_url=None, redoc_url=None)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    # response_model=None: FastAPI would otherwise build a pydantic model from the
    # annotation and re-validate every frame against it. `LineSnapshot` is the payload's
    # own definition and `tests/test_hmi.py` is what asserts it, so a second schema
    # derived from the first would cost a validation per frame and add no check.
    @app.get("/snapshot", response_model=None)
    async def snapshot() -> LineSnapshot:
        return line_snapshot(line, clock)

    @app.websocket("/ws")
    async def stream(socket: WebSocket) -> None:
        await socket.accept()
        try:
            while True:
                # Sent before the first sleep, so a screen that has just connected
                # draws the line rather than an empty page for one interval.
                await socket.send_json(line_snapshot(line, clock))
                await asyncio.sleep(settings.hmi_interval_seconds)
        except WebSocketDisconnect:
            # The one exception here with a real recovery: a closed tab is not a
            # failure of the plant, and this coroutine has nothing left to do.
            # Deliberately narrow -- `asyncio.CancelledError` is shutdown and is not
            # caught, so it still propagates out of the TaskGroup that owns this server.
            return

    return app


async def serve(line: Line, clock: SimulatedClock, settings: Settings) -> None:
    """Run the HMI server until cancelled. Belongs in `server.main`'s TaskGroup.

    `access_log=False`: the screen reconnects on every reload and holds an open
    WebSocket for as long as it is watched, and the plant's container log is already
    budgeted at 30 MB (docs/ENGINEERING.md §7). A line per request buys nothing that
    `/health` does not answer directly.

    One process-wide effect worth stating, because nothing in `server.main` would
    otherwise say so: uvicorn's `Server.serve()` installs its own SIGINT/SIGTERM
    handlers for its duration and re-raises the captured signal once it has closed its
    socket. `docker stop` therefore still ends this process -- it closes the screen's
    socket first.
    """
    config = uvicorn.Config(
        build_app(line, clock, settings),
        host=settings.hmi_server_host,
        port=settings.hmi_server_port,
        log_level="warning",
        access_log=False,
    )
    await uvicorn.Server(config).serve()
