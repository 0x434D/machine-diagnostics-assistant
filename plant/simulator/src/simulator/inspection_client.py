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

        Which parts this client marks defective is a function of (`seed`, `part_id`)
        and `settings.reject_rate` -- never of how many parts came before, which is
        what lets one client serve catch-up and live alike, as M2a's single continuous
        line requires (see `produce`). The verdict that comes back is the inspection
        service's own, over the rendered image, and is not decided here at all.

        `seed` stays a parameter because that order-independence is the property being
        relied on and this is what makes it testable: two clients differing only in
        their seed must disagree about the same part.
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
        # stream: whether this part is defective then depends on (seed, part_id) and
        # the configured rate, never on how many other parts this process produced
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

        # The request itself carries only what a camera would hand over.
        #
        # **carrier_id is a placeholder and this signature widens when M2c arrives.**
        # The real id does reach Postgres -- S3 puts it on `InspectionResultEvent`,
        # which is §5.2's `inspection_results.carrier_id` -- but it does not reach the
        # classifier, because `ProduceFn` is `(part_id, sim_ts)` and neither this call
        # nor `SimulatedClassifier` can see a carrier at all. M2c's scenario 4 wears one
        # carrier and needs its defect draw keyed on exactly that, so `ProduceFn`, this
        # method and the constant below all move then. Recorded as a known widening
        # rather than a settled choice: what is here now is enough for M2b, and it is
        # not enough for the scenario that needs it.
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
            # The classifier returns its six scores keyed by class name; the event
            # carries them as parallel arrays. Ordered here, against this workspace's
            # copy of the vocabulary, rather than at S3 -- this is the module that owns
            # the copy, and `_ordered_confidences` refuses a response whose key set is
            # not the one we know, which is the only place the two copies drifting
            # apart at runtime could be caught before a vector went out mis-keyed.
            defect_classes=tuple(DEFECT_CLASSES),
            confidences=_ordered_confidences(result["confidences"]),
            confidence=result["confidence"],
            # §3.4: only rejected parts carry their image into the OPC UA event.
            image=image if rejected else None,
            # The classifier's own advertised model_version, not settings.model_version:
            # once a real ModelClassifier replaces SimulatedClassifier, this is the only
            # place that still knows which model actually produced the verdict.
            model_version=result["model_version"],
        )


def _ordered_confidences(scored: dict[str, float]) -> tuple[float, ...]:
    """The classifier's per-class scores in `DEFECT_CLASSES` order.

    Raises ValueError if the classifier scored a different set of classes from the one
    this workspace knows. Refused rather than filled in with zeros or silently
    truncated: a vector that is one class short is still a vector, and every entry
    after the gap would then describe the wrong class for the rest of the milestone
    with nothing raised anywhere.
    """
    if set(scored) != set(DEFECT_CLASSES):
        raise ValueError(
            f"the inspection service scored {sorted(scored)}, and this plant's copy of "
            f"the vocabulary is {sorted(DEFECT_CLASSES)}: the two have drifted, and a "
            "vector ordered against the wrong names is a defect attributed to the "
            "wrong class"
        )
    return tuple(scored[name] for name in DEFECT_CLASSES)
