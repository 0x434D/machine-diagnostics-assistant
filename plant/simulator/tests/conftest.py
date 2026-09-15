from __future__ import annotations

import io
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx
from asyncua import Server
from PIL import Image, ImageStat
from simulator.address_space import BUFFERS, STATION_SIGNALS
from simulator.alarms import AlarmSystem
from simulator.buffers import Buffer
from simulator.carriers import Carrier, CarrierPool
from simulator.clock import SimulatedClock
from simulator.config import ClockConfig, Settings
from simulator.events import EventType
from simulator.faults import NO_FAULTS, FaultSet
from simulator.identity import LotSchedule
from simulator.inspection_client import DEFECT_CLASSES, InspectionClient
from simulator.line import BRING_UP_TRANSITIONS, Line, PartState
from simulator.packml import State
from simulator.scenarios import Consequence, Scenario, scenario
from simulator.stations import (
    FeedingStation,
    InspectionStation,
    JoiningStation,
    OutfeedStation,
    PartOutcome,
    ProduceFn,
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


def new_clock(settings: Settings) -> SimulatedClock:
    """A clock a test line can be started on, at a one-hour depth: nothing here
    generates history, and the depth only decides where `history_start` sits -- which is
    the origin a scenario's offsets are measured from."""
    return SimulatedClock(
        ClockConfig(
            history_depth=timedelta(hours=1),
            catchup_speed=settings.catchup_speed,
        )
    )


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

    def external_reserve(self, _at: datetime) -> float | None:
        """A fake station has no feed or discharge outside the line, so the Line's
        outer-end gate never fires on one -- these lines test the buffer rule."""
        return None


def fake_line(carriers: int = 12, capacity: int = 5) -> tuple[Line, list[FakeStation]]:
    """Four fake stations and three buffers, neither started nor seeded. The buffer
    names match the station codes, which `Line` refuses to run without.

    The alarm system it carries is real and gets real node doubles, though nothing here
    measures anything for it to raise on: a fake line built with an alarm system that
    could not publish would be a line on which an alarm raised by accident would go
    missing instead of failing.
    """
    stations = [FakeStation(code) for code in ("S1", "S2", "S3", "S4")]
    buffers = [
        Buffer("B1_2", capacity, "S1", "S2"),
        Buffer("B2_3", capacity, "S2", "S3"),
        Buffer("B3_4", capacity, "S3", "S4"),
    ]
    settings = Settings()
    line = Line(
        stations=stations,
        buffers=buffers,
        carriers=CarrierPool(carriers),
        transition_interval=TRANSITION,
        alarms=AlarmSystem(
            settings,
            settings.seed,
            {station.code: RecordingNodes(station.code) for station in stations},
        ),
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
        if self.code not in event.stations:
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


async def _stub_produce(
    serial: str, _carrier_id: int, _joining_work: float, _at: datetime
) -> PartOutcome:
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
    *,
    faults: FaultSet = NO_FAULTS,
    produce: ProduceFn = _stub_produce,
    clock: SimulatedClock | None = None,
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

    `clock` is a parameter for one reason, and it is the scenarios': a `FaultSet` has to
    be built on the origin the line will actually start at, so a caller that needs both
    has to make the clock first. Left out, one is made here.
    """
    settings = settings or Settings()
    clock = clock or new_clock(settings)
    nodes = {code: RecordingNodes(code) for code in STATION_CODES}
    # The real alarm system on the real node sets, because a test line whose press could
    # not report itself out of tolerance would be a different line from the one the plant
    # runs -- and §3.5 row 3's abort is exactly the behaviour a scenario test has to see.
    # Reachable afterwards as `line.alarms`.
    alarms = AlarmSystem(settings, settings.seed, nodes)
    stations: list[Station] = [
        FeedingStation(
            nodes["S1_Feeding"],
            settings,
            settings.seed,
            LotSchedule(settings, clock.history_start),
            faults=faults,
        ),
        JoiningStation(
            nodes["S2_Joining"], settings, settings.seed, alarms, faults=faults
        ),
        InspectionStation(
            nodes["S3_Inspection"], settings, settings.seed, produce, faults=faults
        ),
        OutfeedStation(nodes["S4_Outfeed"], settings, settings.seed, faults=faults),
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
        alarms,
    )
    await line.bring_up(clock.history_start - BRING_UP_TRANSITIONS * transition)
    line.seed(clock.history_start)
    return line, clock, nodes


# --- running a scenario, and reading what the line published --------------------------
#
# Here rather than in one test module because two of them run whole scenarios: the
# consequence tests that measure what §3.5's rows produce, and the proofs that assert
# those same consequences out of §5.2's tables. A second copy of the run loop is a second
# thing to keep in step with `Line.step`, and the two would drift on the first change to
# how a state change is noticed.


@dataclass(frozen=True)
class Part:
    """One part as the plant declared it: when it was inspected, what it rode, what the
    press left in it, and what it genuinely carries."""

    at: datetime
    serial: str
    carrier_id: int
    joining_work: float
    defects: frozenset[str]


@dataclass(frozen=True)
class Change:
    at: datetime
    station: str
    state: State
    reason: str


@dataclass(frozen=True)
class Run:
    origin: datetime
    parts: tuple[Part, ...]
    changes: tuple[Change, ...]
    nodes: dict[str, RecordingNodes]
    alarms: AlarmSystem
    """The line's own alarm system, as the run left it. §3.5 row 3 ends in an alarm and
    a shutdown, and neither is recoverable from the parts or the state changes alone --
    an `Aborted` row says a station shut down and not what it shut down for."""

    def elapsed(self, at: datetime) -> float:
        return (at - self.origin).total_seconds()

    def within(self, lo: float, hi: float) -> tuple[Part, ...]:
        return tuple(p for p in self.parts if lo <= self.elapsed(p.at) < hi)

    def carrying(self, defect: str) -> frozenset[str]:
        """The serials that genuinely carry `defect`. Serials rather than counts,
        because the sharp comparison between two runs is which parts changed."""
        return frozenset(p.serial for p in self.parts if defect in p.defects)

    def stream(self, station: str, signal: str) -> tuple[tuple[float, float], ...]:
        """`(elapsed seconds, value)` for one historised stream."""
        return tuple(
            (self.elapsed(at), float(value))
            for name, at, value in self.nodes[station].writes
            if name == signal
        )

    def settled(self, station: str) -> Change:
        """The last state change this station made -- the condition it ended the run in,
        and when it entered it.

        **The last and not the first, and that is the whole difference between asserting
        a chain and asserting the noise floor.** A running line suspends on its own
        buffers a few percent of the time, so "the first time S2 starved" is answered by
        an ordinary micro-stop somewhere in the warmup. A propagation chain is the one
        that does not clear: each station stops and stays stopped, and the instants they
        stopped at are the chain in order.

        Raises AssertionError if the station never changed state, which means the run
        never started it.
        """
        for change in reversed(self.changes):
            if change.station == station:
                return change
        raise AssertionError(f"{station} never changed state in this run")


def truth_outcome(defects: Sequence[str]) -> PartOutcome:
    """A verdict built from what the part genuinely carries.

    **The plant's own truth rather than the inspection service's**, and the reason is the
    line M2c must not cross: §3.5's scenarios are about what the plant did to the parts,
    and putting the classifier's own two error rates (D7) in front of every measurement
    would mean asserting the plant's behaviour through something else's noise. The
    classifier is exercised where it belongs -- in the inspection package's own suite,
    and in scenario 6's proof, which is about the classifier reading an image.
    """
    return PartOutcome(
        disposition="reject" if defects else "good",
        defect_class=defects[0] if defects else None,
        defect_classes=tuple(DEFECT_CLASSES),
        confidences=tuple(0.8 if name in defects else 0.03 for name in DEFECT_CLASSES),
        confidence=0.9,
        image=b"PNG" if defects else None,
        model_version="test-1",
    )


ProduceFactory = Callable[[InspectionClient], ProduceFn]
"""How a caller replaces the verdict above with one that goes through the real client.

A factory rather than a `ProduceFn`, because the client is built inside `run_line` (it
needs the run's own `FaultSet`) and `ground_truth.recording` wraps that client.
"""


async def run_line(
    seconds: float,
    *,
    faults: FaultSet = NO_FAULTS,
    settings: Settings | None = None,
    clock: SimulatedClock | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    produce_with: ProduceFactory | None = None,
) -> Run:
    """Run a real four-station line for `seconds` of simulated time.

    The verdict is `truth_outcome`'s unless `produce_with` supplies one; either way the
    `Part` this returns carries the plant's own truth, so a caller that swapped in the
    real client still measures the plant rather than the classifier.
    """
    settings = settings or Settings()
    clock = clock or new_clock(settings)
    parts: list[Part] = []
    changes: list[Change] = []

    async with httpx.AsyncClient(transport=transport) as http:
        client = InspectionClient(settings, http, faults=faults)
        inner = None if produce_with is None else produce_with(client)

        async def produce(
            part_id: str, carrier_id: int, joining_work: float, at: datetime
        ) -> PartOutcome:
            defects = client.truth_for(part_id, carrier_id, joining_work, at)
            parts.append(
                Part(at, part_id, carrier_id, joining_work, frozenset(defects))
            )
            if inner is not None:
                return await inner(part_id, carrier_id, joining_work, at)
            return truth_outcome(defects)

        line, clock, nodes = await build_running_line(
            settings, faults=faults, produce=produce, clock=clock
        )
        horizon = clock.history_start + timedelta(seconds=seconds)
        while (due := line.next_due) is not None and due < horizon:
            before = dict(line.station_states)
            outcome = await line.step()
            assert outcome is not None
            for code, (state, reason) in line.station_states.items():
                if before[code] != (state, reason):
                    changes.append(Change(outcome.at, code, state, reason))

    return Run(clock.history_start, tuple(parts), tuple(changes), nodes, line.alarms)


async def paired_runs(
    number: int,
    seconds: float,
    settings: Settings | None = None,
    *,
    clock: SimulatedClock | None = None,
    produce_with: ProduceFactory | None = None,
    clean_produce_with: ProduceFactory | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[Run, Run]:
    """The same run with the scenario and without it, on one origin, clean first.

    **The comparison this file rests on.** A fault moves a threshold and never a draw
    (`faults`' identity property), so the two runs make the same draws about the same
    parts at the same instants -- and the difference between them is the scenario and
    nothing else. A rate measured against its own before-window carries the sampling
    noise of two small windows; the difference between these two carries none.

    The two `produce_with` hooks are separate because a caller writing a ground-truth log
    writes one per run, and handing both runs the same one would interleave two runs'
    parts into a single log.

    `clock` is a parameter because `SimulatedClock.history_start` is `boot - depth` off
    the wall clock: a caller that built one to place a scenario's offsets on and let this
    build another would have the two a few microseconds apart, and every offset measured
    against the first would be measured against a run that started at the second.
    """
    settings = settings or Settings()
    clock = clock or new_clock(settings)
    injected = scenario(number, settings).fault_set(clock.history_start)
    return (
        await run_line(
            seconds,
            settings=settings,
            clock=clock,
            transport=transport,
            produce_with=clean_produce_with,
        ),
        await run_line(
            seconds,
            faults=injected,
            settings=settings,
            clock=clock,
            transport=transport,
            produce_with=produce_with,
        ),
    )


async def scenario_run(
    number: int,
    seconds: float,
    settings: Settings,
    *,
    clock: SimulatedClock | None = None,
    produce_with: ProduceFactory | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Run:
    """One run carrying scenario `number`, on an origin its offsets are placed on.

    `clock` for the reason `paired_runs` takes one: the origin is wall-clock derived, so
    a caller that needs the run and something else to agree on it has to make it once.
    """
    clock = clock or new_clock(settings)
    return await run_line(
        seconds,
        faults=scenario(number, settings).fault_set(clock.history_start),
        settings=settings,
        clock=clock,
        transport=transport,
        produce_with=produce_with,
    )


def consequence_claiming(item: Scenario, expect: str) -> Consequence:
    """The one consequence of `item` that claims `expect`.

    Raises AssertionError if the scenario claims it more than once or not at all, which
    is what makes this a lookup rather than a search: a test written against a claim the
    log no longer makes has to fail rather than quietly assert a neighbour.
    """
    found = [
        consequence
        for injection in item.injections
        for consequence in injection.consequences
        if consequence.expect == expect
    ]
    assert len(found) == 1, f"scenario {item.number} claims {expect} {len(found)} times"
    return found[0]


def fault_window(item: Scenario) -> tuple[float, float | None]:
    """The single injection's fault window, as `(at, until)` seconds.

    Every one of §3.5's eight injects exactly one fault today. The assertion is what
    makes that visible rather than assumed: a scenario that grew a second injection
    would otherwise be asserted against its first one's window in silence.
    """
    assert len(item.injections) == 1
    fault = item.injections[0].fault
    return (
        fault.at.total_seconds(),
        None if fault.until is None else fault.until.total_seconds(),
    )


def rms_contrast(frame: bytes) -> float:
    """The simulator's own reading of `inspection.classifier.contrast_of`.

    Duplicated for the reason `simulator.render` duplicates `inspection.render`: the two
    packages are separate uv workspace members and may not import each other (§10.7).
    Three lines, and the inspection suite is what pins the copy that matters.
    """
    with Image.open(io.BytesIO(frame)) as image:
        return float(ImageStat.Stat(image.convert("L")).stddev[0])
