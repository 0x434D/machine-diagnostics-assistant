"""S3 Inspection: the camera captures, the vision system classifies (§3.4).

The event carries §3.4's widened verdict: six independent per-class scores beside the
names they belong to, and the scalar confidence in the OK/NOK verdict separately. The
serial the event is stamped with is still S3's own count in M2b Task 3; Task 4 replaces
it with the one S1 created and the carrier carried here.
"""

from __future__ import annotations

from datetime import datetime
from typing import override

from simulator.carriers import Carrier
from simulator.config import Settings
from simulator.events import INSPECTION_RESULT
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
            INSPECTION_RESULT,
            at,
            {
                "AssemblySerial": serial,
                # The carrier the part was inspected on. M2c's scenario 4 wears one
                # carrier and expects `misalignment` + `scratch` to concentrate on it,
                # which is a group-by on this field and nothing else.
                "CarrierId": carrier.carrier_id,
                "Disposition": outcome.disposition,
                # Parallel arrays over all six classes, including on a good part: §3.4's
                # "a good part simply scores low on all six", and the only shape in
                # which scenario 6's decay across every class is a question history can
                # answer. Passed straight through as the classifier reported them --
                # ordering or thresholding them here would be the plant deciding
                # something the diagnostics stack is supposed to decide.
                "DefectClasses": list(outcome.defect_classes),
                "Confidences": list(outcome.confidences),
                "Confidence": outcome.confidence,
                "ModelVersion": outcome.model_version,
                # §3.4: only rejected parts carry their image.
                "Image": outcome.image or b"",
            },
        )
