"""The simulator renders; the inspection service classifies. Two boxes, one wire (§3.4)."""

from __future__ import annotations

import base64
import random
from datetime import datetime

import httpx

from simulator.config import Settings
from simulator.render import render_part
from simulator.stations.base import PartOutcome

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

    def __init__(
        self, settings: Settings, client: httpx.AsyncClient, *, seed: int | None = None
    ) -> None:
        """`seed` defaults to `settings.seed`.

        A client's verdict for a part is a pure function of (`seed`, `part_id`) and of
        nothing else -- see `produce` -- so one client serves catch-up and live alike,
        which is what M2a's single continuous line hands it. `seed` stays a parameter
        because that purity is the property being relied on and this is what makes it
        testable: two clients differing only in their seed must disagree about the
        same part.
        """
        self._s = settings
        self._http = client
        self._seed = settings.seed if seed is None else seed

    async def produce(self, part_id: str, _sim_ts: datetime) -> PartOutcome:
        """Render the part, declare its truth on the side channel, then classify it.

        The timestamp parameter is unused -- it exists to satisfy `ProduceFn`'s
        signature; only the OPC UA event that wraps this result carries a timestamp
        (§4.2, applied in `stations.s3_inspection`). Raises `httpx.HTTPStatusError` if
        the inspection service responds with an error status.
        """
        # A fresh Random keyed by part_id, not a continuing draw from one shared
        # stream: the defect decision for a given part_id is then a pure function of
        # (seed, part_id), never of how many other parts this process produced
        # before it -- see the constructor's `seed` docstring.
        rng = random.Random(f"{self._seed}:{part_id}:defect")
        defects = (
            [rng.choice(DEFECT_CLASSES)] if rng.random() < self._s.reject_rate else []
        )
        image = render_part(
            part_id,
            defects,
            self._s.image_width,
            self._s.image_height,
            self._s.seed,
            self._s.image_compress_level,
        )

        # Truth goes down the side channel, keyed by part id -- never in the /inspect
        # request below (§3.4, §4.5).
        truth_response = await self._http.post(
            f"{self._s.inspection_url}/truth/{part_id}", json={"defects": defects}
        )
        truth_response.raise_for_status()

        # The request itself carries only what a camera would hand over. carrier_id is
        # still a placeholder, and no longer for M1's reason -- the carrier pool exists
        # now and S3 knows which carrier it is looking at. D11 widens
        # inspection_results to §5.2's shape in M2b, in one ALTER, and the only
        # question that needs the part-to-carrier link is M2c's scenario 4; plumbing
        # the real id through a column nothing reads would be M2b's data model
        # arriving early and unvalidated.
        response = await self._http.post(
            f"{self._s.inspection_url}/inspect",
            json={
                "part_id": part_id,
                "image_b64": base64.b64encode(image).decode(),
                "carrier_id": 1,
            },
        )
        response.raise_for_status()
        result = response.json()
        rejected = result["disposition"] == "reject"
        return PartOutcome(
            disposition=result["disposition"],
            defect_class=result["defect_class"],
            confidence=result["confidence"],
            # §3.4: only rejected parts carry their image into the OPC UA event.
            image=image if rejected else None,
            # The classifier's own advertised model_version, not settings.model_version:
            # once a real ModelClassifier replaces SimulatedClassifier, this is the only
            # place that still knows which model actually produced the verdict.
            model_version=result["model_version"],
        )
