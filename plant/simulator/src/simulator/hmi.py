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
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Final, TypedDict

import uvicorn
from fastapi import FastAPI, Response, WebSocket, WebSocketDisconnect

from simulator.alarms import AlarmSystem
from simulator.clock import SimulatedClock
from simulator.config import Settings
from simulator.line import Line
from simulator.packml import State
from simulator.stations.base import PartOutcome, ProduceFn

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
    category decides the colour and ISA-101 forbids the colour being the only channel.

    `browse_name`, emphatically not `code`. The value is §4.1's browse name -- the plant's
    own identity for a station, `S1_Feeding` -- and the plant is right to emit it: it is
    what `StationNodes.code`, `Settings.station_takt_seconds` and `Line`'s buffer check all
    key on, and splitting it here would be the plant performing a split it never performs.
    But `code` is a taken word one stack over: `stations.code` in 001_m1.sql holds `S1`,
    produced by the gateway's `TopologyDiscovery.SplitBrowseName`. A consumer joining a
    field called `code` against that column gets zero rows, no error, and a screen that
    looks fine -- so the field says which of the two names it is carrying.
    """

    browse_name: str
    state: str
    category: str
    reason: str


class BufferView(TypedDict):
    """One buffer, with the capacity its level is a fraction of -- so the screen draws a
    fill bar without a copy of `Settings.buffer_capacity` of its own.

    `code` here IS the diagnostics stack's `buffers.code`: the gateway stores a buffer's
    browse name whole (`TopologyDiscovery.DiscoverBuffersAsync` passes `child.Name`
    through), because a buffer's name carries no function to split off. The two station
    references do not get the same treatment -- the gateway splits those down to `S1`
    before storing them as `upstream_station_id` -- so they are named for what they hold.
    """

    code: str
    level: int
    capacity: int
    upstream_browse_name: str
    downstream_browse_name: str


class PartView(TypedDict):
    """One part on §3.7's strip, newest first.

    `image_url` is a path on this server rather than the image itself. A reject's PNG is
    ~110 kB (R4's median at the shipped configuration), the strip holds up to
    `Settings.hmi_recent_parts` of them and a frame goes out every
    `hmi_interval_seconds` -- so inlining them would put megabytes on the socket every
    second for pictures the browser already has. `null` is a good part, which §3.4 says
    carries no image at all; it is a fact rather than a missing value.

    The path is relative to this server, and the screen is served from an origin that
    forwards `/api/plant/*` here, so the frontend is what joins the two. Same split as
    `useLineSnapshot`'s socket URL, for the same reason: the proxy prefix is the
    browser's business and this process does not know it.
    """

    serial: str
    at: str
    disposition: str
    reason: str
    image_url: str | None


class AlarmView(TypedDict):
    """One active alarm on §3.7's screen.

    `sequence` is the plant's own number for the alarm and is what the acknowledge
    button sends back -- deliberately not §5.2's `alarms.id`, which is the gateway's
    surrogate key and which this process never sees. Two numbers for one alarm would be
    an acknowledgement addressed to whichever of them the screen happened to hold.

    `acknowledged` travels beside `acked_at` for the reason `state` travels beside
    `category`: the screen has to be able to say "acknowledged, waiting on the operator"
    without parsing a timestamp to find out, and an empty string is not a falsy instant.
    """

    sequence: int
    station_browse_name: str
    code: str
    text: str
    severity: int
    raised_at: str
    acknowledged: bool
    acked_at: str | None


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
    parts: list[PartView]
    alarms: list[AlarmView]


@dataclass(frozen=True)
class InspectedPart:
    """One part as the vision system reported it, kept only for the screen.

    Not a `PartState`: that carries what the *line* needs to move a part along and
    nothing else, and the image in particular has no business riding a carrier. This is
    the screen's own record, and the only thing in the plant that keeps an image after
    the event carrying it has been published.
    """

    serial: str
    at: datetime
    disposition: str
    reason: str
    image: bytes | None


class RecentParts:
    """The last few parts the line inspected, for §3.7's strip.

    **Bounded, and that is the whole design.** A run generates ~19,800 parts and rebuilds
    33 h of history on every boot at 700x, so anything remembering all of them would grow
    without limit in a process that is otherwise flat. A `deque` with a `maxlen` drops the
    oldest record -- and the image it holds -- as the next one arrives.

    Fed by wrapping the inspection call rather than by a station writing here. S3 already
    has every field on this record, and the wrapper is applied where the line is composed,
    so no station gains a screen-shaped dependency and the four of them stay testable
    without one.
    """

    def __init__(self, keep: int) -> None:
        self._parts: deque[InspectedPart] = deque(maxlen=keep)

    def watching(self, produce: ProduceFn) -> ProduceFn:
        """`produce`, with every verdict it returns remembered as the strip's newest part."""

        async def remembering(
            part_id: str, carrier_id: int, joining_work: float, at: datetime
        ) -> PartOutcome:
            outcome = await produce(part_id, carrier_id, joining_work, at)
            self._parts.append(
                InspectedPart(
                    serial=part_id,
                    at=at,
                    disposition=outcome.disposition,
                    # The classifier's named reason for a reject, empty for a good part --
                    # the same convention `PartState.reason` carries to S4, so the strip
                    # and `part_dispositions` say the same word about the same part.
                    reason=outcome.defect_class or "",
                    image=outcome.image,
                )
            )
            return outcome

        return remembering

    def views(self) -> list[PartView]:
        """Newest first, which is the order a strip of the last parts is read in."""
        return [
            PartView(
                serial=part.serial,
                at=part.at.isoformat(),
                disposition=part.disposition,
                reason=part.reason,
                image_url=None if part.image is None else f"/parts/{part.serial}/image",
            )
            for part in reversed(self._parts)
        ]

    def image(self, serial: str) -> bytes | None:
        """The stored image for `serial`, or None once it has fallen off the strip.

        None covers three different things -- a good part, a serial this run never saw,
        and a reject the strip has forgotten -- and the caller answers all three the same
        way, because the screen only ever asks for an image the frame it is drawing says
        exists.
        """
        return next((part.image for part in self._parts if part.serial == serial), None)


def alarm_views(alarms: AlarmSystem) -> list[AlarmView]:
    """§3.7's active alarms, oldest first -- the order an operator works a panel in.

    Active only. A cleared alarm is history, and history is what the diagnostics stack
    answers from; a screen that listed them all would grow for the life of a run and put
    the alarm being worked on at the bottom.
    """
    return [
        AlarmView(
            sequence=alarm.sequence,
            station_browse_name=alarm.station,
            code=alarm.code.code,
            text=alarm.code.text,
            severity=alarm.code.severity,
            raised_at=alarm.raised_at.isoformat(),
            acknowledged=alarm.acknowledged,
            acked_at=None if alarm.acked_at is None else alarm.acked_at.isoformat(),
        )
        for alarm in alarms.active
    ]


def line_snapshot(
    line: Line, clock: SimulatedClock, recent: RecentParts
) -> LineSnapshot:
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
                browse_name=browse_name,
                state=state.value,
                category=category_for(state),
                reason=reason,
            )
            for browse_name, (state, reason) in line.station_states.items()
        ],
        "buffers": [
            BufferView(
                code=buffer.buffer_id,
                level=buffer.level,
                capacity=buffer.capacity,
                upstream_browse_name=buffer.upstream,
                downstream_browse_name=buffer.downstream,
            )
            for buffer in line.buffers
        ],
        "parts": recent.views(),
        "alarms": alarm_views(line.alarms),
    }


def build_app(
    line: Line, clock: SimulatedClock, settings: Settings, recent: RecentParts
) -> FastAPI:
    """The screen's four endpoints, reading the live `line`, `clock` and strip.

    `/snapshot` and `/ws` serve the same payload, so a frame the screen renders and one
    a human curls are the same object -- there is no second rendering to disagree.
    """
    app = FastAPI(title="plant HMI", docs_url=None, redoc_url=None)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    # response_class, so FastAPI does not wrap the bytes in JSON. Rejects only (§3.4),
    # and only while the part is still on the strip -- this is the screen's thumbnail
    # source and not an archive. The archive is `inspection_images` one stack over, and
    # it is reached through the diagnostics API, not through here.
    @app.get("/parts/{serial}/image", response_class=Response)
    async def part_image(serial: str) -> Response:
        image = recent.image(serial)
        if image is None:
            # 404 rather than an empty 200: the screen asks only for images a frame said
            # exist, so an empty body here would be a blank thumbnail with nothing
            # anywhere saying why.
            return Response(status_code=404)
        return Response(content=image, media_type="image/png")

    # response_model=None: FastAPI would otherwise build a pydantic model from the
    # annotation and re-validate every frame against it. `LineSnapshot` is the payload's
    # own definition and `tests/test_hmi.py` is what asserts it, so a second schema
    # derived from the first would cost a validation per frame and add no check.
    @app.get("/snapshot", response_model=None)
    async def snapshot() -> LineSnapshot:
        return line_snapshot(line, clock, recent)

    @app.websocket("/ws")
    async def stream(socket: WebSocket) -> None:
        await socket.accept()
        try:
            while True:
                # Sent before the first sleep, so a screen that has just connected
                # draws the line rather than an empty page for one interval.
                await socket.send_json(line_snapshot(line, clock, recent))
                await asyncio.sleep(settings.hmi_interval_seconds)
        except WebSocketDisconnect:
            # The one exception here with a real recovery: a closed tab is not a
            # failure of the plant, and this coroutine has nothing left to do.
            # Deliberately narrow -- `asyncio.CancelledError` is shutdown and is not
            # caught, so it still propagates out of the TaskGroup that owns this server.
            return

    return app


async def serve(
    line: Line, clock: SimulatedClock, settings: Settings, recent: RecentParts
) -> None:
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
        build_app(line, clock, settings, recent),
        host=settings.hmi_server_host,
        port=settings.hmi_server_port,
        log_level="warning",
        access_log=False,
    )
    await uvicorn.Server(config).serve()
