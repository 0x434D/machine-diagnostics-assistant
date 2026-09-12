"""The inspection service: a box beside the camera, not inside the simulator (§3.4).

`POST /inspect` is the real interface a model swaps into unchanged; `POST /truth/{part_id}`
is the side channel the simulator uses to declare what is actually wrong with a part, kept
out of the `/inspect` request body so truth never crosses that boundary (§4.5).
"""

from __future__ import annotations

import base64
import os

from fastapi import FastAPI
from pydantic import BaseModel

from inspection.classifier import (
    DEFECT_CLASSES,
    PartContext,
    SimulatedClassifier,
    TruthChannel,
)

app = FastAPI(title="inspection-service")
_truth = TruthChannel()
_classifier = SimulatedClassifier(
    _truth, seed=int(os.environ.get("PLANT_SEED", "20260912"))
)


class TruthIn(BaseModel):
    defects: list[str]


class InspectIn(BaseModel):
    """Note what is absent: nothing about the true defect state (§3.4, §4.5)."""

    part_id: str
    image_b64: str
    carrier_id: int


class InspectOut(BaseModel):
    disposition: str
    defect_class: str | None
    confidence: float
    confidences: dict[str, float]
    model_version: str


@app.post("/truth/{part_id}")
def declare_truth(part_id: str, body: TruthIn) -> dict[str, str]:
    """Side channel. Inside plant-net only; never reachable across field-net."""
    _truth.declare(part_id, body.defects)
    return {"status": "ok"}


@app.post("/inspect")
def inspect(body: InspectIn) -> InspectOut:
    """Classify the image a camera would have produced for `body.part_id`.

    Assumes truth for `body.part_id`, if any, was already declared through
    `/truth/{part_id}`; a part with no declared truth is reported "good".
    """
    image = base64.b64decode(body.image_b64)
    result = _classifier.classify(image, PartContext(body.part_id, body.carrier_id))
    return InspectOut(
        disposition=result.disposition,
        defect_class=result.defect_class,
        confidence=result.confidence,
        confidences=result.confidences,
        model_version=result.model_version,
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


__all__ = ["DEFECT_CLASSES", "app"]
