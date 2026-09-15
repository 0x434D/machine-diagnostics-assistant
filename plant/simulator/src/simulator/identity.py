"""§3.1's asymmetric identities: components belong to lots, assemblies to themselves.

A component is individually serialised and carries the supplier lot it came from; the
assembly gets its own serial, created here when the carrier is loaded at S1. The
asymmetry is what buys both answers at once -- lot-level containment ("which parts
contain lot L-4471") survives, and exact as-built genealogy ("which assembly holds
component C-1-00004471") is gained on top of it.

Pure logic: no clock, no I/O. Simulated time arrives as an argument, which is what
lets a containment question be asked of this module without a line running.
"""

from __future__ import annotations

import random
import zlib
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Final

from simulator.config import Settings

LANES: Final[tuple[int, ...]] = (1, 2)
"""§4.1's two feeder lanes, in the order S1 loads them.

A closed set like the station codes rather than a setting: a lane is two nodes in the
address space (`LaneFill_n` and `Lane{n}_Lot`, so four across the two) and a column in
§5.2, so a third lane is a tree change and a migration, not a number to turn up. Ordered, because a
component's position in `Assembly.components` is §5.2's `genealogy.position`.
"""

_LOT_NUMBER_CEILING: Final = 9999
"""The largest number `L-{n:04d}` can spell.

The *shape* is §3.5 scenario 7's `L-4471`; the codes themselves are not. Where the
sequence starts is derived from the seed, so at the shipped 20260912 a run issues
`L-2305` upwards and the literal `L-4471` is never reached at all. **A scenario must
name the lot it wants by lane and position in that lane's sequence -- `lots(lane)[0]`,
say -- and read the code back off it.** A scenario that hard-codes `L-4471` contaminates
a lot that does not exist, injects nothing, and scores zero against a containment list
it never touched.
"""


def component_serial(lane: int, index: int) -> str:
    """`C-{lane}-{index:08d}`.

    The lane is *in* the serial because containment questions start from a lane --
    §3.5 scenario 5 contaminates one lane and scenario 7 delivers a bad lot to one
    lane, and both are read back from the serials the lane produced.
    """
    return f"C-{lane}-{index:08d}"


def assembly_serial(index: int) -> str:
    """`A-{index:08d}`, M1's format unchanged.

    Kept rather than made symmetric with the component format: the gateway, the
    analysis service and §6.3's worked chat citation all resolve this spelling
    already, and a cosmetic change breaks a citation that works.
    """
    return f"A-{index:08d}"


@dataclass(frozen=True)
class Lot:
    """One supplier lot on one lane, and the window it was drawn from.

    `depleted_at` is None while the lot is the one in use, and is set to the instant
    the *next* lot was loaded -- the windows are contiguous and non-overlapping, so
    "which lot was this component from" has exactly one answer at every instant.
    """

    lot_code: str
    lane: int
    supplier: str
    loaded_at: datetime
    depleted_at: datetime | None = None


@dataclass(frozen=True)
class Component:
    serial: str
    lot_code: str
    lane: int
    read_at: datetime


@dataclass(frozen=True)
class Assembly:
    """One assembly and the components it was built from, as built.

    `components` is in `LANES` order, so a component's index in it is §5.2's
    `genealogy.position`.
    """

    serial: str
    carrier_id: int
    created_at: datetime
    components: tuple[Component, ...]


class LotSchedule:
    """Which lot each lane is drawing from, and which it drew from before.

    **Lot codes come from one counter shared by both lanes**, not one per lane. That
    is what makes "two lanes are never on the same lot" true by construction rather
    than by the odds of two independent draws avoiding each other -- and the two
    scenarios that turn on it are different scenarios only while it holds: §3.5
    scenario 7 contaminates one lane's *lot*, scenario 5 contaminates one *lane*, and
    a code that could appear on both collapses the pair.

    Lots advance on consumption, not on the clock: a lane moves to its next lot after
    `settings.lot_size` components, wherever in the shift that falls.
    """

    def __init__(self, settings: Settings, started_at: datetime) -> None:
        """Raises ValueError for a configuration that cannot produce a lot."""
        if settings.lot_size < 1:
            raise ValueError(
                f"lot_size must be at least 1, got {settings.lot_size!r}: a lot that "
                "supplies no components can never be depleted, so the schedule would "
                "advance forever at the first draw"
            )
        if settings.supplier_count < 1:
            raise ValueError(
                f"supplier_count must be at least 1, got {settings.supplier_count!r}"
            )
        self._settings = settings
        # crc32, never hash(): str.__hash__ is salted per interpreter process
        # (PYTHONHASHSEED), so a hash-derived seed is stable within one run and
        # different on the next boot -- §3.6's "a seed reproduces a run exactly",
        # broken invisibly. M2a shipped exactly this defect once.
        self._suppliers = random.Random(settings.seed ^ zlib.crc32(b"lot-suppliers"))
        # Seed-derived rather than fixed, so two runs with different seeds do not
        # issue each other's lot codes into the same history. The window leaves at
        # least 5,000 codes before `L-{n:04d}` is exhausted; one lot lasts lot_size
        # parts, 50 minutes at the configured 500 and a 6 s takt, so what remains is
        # thousands of hours of production either way.
        self._next_number = 1000 + (settings.seed ^ zlib.crc32(b"lot-codes")) % 4000
        self._lots: dict[int, list[Lot]] = {}
        self._drawn: dict[int, int] = {}
        self._read: dict[int, int] = {}
        self._last_draw: dict[int, datetime] = {}
        for index, lane in enumerate(LANES):
            self._lots[lane] = [self._load(lane, started_at)]
            # **Staggered, so the two lanes do not roll their lots on the same part.**
            # Both lanes supply every assembly, so left at zero they deplete together and
            # lane 1's k-th lot covers exactly the parts lane 2's k-th lot covers -- which
            # makes "which lot" and "which lane" one question, and collapses §3.5's
            # scenario 7 (one lane's lot) into scenario 5 (one lane). `Settings.
            # lot_stagger_fraction` carries the rest of the reasoning.
            #
            # A lane that starts part-way through its first lot is a lane that was already
            # running when the line started, so that lot supplies fewer than `lot_size`
            # components and every lot after it supplies exactly `lot_size`.
            self._drawn[lane] = self._stagger(index)
            self._read[lane] = 0

    def draw(self, lane: int, at: datetime) -> Component:
        """One component off `lane`'s current lot, rolling over when it is exhausted.

        Raises ValueError for a lane §4.1 does not have, and for a draw at or before
        this lane's previous one -- simulated time only moves forward per lane, and a
        lot window that ran backwards, or two components off one lane at one instant,
        would make a component's lot ambiguous at exactly the instant a containment
        query needs it. The two lanes do draw at the same instant, which is what
        `load_carrier` does.
        """
        self._check_lane(lane)
        previous = self._last_draw.get(lane)
        if previous is not None and at <= previous:
            raise ValueError(
                f"lane {lane} drew at {at}, at or before its previous draw at "
                f"{previous}: two components off one lane at one instant straddling a "
                "rollover belong to different lots, and `lot_at` can only return the "
                "second -- so the component's own record and the schedule disagree "
                "about exactly the fact a containment query asks for"
            )
        self._last_draw[lane] = at

        if self._drawn[lane] == self._settings.lot_size:
            history = self._lots[lane]
            history[-1] = replace(history[-1], depleted_at=at)
            history.append(self._load(lane, at))
            self._drawn[lane] = 0

        component = Component(
            serial=component_serial(lane, self._read[lane]),
            lot_code=self._lots[lane][-1].lot_code,
            lane=lane,
            read_at=at,
        )
        self._drawn[lane] += 1
        self._read[lane] += 1
        return component

    def current(self, lane: int) -> Lot:
        """The lot this lane is drawing from now -- §4.1's live-only `Lane{n}_Lot`."""
        self._check_lane(lane)
        return self._lots[lane][-1]

    def lots(self, lane: int) -> tuple[Lot, ...]:
        """Every lot this lane has drawn from, oldest first, the current one last.

        §5.2's `component_lots` rows for the lane, in the order they were loaded.
        """
        self._check_lane(lane)
        return tuple(self._lots[lane])

    def lot_at(self, lane: int, at: datetime) -> Lot:
        """The lot this lane was drawing from at `at`.

        Raises ValueError before the schedule started, rather than returning the first
        lot: a containment query answered for an instant the lot did not cover is a
        wrong answer that looks like a right one.
        """
        self._check_lane(lane)
        for lot in self._lots[lane]:
            if lot.loaded_at <= at and (
                lot.depleted_at is None or at < lot.depleted_at
            ):
                return lot
        raise ValueError(
            f"lane {lane} was drawing from no lot at {at}: the schedule starts at "
            f"{self._lots[lane][0].loaded_at}"
        )

    def _stagger(self, index: int) -> int:
        """How many components of its first lot lane `index` is treated as having
        already drawn. Zero for the first lane, so one lane's lots always start at the
        instant the line does and a scenario can name a window on it.

        Taken modulo `lot_size`, so a fraction of 1.0 or more is a full lot rather than a
        lane that starts already depleted -- `draw` would otherwise roll it over on its
        very first component, before any part had been built from it.
        """
        offset = round(
            index * self._settings.lot_size * self._settings.lot_stagger_fraction
        )
        return offset % self._settings.lot_size

    def _check_lane(self, lane: int) -> None:
        if lane not in self._lots:
            raise ValueError(f"§4.1 gives S1 lanes {LANES}, not {lane!r}")

    def _load(self, lane: int, at: datetime) -> Lot:
        if self._next_number > _LOT_NUMBER_CEILING:
            raise ValueError(
                f"lot codes past {_LOT_NUMBER_CEILING} do not fit "
                f"{self._settings.lot_code_prefix}{{n:04d}}; wrapping would reuse a "
                "code and silently merge two containment lists"
            )
        number = self._next_number
        self._next_number += 1
        supplier = self._suppliers.randint(1, self._settings.supplier_count)
        return Lot(
            lot_code=f"{self._settings.lot_code_prefix}{number:04d}",
            lane=lane,
            supplier=f"SUP-{supplier:02d}",
            loaded_at=at,
        )


def load_carrier(
    schedule: LotSchedule, index: int, carrier_id: int, at: datetime
) -> Assembly:
    """§3.1's assembly, created at S1 the moment the carrier is loaded.

    One component per lane, drawn in `LANES` order. Here rather than in S1 because it
    is the whole of what "the identities are created at S1" means, and it is testable
    without a station, a clock or a server.
    """
    return Assembly(
        serial=assembly_serial(index),
        carrier_id=carrier_id,
        created_at=at,
        components=tuple(schedule.draw(lane, at) for lane in LANES),
    )
