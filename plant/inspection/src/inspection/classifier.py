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

    Configurable false-accept/false-reject rates are M2 (see `docs/ENGINEERING.md`
    and the M1 brief's scope note); this reports the declared truth faithfully and
    only fabricates the confidence distribution around it.
    """

    def __init__(self, truth: TruthChannel, seed: int) -> None:
        self._truth = truth
        self._seed = seed

    def classify(self, image: bytes, ctx: PartContext) -> InspectionResult:
        defects = self._truth.lookup(ctx.part_id)
        rng = random.Random(f"{self._seed}:{ctx.part_id}:{len(image)}")

        # Independent per-class scores (§3.4, amended): a low baseline for every
        # class, boosted for whichever ones are actually declared truth -- no
        # normalisation, so a part with two declared defects can score high on both
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
        # second declared defect could plausibly score higher than the first.
        return InspectionResult("reject", top, max(scores.values()), scores)
