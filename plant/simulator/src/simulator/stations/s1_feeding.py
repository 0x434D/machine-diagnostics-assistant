"""S1 Feeding: separates parts from two feeder lanes onto a carrier (§3.1).

Lane lots, component serials and the two events §4.1 gives S1 are M2b. M2a writes the
two lane-fill signals and nothing that would be a placeholder -- §13's standard is
that nothing in a milestone is faked, and a Lot node holding a constant is a fake.
"""

from __future__ import annotations

from datetime import datetime
from typing import override

from simulator.carriers import Carrier
from simulator.line import PartState
from simulator.stations.base import Station


class FeedingStation(Station):
    @override
    async def on_part(self, at: datetime, carrier: Carrier, part: PartState) -> None:
        # Lane fill is a level that falls as parts are drawn and is topped up by an
        # operator. M2c's scenario 5 contaminates one lane and scenario 1 starves the
        # feed entirely, so both lanes are separate signals from the start.
        for lane in (1, 2):
            await self._nodes.write(f"LaneFill_{lane}", at, self._lane_level(lane))

    def _lane_level(self, lane: int) -> float:
        """A slow sawtooth with noise: drawn down by production, refilled when low.
        The shape carries no diagnosis in M2a -- it exists so the signal is a real
        varying float rather than a constant the historian would coalesce away."""
        # S1 alternates feeders, so each lane falls with the parts it actually
        # supplied: lane 1 the odd parts, lane 2 the even ones. Draining both by the
        # whole part count would make the two nodes one signal published twice, and
        # M2c's scenario 5 contaminates exactly one of them.
        supplied = (self._part_count + 2 - lane) // 2
        drawn = (supplied * 0.5) % 100.0
        return round(100.0 - drawn + self._rng.gauss(0.0, 0.4), 3)
