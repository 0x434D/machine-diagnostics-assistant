from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from asyncua import Server
from simulator.buffers import Buffer
from simulator.carriers import Carrier, CarrierPool
from simulator.line import Line, PartState
from simulator.packml import State

T0 = datetime(2026, 9, 13, 6, 0, tzinfo=UTC)


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


def build_fake_line(
    carriers: int = 12, capacity: int = 5
) -> tuple[Line, list[FakeStation]]:
    """Four fake stations, seeded at T0. The buffer names match the station codes,
    which `Line` refuses to run without."""
    stations = [FakeStation(code) for code in ("S1", "S2", "S3", "S4")]
    buffers = [
        Buffer("B1_2", capacity, "S1", "S2"),
        Buffer("B2_3", capacity, "S2", "S3"),
        Buffer("B3_4", capacity, "S3", "S4"),
    ]
    line = Line(stations=stations, buffers=buffers, carriers=CarrierPool(carriers))
    line.seed(T0)
    return line, stations
