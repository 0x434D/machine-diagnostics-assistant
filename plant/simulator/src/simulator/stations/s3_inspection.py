"""S3 Inspection: the camera captures, the vision system classifies (§3.4).

The inspection call and the event shape are M1's, moved rather than rewritten. The
event keeps M1's event fields exactly -- D11 widens inspection_results to §5.2's target
shape in M2b, in one ALTER, not here.
"""

from __future__ import annotations

from datetime import datetime
from typing import override

from simulator.carriers import Carrier
from simulator.config import Settings
from simulator.identity import assembly_serial
from simulator.line import PartState
from simulator.stations.base import ProduceFn, Station, StationNodes


class InspectionStation(Station):
    def __init__(
        self, nodes: StationNodes, settings: Settings, seed: int, produce: ProduceFn
    ) -> None:
        super().__init__(nodes, settings, seed)
        self._produce = produce

    @override
    async def on_part(self, at: datetime, carrier: Carrier, part: PartState) -> None:
        serial = assembly_serial(self._part_count - 1)
        outcome = await self._produce(serial, at)
        # S4 sorts on this. Putting it on the part rather than leaving S4 to
        # time-join the event stream is §3.4a's rule applied one station early.
        part.disposition = outcome.disposition
        await self._nodes.trigger_event(
            at,
            {
                "AssemblySerial": serial,
                "Disposition": outcome.disposition,
                "DefectClass": outcome.defect_class or "",
                "Confidence": outcome.confidence,
                "ModelVersion": outcome.model_version,
                # §3.4: only rejected parts carry their image.
                "Image": outcome.image or b"",
            },
        )
