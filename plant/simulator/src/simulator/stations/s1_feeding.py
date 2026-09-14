"""S1 Feeding: separates parts from two feeder lanes onto a carrier (§3.1).

This is where every identity in the plant is created. One component is drawn off each
lane, the assembly that joins them gets its serial, and both facts go out as events at
the instant they happen -- §3.4a's rule, which is what makes the genealogy as-built
rather than reconstructed.
"""

from __future__ import annotations

from datetime import datetime
from typing import override

from simulator.carriers import Carrier
from simulator.config import Settings
from simulator.events import ASSEMBLY_CREATED, COMPONENT_READ
from simulator.faults import LANE_FILL, NO_FAULTS, FaultSet
from simulator.identity import LANES, LotSchedule, load_carrier
from simulator.line import PartState
from simulator.stations.base import Station, StationNodes, clamp_level


class FeedingStation(Station):
    def __init__(
        self,
        nodes: StationNodes,
        settings: Settings,
        seed: int,
        schedule: LotSchedule,
        *,
        faults: FaultSet = NO_FAULTS,
    ) -> None:
        """`schedule` is the lot schedule both lanes draw from.

        Injected rather than built here, for the reason S3's `produce` is: it needs the
        instant the line starts, which is the clock's and not this station's, and a
        containment question can then be asked of the schedule without a station.
        """
        super().__init__(nodes, settings, seed, faults=faults)
        self._schedule = schedule

    @override
    async def on_part(self, at: datetime, carrier: Carrier, part: PartState) -> None:
        # `_part_count` was incremented for this part before `on_part` ran, so the
        # first assembly is A-00000000. The index is S1's own count and not S3's:
        # M2a's serial came from the count at S3, which diverges from S1's by whatever
        # is in the buffers, and the whole of M2b is that one identity is created here
        # and carried rather than computed twice.
        assembly = load_carrier(
            self._schedule, self._part_count - 1, carrier.carrier_id, at
        )
        part.assembly = assembly

        for component in assembly.components:
            # Read back by lane and instant rather than off the lane's current lot:
            # `lot_at` is the function that answers "which lot was this lane drawing
            # from then", the windows are contiguous so the answer is exact, and it
            # raises for an instant no lot covers -- so a supplier cannot be attributed
            # to a component from a different lot by a reordering here.
            lot = self._schedule.lot_at(component.lane, component.read_at)
            await self._nodes.trigger_event(
                COMPONENT_READ,
                at,
                {
                    "ComponentSerial": component.serial,
                    "Lane": component.lane,
                    "LotCode": component.lot_code,
                    "Supplier": lot.supplier,
                },
            )

        await self._nodes.trigger_event(
            ASSEMBLY_CREATED,
            at,
            {
                "AssemblySerial": assembly.serial,
                # In LANES order, because a component's index here is §5.2's
                # `genealogy.position`.
                "ComponentSerials": [c.serial for c in assembly.components],
                "CarrierId": assembly.carrier_id,
            },
        )

        # Lane fill is a level that falls as parts are drawn and is topped up by an
        # operator. M2c's scenario 5 contaminates one lane and scenario 1 starves the
        # feed entirely, so both lanes are separate signals from the start.
        for lane in LANES:
            await self._nodes.write(f"LaneFill_{lane}", at, self._lane_level(lane, at))
            # D12: live-only. The lot code is on the ComponentReadEvent above, which is
            # the authoritative copy; this is the one an HMI reads without asking for
            # history, and historising it would be the second copy §3.4a warns about.
            await self._nodes.write_live(
                f"Lane{lane}_Lot", at, self._schedule.current(lane).lot_code
            )
        await self._nodes.write_live("CurrentAssemblySerial", at, assembly.serial)

    def _lane_level(self, lane: int, at: datetime) -> float:
        """A slow sawtooth with measurement noise: drawn down by production, topped
        back up when the lane runs out.

        **Both lanes supply every assembly**, one component each, so both fall at the
        same rate and wrap on the same part -- the sawtooth itself is the same on both.
        M2a drained lane 1 on the odd parts and lane 2 on the even ones; that was a
        guess made before there were components to draw, and `identity.load_carrier` now
        contradicts it outright. What separates the two lanes is which lot each is
        drawing from, which is what §3.5's scenarios 5 and 7 turn on.

        `lane` is here for the fault engine rather than for the sawtooth: §3.5's feeder
        starvation may take out one lane or the whole feed, and a modifier that could
        not tell which lane it was asked about could only do the second.

        Called once per lane, so each stream carries its own noise draw and the two are
        two measurements rather than one number published twice.
        """
        capacity = self._settings.lane_capacity
        drawn = (self._part_count * self._settings.lane_draw_per_part) % capacity
        level = capacity - drawn + self._rng.gauss(0.0, self._settings.lane_fill_sigma)
        # Applied to the drawn value rather than to the sawtooth it came from, so the
        # noise draw above happens identically whether or not a fault is active --
        # see `faults`' identity property.
        return round(
            clamp_level(self._faults.modify(LANE_FILL, level, at, lane=lane)), 3
        )
