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
    confidence: float
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

        # A plausible distribution: mass concentrated on the true class, the rest
        # spread with noise, so the confidence field carries information.
        weights = {c: rng.uniform(0.01, 0.08) for c in DEFECT_CLASSES}
        if not defects:
            total = sum(weights.values())
            confidences = {c: w / total for c, w in weights.items()}
            return InspectionResult(
                "good", None, max(confidences.values()), confidences
            )

        top = defects[0]
        weights[top] = rng.uniform(0.55, 0.95)
        total = sum(weights.values())
        confidences = {c: w / total for c, w in weights.items()}
        return InspectionResult("reject", top, confidences[top], confidences)
