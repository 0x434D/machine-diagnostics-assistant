"""S4 Outfeed: good/bad sorting, and the carrier goes back (§3.1)."""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar, override

from simulator.carriers import Carrier
from simulator.config import Settings
from simulator.events import PART_COMPLETED
from simulator.faults import NO_FAULTS, OUTFEED_FILL, FaultSet
from simulator.line import PartState
from simulator.stations.base import (
    Station,
    StationNodes,
    clamp_level,
    require_assembly,
)


class OutfeedStation(Station):
    # §4.1 gives S4 no PartCount: GoodCount and RejectCount are its part count, and
    # they sum to it. See Station.part_count_signal.
    part_count_signal: ClassVar[str | None] = None

    def __init__(
        self,
        nodes: StationNodes,
        settings: Settings,
        seed: int,
        *,
        faults: FaultSet = NO_FAULTS,
    ) -> None:
        super().__init__(nodes, settings, seed, faults=faults)
        self._good = 0
        self._reject = 0

    @override
    async def on_part(self, at: datetime, carrier: Carrier, part: PartState) -> None:
        # Identity is held to the same standard as the disposition, and checked first:
        # a part nobody can name cannot be reported as completed, and §14's traceability
        # line ends at this event.
        assembly = require_assembly(part, carrier, self.code)
        if part.disposition is None:
            raise ValueError(
                f"part on carrier {carrier.carrier_id} reached S4 with no disposition: "
                "S3 did not inspect it, and sorting it as good would be exactly the "
                "quiet wrong answer this system exists not to give"
            )
        if part.disposition == "good":
            self._good += 1
        else:
            self._reject += 1

        await self._nodes.write("GoodCount", at, self._good)
        await self._nodes.write("RejectCount", at, self._reject)
        await self._nodes.write("OutfeedFill", at, self._outfeed_level(at))
        # §5.2's `part_dispositions` row: how the part left the line, and why. The
        # reason is S3's verdict carried on the part, not re-derived here.
        await self._nodes.trigger_event(
            PART_COMPLETED,
            at,
            {
                "AssemblySerial": assembly.serial,
                "Disposition": part.disposition,
                "Reason": part.reason,
            },
        )

    def _outfeed_level(self, at: datetime) -> float:
        """Fills as parts arrive, emptied when an operator clears it. §3.5's scenario 2
        blocks the outfeed, which is why this is a level rather than a counter: a
        blockage is a backlog the operator stops taking away, and it shows up here as
        the level climbing past the point it normally wraps at."""
        held = (self._good + self._reject) % self._settings.outfeed_capacity
        level = held + self._rng.gauss(0.0, self._settings.outfeed_fill_sigma)
        # After the draw, not before it -- see `faults`' identity property.
        return round(clamp_level(self._faults.modify(OUTFEED_FILL, level, at)), 3)
