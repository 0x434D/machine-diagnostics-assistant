"""inspect(image_bytes, part_context) -> InspectionResult.

SimulatedClassifier resolves truth through a side channel keyed by part id.
A real ModelClassifier would ignore the channel entirely and this signature
would not change -- that is the whole point of the split (§3.4).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Protocol

DEFECT_CLASSES = [
    "gap",
    "crack",
    "misalignment",
    "missing_part",
    "scratch",
    "contamination",
]
MODEL_VERSION = "simulated-1"


@dataclass(frozen=True)
class PartContext:
    part_id: str
    carrier_id: int


@dataclass(frozen=True)
class InspectionResult:
    disposition: str  # "good" | "reject"
    defect_class: str | None
    # Confidence in the OK/NOK verdict above, NOT in `defect_class` -- §3.4 (amended):
    # a good part is confidently OK while every entry in `confidences` scores low.
    confidence: float
    # Independent per-class scores in [0, 1], not a distribution: they do not sum to
    # 1, and there is no seventh "good" class. Forced by §3.5's scenarios -- optics
    # fouling needs every class's score able to fall together (impossible if six
    # values must sum to 1), and two scenarios need two classes scoring high on one
    # part, which mutually exclusive classes cannot express.
    confidences: dict[str, float]
    model_version: str = MODEL_VERSION


class Classifier(Protocol):
    def classify(self, image: bytes, ctx: PartContext) -> InspectionResult: ...


@dataclass
class TruthChannel:
    """The side channel. Keyed by part id, reachable only inside plant-net, and
    never part of an /inspect request body."""

    _truth: dict[str, list[str]] = field(default_factory=dict)

    def declare(self, part_id: str, defects: list[str]) -> None:
        self._truth[part_id] = defects

    def lookup(self, part_id: str) -> list[str]:
        return self._truth.get(part_id, [])


class SimulatedClassifier:
    """Stands in for a real model behind the `Classifier` protocol (§3.4).

    **D7: the error rates live here, not in the simulator's noise model.** §3.5 lists
    false accepts and false rejects under the permanent noise floor because they are
    visible as line behaviour, and §3.4 assigns them to the classifier because that is
    what produces them. §3.4 wins: a real `ModelClassifier` has error rates emergently,
    so a plant that also modelled them would double-count them the day the interface is
    swapped. Nothing on the simulator side draws a verdict.

    The error is in the *evidence*, not bolted onto the verdict afterwards. A missed
    defect scores low on every class, exactly as a model that did not see it would, and
    a false alarm scores high on a class that is not there -- so the vector and the
    verdict always agree, and only the truth channel knows which of them was wrong.
    """

    def __init__(
        self,
        truth: TruthChannel,
        seed: int,
        false_accept_rate: float = 0.0,
        false_reject_rate: float = 0.0,
    ) -> None:
        """`false_accept_rate` is the share of genuinely defective parts reported good;
        `false_reject_rate` the share of genuinely good parts reported defective. Both
        default to zero, so a caller that wants a faithful classifier gets one by
        saying nothing; `inspection.app` passes the configured values.
        """
        self._truth = truth
        self._seed = seed
        self._false_accept_rate = false_accept_rate
        self._false_reject_rate = false_reject_rate

    def classify(self, image: bytes, ctx: PartContext) -> InspectionResult:
        truth = self._truth.lookup(ctx.part_id)
        rng = random.Random(f"{self._seed}:{ctx.part_id}:{len(image)}")

        # What the model believes it saw, which is the truth except at the configured
        # rates. Drawn before the scores below, so that the scores describe the belief
        # rather than being patched up after it.
        if truth:
            defects = [] if rng.random() < self._false_accept_rate else truth
        elif rng.random() < self._false_reject_rate:
            defects = [rng.choice(DEFECT_CLASSES)]
        else:
            defects = []

        # Independent per-class scores (§3.4, amended): a low baseline for every
        # class, boosted for whichever ones the model believes it saw -- no
        # normalisation, so a part with two defects can score high on both
        # at once (§3.5 scenarios 4 and 5), and the six never have to sum to 1.
        scores = {c: rng.uniform(0.01, 0.08) for c in DEFECT_CLASSES}
        for defect in defects:
            scores[defect] = rng.uniform(0.55, 0.95)

        if not defects:
            # Confidently OK: nothing scored high, so 1 - the loudest false alarm
            # is the verdict's own confidence, not any one class's.
            return InspectionResult("good", None, 1.0 - max(scores.values()), scores)

        top = defects[0]
        # Confidently NOK: the verdict's confidence is the strongest signal seen,
        # whichever class it came from -- not `scores[top]` specifically, since a
        # second defect could plausibly score higher than the first.
        return InspectionResult("reject", top, max(scores.values()), scores)
