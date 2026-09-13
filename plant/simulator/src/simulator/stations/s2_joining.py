"""S2 Joining: presses the parts together (§3.1).

M2a writes §4.1's two summary signals. §3.4a's force-distance curve -- the thing that
actually separates a press problem from a material problem, and the only thing
separating M2c's scenario 7 from scenario 3 -- is M2b. The two scalars are written
here so the stream exists and the gateway has something to deadband; they are
explicitly not claimed to be sufficient for diagnosis.
"""

from __future__ import annotations

from datetime import datetime
from typing import override

from simulator.carriers import Carrier
from simulator.line import PartState
from simulator.stations.base import Station


class JoiningStation(Station):
    @override
    async def on_part(self, at: datetime, carrier: Carrier, part: PartState) -> None:
        peak = round(
            self._settings.joining_force_nominal + self._rng.gauss(0.0, 40.0), 2
        )
        distance = round(
            self._settings.joining_distance_nominal + self._rng.gauss(0.0, 0.02), 4
        )
        await self._nodes.write("JoiningForcePeak", at, peak)
        await self._nodes.write("JoiningDistance", at, distance)
