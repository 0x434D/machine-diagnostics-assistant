"""The simulator renders; the inspection service classifies. Two boxes, one wire (§3.4)."""

from __future__ import annotations

import base64
from datetime import datetime
from typing import Final

import httpx

from simulator.config import Settings
from simulator.faults import DEFECT_PROPENSITY, NO_FAULTS, FaultSet
from simulator.identity import LANES
from simulator.noise import NoiseFloor
from simulator.render import render_part
from simulator.stations.base import PartOutcome

# Mirrors inspection.classifier.DEFECT_CLASSES. Same §10.7 reason as simulator.render's
# duplication of inspection.render: the workspace split forbids importing it from there,
# so this is the plant's own copy of the shared defect vocabulary. It decides which
# classes the plant draws truth over and the order the event's score array is keyed in.
DEFECT_CLASSES = [
    "gap",
    "crack",
    "misalignment",
    "missing_part",
    "scratch",
    "contamination",
]


TRUTH_DRAWS_PER_PART: Final = len(LANES) * len(DEFECT_CLASSES)
"""How many independent Bernoulli draws decide one part's true defect state.

One per (lane, defect class). **Per class**, because §3.4 makes the six scores
independent -- M2b drew one class if the part scrapped, which cannot express a part
carrying two however the rate is set. **Per lane**, because a defect on a component is
attributable to the lane that component came from, which is what separates scenario 5
(one lane) from a line-wide rise and what §5.2's `genealogy.position` exists to record.

`noise.NoiseFloor.class_propensity` takes this count and sets the per-draw rate so a
nominal carrier still scraps at `settings.reject_rate`.

**The class pair DP-02 keys on is a pair per carrier and per lane, not a pair per
part.** D11's wording -- "scenarios 4 and 5 each need two classes on one part" -- reads
as the second, and at the shipped ratio the plant does not produce it: on carrier 7 under
scenario 4, P(misalignment) = P(scratch) = 0.00718 per part and the two are independent,
so P(both) = 5.2e-5, which is 0.06 parts across the carrier's 1,100 in a 33 h run. One
part carrying both would need a wear factor of ~39 instead of 2.86, and carrier 7 would
then scrap 19 % of its parts against the line's 1.5 % -- a difference no significance
test is needed for, and the ratio `carrier_wear_sigmas` exists to set would be gone.

So the correlation the analysis queries is `GROUP BY carrier_id` (or lane) with **both**
classes elevated on it, which is what `test_noise` measures and what M3 must look for.
The independent per-class shape is still exactly what D11's `ALTER` was for: §3.4 needs a
good part to score low on all six at once and scenario 6 needs all six to fall together,
neither of which a single-class draw or a softmax can produce.
"""


class InspectionClient:
    """Wraps the HTTP calls to the inspection service behind `ProduceFn`.

    Assumes `settings.inspection_url` names a reachable inspection service.
    """

    def __init__(
        self,
        settings: Settings,
        client: httpx.AsyncClient,
        *,
        seed: int | None = None,
        faults: FaultSet = NO_FAULTS,
    ) -> None:
        """`seed` defaults to `settings.seed`.

        Which parts this client marks defective is a function of (`seed`, `part_id`),
        the carrier the part rode and whatever `faults` is doing at the instant -- never
        of how many parts came before, which is what lets one client serve catch-up and
        live alike, as M2a's single continuous line requires (see `produce`). The
        verdict that comes back is the inspection service's own, over the rendered
        image, and is not decided here at all.

        `seed` stays a parameter because that order-independence is the property being
        relied on and this is what makes it testable: two clients differing only in
        their seed must disagree about the same part.
        """
        self._s = settings
        self._http = client
        self._seed = settings.seed if seed is None else seed
        self._noise = NoiseFloor(settings, self._seed)
        self._faults = faults

    async def produce(
        self, part_id: str, carrier_id: int, sim_ts: datetime
    ) -> PartOutcome:
        """Render the part, declare its truth on the side channel, then classify it.

        Raises `httpx.HTTPStatusError` if the inspection service responds with an error
        status.
        """
        defects = self.truth_for(part_id, carrier_id, sim_ts)
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

        # The request itself carries only what a camera would hand over -- the image and
        # the two identifiers the frame is stamped with. The carrier is the real one the
        # part rode, which M2b could not supply: `ProduceFn` was `(part_id, sim_ts)`
        # then, and the placeholder `1` that stood here is what §3.5's scenario 4 needed
        # widened before a worn carrier could concentrate anything.
        response = await self._http.post(
            f"{self._s.inspection_url}/inspect",
            json={
                "part_id": part_id,
                "image_b64": base64.b64encode(image).decode(),
                "carrier_id": carrier_id,
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

    def truth_for(self, part_id: str, carrier_id: int, sim_ts: datetime) -> list[str]:
        """Which defect classes this part genuinely carries (§3.6's true defect state).

        Public because it is the plant's own declaration about the part and nothing else
        in the system can recompute it: §3.6 puts the true defect state of every part in
        the ground-truth log, and the log is written beside the line rather than by
        asking the classifier what it thought.

        **The draws come from a stream no fault can reach and the thresholds are what a
        fault moves.** That is §3.5's "baseline scrap drawn from a distribution
        unrelated to any injected fault", in the only form that can be checked: a run
        with a scenario makes exactly the same draws as a run without one, so a
        scenario's signal is never partly its own noise.

        Ordered by lane and then by class, and returned in `DEFECT_CLASSES` order so
        that two lanes carrying the same class name produce one entry: the classifier
        scores a class, not a component, and a duplicate would make one defect look like
        two on §3.4's vector.
        """
        draws = self._noise.scrap_draws(part_id, TRUTH_DRAWS_PER_PART)
        propensity = self._noise.class_propensity(carrier_id, TRUTH_DRAWS_PER_PART)
        present: set[str] = set()
        for index, (lane, name) in enumerate(
            (lane, name) for lane in LANES for name in DEFECT_CLASSES
        ):
            threshold = self._faults.modify(
                DEFECT_PROPENSITY,
                propensity,
                sim_ts,
                carrier_id=carrier_id,
                lane=lane,
                defect_class=name,
            )
            if draws[index] < threshold:
                present.add(name)
        return [name for name in DEFECT_CLASSES if name in present]


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
