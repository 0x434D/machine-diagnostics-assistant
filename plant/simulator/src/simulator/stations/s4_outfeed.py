"""S4 Outfeed: good/bad sorting, and the carrier goes back (§3.1)."""

from __future__ import annotations

from datetime import datetime
from typing import override

from simulator.carriers import Carrier
from simulator.config import Settings
from simulator.line import PartState
from simulator.stations.base import Station, StationNodes


class OutfeedStation(Station):
    def __init__(self, nodes: StationNodes, settings: Settings, seed: int) -> None:
        super().__init__(nodes, settings, seed)
        self._good = 0
        self._reject = 0

    @override
    async def on_part(self, at: datetime, carrier: Carrier, part: PartState) -> None:
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
        await self._nodes.write("OutfeedFill", at, self._outfeed_level())

    def _outfeed_level(self) -> float:
        """Fills as parts arrive, emptied when an operator clears it. M2c's scenario 2
        blocks the outfeed entirely, which is why this is a level rather than a
        counter."""
        return round((self._good + self._reject) % 50 + self._rng.gauss(0.0, 0.3), 3)
