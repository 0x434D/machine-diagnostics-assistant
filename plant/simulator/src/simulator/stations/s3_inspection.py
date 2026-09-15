"""S3 Inspection: the camera captures, the vision system classifies (§3.4).

The event carries §3.4's widened verdict: six independent per-class scores beside the
names they belong to, and the scalar confidence in the OK/NOK verdict separately. The
serial it is stamped with is the one S1 created and the carrier brought here, not a
count of S3's own -- the two diverge by whatever is sitting in the buffers, and a part
inspected under a serial S1 never issued is a traceability record pointing at nothing.
"""

from __future__ import annotations

from datetime import datetime
from typing import override

from simulator.carriers import Carrier
from simulator.config import Settings
from simulator.events import INSPECTION_RESULT
from simulator.faults import NO_FAULTS, FaultSet
from simulator.line import PartState
from simulator.stations.base import (
    ProduceFn,
    Station,
    StationNodes,
    require_assembly,
    require_joining_work,
)


class InspectionStation(Station):
    def __init__(
        self,
        nodes: StationNodes,
        settings: Settings,
        seed: int,
        produce: ProduceFn,
        *,
        faults: FaultSet = NO_FAULTS,
    ) -> None:
        super().__init__(nodes, settings, seed, faults=faults)
        self._produce = produce

    @override
    async def on_part(self, at: datetime, carrier: Carrier, part: PartState) -> None:
        assembly = require_assembly(part, carrier, self.code)
        outcome = await self._produce(
            assembly.serial,
            carrier.carrier_id,
            require_joining_work(part, carrier, self.code),
            at,
        )
        # S4 sorts on these. Putting them on the part rather than leaving S4 to
        # time-join the event stream is §3.4a's rule applied one station early, and the
        # reason is the same one station on: the part S4 sorts is the part S3 inspected,
        # and joining the two streams on "when was this serial at S3" is an inference
        # where an exact answer is already in hand.
        part.disposition = outcome.disposition
        part.reason = outcome.defect_class or ""
        await self._nodes.trigger_event(
            INSPECTION_RESULT,
            at,
            {
                "AssemblySerial": assembly.serial,
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
