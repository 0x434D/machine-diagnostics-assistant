"""What every station does identically: keep its own RNG, jitter its takt, write
TaktTime, count the parts it has handled, and publish PackML state with its reason.

`StationNodes` is the seam. A station never touches asyncua directly -- it names a
signal and a value, and the address space decides which node that is. That is what
lets the four stations be tested without a server, and it is what keeps the
`SourceTimestamp` suppression -- a `ua.DateTime` binds as an unsupported sqlite
parameter type and every row then vanishes with no visible error
(`address_space.write_at` carries the full account) -- in one place instead of four.
"""

from __future__ import annotations

import random
import zlib
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar, Protocol

from simulator.carriers import Carrier
from simulator.config import Settings
from simulator.events import EventType
from simulator.faults import NO_FAULTS, FaultSet
from simulator.identity import Assembly
from simulator.line import PartState
from simulator.noise import NoiseFloor
from simulator.packml import State


@dataclass(frozen=True)
class PartOutcome:
    """One part's inspection verdict, as the vision system itself reported it."""

    disposition: str  # "good" | "reject"
    # The classifier's named reason for a reject, None for a good part. Not on the
    # inspection event -- §5.2's widened `inspection_results` has no scalar class --
    # but it is what `PartCompletedEvent.Reason` carries into `part_dispositions`.
    defect_class: str | None
    # §3.4's six independent per-class scores and the names they belong to, as parallel
    # tuples. Both, rather than the scores alone: the defect vocabulary already exists
    # twice in this repository because the two uv workspaces may not import each other,
    # and a vector whose key is agreed privately somewhere else is a vector that gets
    # decoded against the wrong names with nothing raised. Paired here so the two cannot
    # be re-derived apart downstream.
    defect_classes: tuple[str, ...]
    confidences: tuple[float, ...]
    # §3.4: confidence in the OK/NOK *verdict*, never in a class. A good part is
    # confidently good while every entry in `confidences` scores low; reading the
    # vector as a distribution is the measured defect that reported a good part as
    # 27 % confident and ~30 % misaligned.
    confidence: float
    image: bytes | None  # §3.4: only rejects carry their image
    # The classifier's own advertised version -- not settings.model_version, which
    # only ever describes the simulator's *expected* model. Once a real
    # ModelClassifier replaces SimulatedClassifier, this field is what still
    # correctly attributes the verdict.
    model_version: str


ProduceFn = Callable[[str, int, datetime], Awaitable[PartOutcome]]
"""`(assembly serial, carrier id, simulated instant) -> the vision system's verdict`.

The carrier is here because §3.5's scenario 4 wears one and the plant's *truth* about a
part therefore depends on which carrier it rode -- so the id has to reach the draw, not
only the event S3 publishes afterwards. M2b passed a placeholder and said so.
"""


class StationNodes(Protocol):
    """The address-space handles one station writes through."""

    @property
    def code(self) -> str: ...

    # `float` covers int under PEP 484's numeric tower, so PartCount and GoodCount
    # pass as they are; Task 6's address space is what maps a signal to its OPC UA
    # variant type, which is where an integer counter becomes a UInt32.
    async def write(self, signal: str, at: datetime, value: float | str) -> None: ...

    # D12's live-only variables. A separate method rather than a flag on `write`,
    # because the ledger counts one of them and must not count the other.
    async def write_live(
        self, signal: str, at: datetime, value: float | str
    ) -> None: ...

    async def trigger_event(
        self, event: EventType, at: datetime, fields: dict[str, object]
    ) -> None: ...


def require_assembly(part: PartState, carrier: Carrier, station: str) -> Assembly:
    """The assembly S1 created on this carrier. Raises ValueError if there is none.

    Shared by the three stations downstream of S1 rather than written three times,
    and raising rather than substituting a placeholder for the same reason S4 already
    refuses a part with no disposition: a part nobody can name cannot be pressed
    against its serial, inspected against it or sorted by it, and publishing an event
    under a made-up or empty serial is the quiet wrong answer §14's traceability line
    exists to rule out.
    """
    if part.assembly is None:
        raise ValueError(
            f"the part on carrier {carrier.carrier_id} reached {station} with no "
            "assembly: S1 did not create one, and an event published against a serial "
            "nobody issued is a traceability record that points at nothing"
        )
    return part.assembly


def clamp_level(value: float) -> float:
    """A fill level and its sensor cannot read below empty.

    Both lane fill and outfeed fill are a sawtooth plus measurement noise, and both
    pass within one noise sigma of zero at the wrap, where the noise alone would
    otherwise put a negative number on the wire. Shared rather than written twice so
    the reason is stated once.
    """
    return max(0.0, value)


class Station(ABC):
    """Four implementations, in this package. That is what justifies the base class
    existing at all (CLAUDE.md)."""

    part_count_signal: ClassVar[str | None] = "PartCount"
    """Which §4.1 variable this station publishes its running part count as.

    Three of the four have a `PartCount`. S4 has none: it publishes the same count as
    `GoodCount` and `RejectCount`, which sum to it, and publishing both would be one
    number on the wire twice -- so it sets this to None and writes the pair itself,
    where the disposition deciding which of the two moves is in hand.

    Stated here rather than left implicit because it was implicit and wrong: this base
    wrote `PartCount` for all four, `StationNodeSet.write` raises KeyError for a signal
    §4.1 does not give a station, and nothing found it until Task 7 wired the real
    address space to the real stations -- the station tests write through a double that
    accepts any name, which is exactly the shape of test that cannot see this.
    """

    def __init__(
        self,
        nodes: StationNodes,
        settings: Settings,
        seed: int,
        *,
        faults: FaultSet = NO_FAULTS,
    ) -> None:
        """Raises ValueError if `settings` names no takt for this station's code.

        Checked here rather than at the first cycle so that a misconfigured
        deployment fails while the line is being built -- which is boot -- instead of
        one takt into generation, with a run already in flight.

        `faults` defaults to the empty set, so a station built without a scenario and a
        station built with one that has not fired are the same station -- see
        `faults.NO_FAULTS`.
        """
        if nodes.code not in settings.station_takt_seconds:
            raise ValueError(
                f"no takt configured for station {nodes.code!r}: "
                f"station_takt_seconds names {sorted(settings.station_takt_seconds)}. "
                "§4.1 fixes the set of stations, so an unnamed one is a "
                "misconfiguration, not a station to run at the line-wide default"
            )
        self._nodes = nodes
        self._settings = settings
        # Per-station RNG, derived from the master seed and the station code rather
        # than shared. §3.6's reproducibility must not depend on how many draws a
        # *different* station happened to make first.
        #
        # crc32 rather than hash(): str.__hash__ is salted per interpreter process
        # (PYTHONHASHSEED), so seeding from it would make the same seed produce a
        # different run on every boot -- the exact guarantee §3.6 asks for, broken
        # invisibly. crc32 is stable across processes, platforms and releases.
        self._rng = random.Random(seed ^ zlib.crc32(nodes.code.encode()))
        self._faults = faults
        # §3.5's permanent background. Built here from the same (settings, seed) every
        # station has rather than threaded in: every draw it makes is a pure function of
        # the seed and its key, so two NoiseFloors built the same way are the same
        # noise floor and there is nothing for a shared instance to buy.
        self._noise = NoiseFloor(settings, seed)
        self._previous_takt: float | None = None
        self._takt_draws = 0
        self._part_count = 0

    @property
    def code(self) -> str:
        return self._nodes.code

    def _nominal_takt(self) -> float:
        """Per station, not one line-wide number (§3.1).

        Raises KeyError for a station the configuration does not name -- __init__
        has already refused that case, so reaching it means the settings changed
        underneath a running line.

        This deliberately does *not* fail open to `takt_seconds`. The station set is
        **closed**: §4.1 fixes four stations and `address_space.STATION_SIGNALS`
        enumerates them, so a missing key is an operator error rather than an
        expected case. (Task 9's signal policy does fail open, and correctly -- its
        signal set is open and discovered, where losing an unknown stream is the
        worse failure. The two are not the same situation.) Falling back here is
        silent and produces a perfectly balanced line: every buffer oscillates
        between empty and one, buffer capacity bounds nothing, and §3.1's whole
        propagation claim quietly stops being true. `line.Line` already keys its
        state machines on this same code space with a raising lookup.
        """
        return self._settings.station_takt_seconds[self.code]

    def next_takt(self) -> float:
        """This station's nominal takt, jittered, plus whatever a micro-stop adds.

        **D13: the resample-until-distinct guard is gone.** It existed because M1's
        takt was a constant and asyncua's monitored-item filter
        (DataChangeTrigger.StatusValue, the default) drops a notification whose value is
        unchanged regardless of SourceTimestamp -- so a constant takt was historised
        exactly once per run. It then survived as a claim about reconciliation: it made
        the historian's row count equal the ledger's *by construction* rather than by
        the odds of two float64 draws colliding.

        That claim was never the guard's to make. `historian.Ledger.record` applies
        asyncua's own rule -- it counts a row only where the value changed -- so a
        repeated takt is dropped on both sides and the two still agree exactly. The
        guard bought the ledger nothing, and with §3.5's micro-stops added to a jittered
        Gaussian on a Double node it was dead code pretending to be a safety property.
        `test_every_stream_reconciles_exactly` is what says so, at the production depth.

        The micro-stop is added here rather than driven through PackML because a jam
        that needed an operator would be a `Held` and a §3.3 cause candidate -- see
        `noise.NoiseFloor.micro_stop_seconds`. It lands in `TaktTime`, which is what a
        brief jam looks like on the wire.
        """
        value = (
            self._nominal_takt()
            + self._rng.gauss(0.0, self._settings.takt_jitter_sigma)
            + self._noise.micro_stop_seconds(self.code, self._takt_draws)
        )
        # The micro-stop stream is keyed on this counter rather than on the part count,
        # because a suspended station still takes a takt and can still jam; the two
        # diverge by every cycle that produced nothing.
        self._takt_draws += 1
        self._previous_takt = value
        return value

    async def publish_state(self, at: datetime, state: State, reason: str) -> None:
        """§4.1's State and StateReason. Written together and always in this order, so
        a reader that sees Suspended can never read a reason belonging to the state
        before it."""
        await self._nodes.write("State", at, state.value)
        await self._nodes.write("StateReason", at, reason)

    async def run_cycle(self, at: datetime, carrier: Carrier, part: PartState) -> None:
        self._part_count += 1
        # The value written for part i is the interval that will elapse before part
        # i+1, not the interval since part i-1. A query asking "what was the takt at
        # instant T" should look at the next row after T. The reverse convention is
        # equally self-consistent; this is the one this project made.
        #
        # `is None` rather than `or`: _previous_takt is a float, and `or` would read a
        # legitimate draw of 0.0 as "never drawn" and report nominal instead -- a takt
        # the station did not run at, written as if it had.
        #
        # This reads the takt rather than being handed it, so it is the *current*
        # cycle's interval only because Line.step calls next_takt() before run_cycle().
        # Swap those two calls and every station silently writes the previous cycle's
        # interval instead -- no test fails, and the error is one row's offset in a
        # signal nothing cross-checks. Either keep the order or pass the takt in.
        await self._nodes.write(
            "TaktTime",
            at,
            self._nominal_takt()
            if self._previous_takt is None
            else self._previous_takt,
        )
        if self.part_count_signal is not None:
            await self._nodes.write(self.part_count_signal, at, self._part_count)
        await self.on_part(at, carrier, part)

    @abstractmethod
    async def on_part(self, at: datetime, carrier: Carrier, part: PartState) -> None:
        """What this station does to the part in front of it."""
