"""The inspection service: a box beside the camera, not inside the simulator (§3.4).

`POST /inspect` is the real interface a model swaps into unchanged; `POST /truth/{part_id}`
is the side channel the simulator uses to declare what is actually wrong with a part, kept
out of the `/inspect` request body so truth never crosses that boundary (§4.5).
"""

from __future__ import annotations

import base64

from fastapi import FastAPI

from inspection.classifier import (
    DEFECT_CLASSES,
    Classifier,
    PartContext,
    SimulatedClassifier,
    TruthChannel,
)
from inspection.config import Settings
from inspection.schemas import InspectIn, InspectOut, TruthIn

app = FastAPI(title="inspection-service")
_truth = TruthChannel()
_settings = Settings()
# Annotated against the Protocol, not left to infer the concrete class: §3.4's seam
# (a real ModelClassifier drops in unchanged) is only load-bearing if something
# actually checks a substitute still satisfies `Classifier`.
_classifier: Classifier = SimulatedClassifier(
    _truth,
    seed=_settings.seed,
    # D7: the rates belong to the classifier, so they are configured on the service
    # that runs it and never reach the simulator.
    false_accept_rate=_settings.false_accept_rate,
    false_reject_rate=_settings.false_reject_rate,
    # D8: what a clean lens renders, so that the clarity the confidences are scaled by
    # is a ratio against a measured frame rather than against an assumed one.
    reference_contrast=_settings.reference_contrast,
)


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
