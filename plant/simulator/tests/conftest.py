from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from asyncua import Server
from simulator.address_space import BUFFERS, STATION_SIGNALS
from simulator.buffers import Buffer
from simulator.carriers import Carrier, CarrierPool
from simulator.clock import SimulatedClock
from simulator.config import ClockConfig, Settings
from simulator.events import EventType
from simulator.identity import LotSchedule
from simulator.inspection_client import DEFECT_CLASSES
from simulator.line import BRING_UP_TRANSITIONS, Line, PartState
from simulator.packml import State
from simulator.stations import (
    FeedingStation,
    InspectionStation,
    JoiningStation,
    OutfeedStation,
    PartOutcome,
    Station,
)

T0 = datetime(2026, 9, 13, 6, 0, tzinfo=UTC)

STATION_CODES: tuple[str, ...] = tuple(STATION_SIGNALS)
"""§4.1's browse names, in line order, read from the tree rather than restated. That
equality is what `Settings.station_takt_seconds` is keyed by and what `Line` checks
each buffer's own upstream/downstream against."""
TRANSITION = timedelta(seconds=Settings().state_transition_seconds)
"""The configured spacing between two published PackML states, read from Settings
rather than restated: a test line whose transitions land on top of each other would
not be exercising what the plant does."""


def new_server() -> Server:
    """Binds an OS-assigned ephemeral loopback port instead of asyncua's 4840
    default. Once the plant container exists (Task 6), anything with that stack up
    already owns 4840, and every asyncua-backed test would fail on
    OSError: [Errno 98] Address already in use for a reason unrelated to whatever
    changed."""
    server = Server()
    server.set_endpoint("opc.tcp://127.0.0.1:0/plant")
    return server


@dataclass
class FakeStation:
    """Records what the Line asked it to do. Not a second implementation of
    StationCycle in the sense CLAUDE.md forbids -- a test double.

    Here rather than in one test module because two of them drive a Line: the queue's
    own tests, and the status snapshot, which reports what the line is doing.
    """

    code: str
    takt: float = 6.0
    cycles: list[tuple[datetime, int]] = field(default_factory=list)
    states: list[tuple[datetime, State, str]] = field(default_factory=list)

    async def run_cycle(self, at: datetime, carrier: Carrier, part: PartState) -> None:
        # Stamping the part is what a real station does with it -- S3 writes the
        # disposition and S4 reads it -- and it is what proves the Line handed over a
        # PartState rather than None.
        part.disposition = self.code
        self.cycles.append((at, carrier.carrier_id))

    async def publish_state(self, at: datetime, state: State, reason: str) -> None:
        self.states.append((at, state, reason))

    def next_takt(self) -> float:
        return self.takt


def fake_line(carriers: int = 12, capacity: int = 5) -> tuple[Line, list[FakeStation]]:
    """Four fake stations and three buffers, neither started nor seeded. The buffer
    names match the station codes, which `Line` refuses to run without."""
    stations = [FakeStation(code) for code in ("S1", "S2", "S3", "S4")]
    buffers = [
        Buffer("B1_2", capacity, "S1", "S2"),
        Buffer("B2_3", capacity, "S2", "S3"),
        Buffer("B3_4", capacity, "S3", "S4"),
    ]
    line = Line(
        stations=stations,
        buffers=buffers,
        carriers=CarrierPool(carriers),
        transition_interval=TRANSITION,
    )
    return line, stations


async def build_fake_line(
    carriers: int = 12, capacity: int = 5
) -> tuple[Line, list[FakeStation]]:
    """A fake line brought up and seeded at T0 -- what a caller that wants a *running*
    line needs, which is all of them but the tests about starting one.

    Async because starting a line publishes six PackML transitions per station; a line
    that is only constructed is Aborted and cycles nothing.
    """
    line, stations = fake_line(carriers, capacity)
    await line.bring_up(T0 - BRING_UP_TRANSITIONS * TRANSITION)
    line.seed(T0)
    return line, stations


class RecordingNodes:
    """Stands in for StationNodes. Records (signal, timestamp, value) per write and
    (event type, timestamp, fields) per event triggered.

    Here rather than in one test module because three of them need it: the stations'
    own tests, the HMI snapshot and Task 12's propagation proof, which both drive the
    *real* four stations and so need somewhere for their writes to go.

    Historised and live-only writes land in separate lists, the way the real address
    space keeps them in separate dicts -- a double that merged them would let a station
    publish a live-only value as a historised stream, which is the one thing D12 exists
    to prevent and exactly the shape of mistake a permissive double cannot see. The
    field-set check on `trigger_event` is here for the same reason: `StationNodeSet`
    raises on a payload that is not the event type's, and a double that accepted
    anything would let a station ship a field set the real tree refuses.
    """

    def __init__(self, code: str) -> None:
        self.code = code
        self.writes: list[tuple[str, datetime, float | str]] = []
        self.live_writes: list[tuple[str, datetime, float | str]] = []
        self.events: list[tuple[str, datetime, dict[str, object]]] = []

    async def write(self, signal: str, at: datetime, value: float | str) -> None:
        self.writes.append((signal, at, value))

    async def write_live(self, signal: str, at: datetime, value: float | str) -> None:
        self.live_writes.append((signal, at, value))

    async def trigger_event(
        self, event: EventType, at: datetime, fields: dict[str, object]
    ) -> None:
        if event.station != self.code:
            raise ValueError(f"{self.code} does not emit {event.name}")
        if set(fields) != set(event.field_names):
            raise ValueError(
                f"{event.name} fields {sorted(fields)} are not "
                f"{sorted(event.field_names)}"
            )
        self.events.append((event.name, at, fields))

    def signals(self) -> set[str]:
        return {signal for signal, _, _ in self.writes}

    def live_signals(self) -> set[str]:
        return {signal for signal, _, _ in self.live_writes}

    def payloads(self, event: EventType) -> list[dict[str, object]]:
        """Every payload triggered for one event type, in the order it was fired."""
        return [fields for name, _, fields in self.events if name == event.name]


async def _stub_produce(serial: str, _at: datetime) -> PartOutcome:
    """S3's inspection call, with no inspection service in reach.

    Deterministic rather than drawn: a proof that failed one run in twenty would be
    worse than no proof. Roughly a fifth of parts reject, which is far above §3.5's
    real rate and is the point -- it puts both dispositions in every short run.
    """
    reject = serial.endswith(("3", "7"))
    return PartOutcome(
        disposition="reject" if reject else "good",
        defect_class="gap" if reject else None,
        defect_classes=tuple(DEFECT_CLASSES),
        # §3.4's six independent scores: one class high on a reject, all six low on a
        # good part, and never summing to 1.
        confidences=tuple(
            0.87 if reject and name == "gap" else 0.04 for name in DEFECT_CLASSES
        ),
        confidence=0.93,
        image=b"PNG" if reject else None,
        model_version="test-1",
    )


async def build_running_line(
    settings: Settings | None = None,
) -> tuple[Line, SimulatedClock, dict[str, RecordingNodes]]:
    """A four-station line with recording node sets, built the way `server.build_line`
    builds the real one -- same station classes, same buffer capacities, same carrier
    count -- then brought up and seeded so that it actually runs.

    Async, and brought up, for the same reason `build_fake_line` is: a Line that is
    only constructed is `Aborted` in every station and cycles nothing, so a caller that
    stepped it would be proving things about a line that had never started. `bring_up`
    is placed before `history_start` exactly as `line.run_catchup` places it.

    Returns the clock too, because the HMI snapshot reports it and Task 12's
    propagation proof stamps its assertions with it, and the node sets, because the
    events every station now triggers land there and a caller tracing one serial from
    S1 to S4 has nowhere else to read them.

    A one-hour history depth rather than the shipped 33 h: nothing here generates
    history, and the depth only decides where `history_start` sits.
    """
    settings = settings or Settings()
    clock = SimulatedClock(
        ClockConfig(
            history_depth=timedelta(hours=1),
            catchup_speed=settings.catchup_speed,
        )
    )
    nodes = {code: RecordingNodes(code) for code in STATION_CODES}
    stations: list[Station] = [
        FeedingStation(
            nodes["S1_Feeding"],
            settings,
            settings.seed,
            LotSchedule(settings, clock.history_start),
        ),
        JoiningStation(nodes["S2_Joining"], settings, settings.seed),
        InspectionStation(
            nodes["S3_Inspection"], settings, settings.seed, _stub_produce
        ),
        OutfeedStation(nodes["S4_Outfeed"], settings, settings.seed),
    ]
    buffers = [
        Buffer(buffer_id, settings.buffer_capacity, upstream, downstream)
        for buffer_id, upstream, downstream in BUFFERS
    ]
    transition = timedelta(seconds=settings.state_transition_seconds)
    line = Line(
        stations,
        buffers,
        CarrierPool(settings.carrier_count),
        transition,
    )
    await line.bring_up(clock.history_start - BRING_UP_TRANSITIONS * transition)
    line.seed(clock.history_start)
    return line, clock, nodes
