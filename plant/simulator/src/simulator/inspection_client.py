"""The simulator renders; the inspection service classifies. Two boxes, one wire (§3.4)."""

from __future__ import annotations

import base64
import random
from datetime import datetime

import httpx

from simulator.config import Settings
from simulator.render import render_part
from simulator.station_s3 import PartOutcome

# Mirrors inspection.classifier.DEFECT_CLASSES. Same §10.7 reason as simulator.render's
# duplication of inspection.render: the workspace split forbids importing it from there,
# so this is the plant's own copy of the shared defect vocabulary, kept only for picking
# which defect to simulate.
DEFECT_CLASSES = [
    "gap",
    "crack",
    "misalignment",
    "missing_part",
    "scratch",
    "contamination",
]


class InspectionClient:
    """Wraps the HTTP calls to the inspection service behind `ProduceFn`.

    Assumes `settings.inspection_url` names a reachable inspection service.
    """

    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        self._s = settings
        self._http = client
        self._rng = random.Random(settings.seed)

    async def produce(self, part_id: str, _sim_ts: datetime) -> PartOutcome:
        """Render the part, declare its truth on the side channel, then classify it.

        The timestamp parameter is unused -- it exists to satisfy `ProduceFn`'s
        signature; only the OPC UA event that wraps this result carries a
        timestamp (§4.2, applied in station_s3._emit_part).
        """
        defects = (
            [self._rng.choice(DEFECT_CLASSES)]
            if self._rng.random() < self._s.reject_rate
            else []
        )
        image = render_part(
            part_id, defects, self._s.image_width, self._s.image_height, self._s.seed
        )

        # Truth goes down the side channel, keyed by part id -- never in the /inspect
        # request below (§3.4, §4.5).
        await self._http.post(
            f"{self._s.inspection_url}/truth/{part_id}", json={"defects": defects}
        )

        # The request itself carries only what a camera would hand over.
        response = await self._http.post(
            f"{self._s.inspection_url}/inspect",
            json={
                "part_id": part_id,
                "image_b64": base64.b64encode(image).decode(),
                "carrier_id": 1,
            },
        )
        result = response.json()
        rejected = result["disposition"] == "reject"
        return PartOutcome(
            disposition=result["disposition"],
            defect_class=result["defect_class"],
            confidence=result["confidence"],
            # §3.4: only rejected parts carry their image into the OPC UA event.
            image=image if rejected else None,
        )
