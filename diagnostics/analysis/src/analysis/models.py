"""The response shapes. These generate the OpenAPI contract, and §7.3 generates the
frontend's TypeScript from that, so the frontend cannot drift from the API.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class Window(BaseModel):
    from_ts: datetime
    to_ts: datetime


class DefectClassCount(BaseModel):
    """One class, and how many parts in the window scored at or above the threshold.

    Not a partition of the rejects: §3.4's six scores are independent and do not sum to 1,
    so a part the model believes carries two defects counts under both. The counts can
    therefore total more than `rejects`, and that is the answer rather than a rounding of it.
    """

    defect_class: str
    count: int


class Gap(BaseModel):
    """§4.4: without gap markers, missing data is indistinguishable from a quiet machine."""

    from_ts: datetime
    to_ts: datetime
    reason: str


class Coverage(BaseModel):
    """§4.4: without gap markers, missing data is indistinguishable from a quiet machine."""

    gaps: list[Gap]


class InspectionStats(BaseModel):
    window: Window
    total: int
    rejects: int
    by_defect_class: list[DefectClassCount]
    # The score `by_defect_class` counted at or above. Reported rather than left in a config
    # file: a count whose meaning depends on a number the reader cannot see is a number the
    # agent would cite as if it meant something else.
    defect_class_threshold: float
    sample_serials: list[str]
    coverage: Coverage


class ComponentOrigin(BaseModel):
    """One as-built component of an assembly, and the supplier lot it was drawn from.

    Everything but the serial and the position is nullable, and null means **unknown**, not
    absent: a component named by an assembly whose own read event lies before the gateway's
    history horizon is one this system knows exists and knows nothing else about. Dropping
    such a component would make §3.5 scenario 7's containment list quietly short, which is
    the one failure mode that matters at the moment a containment list is wanted.
    """

    component_serial: str
    position: int
    lane: int | None
    read_at: datetime | None
    lot_code: str | None
    supplier: str | None


class ProcessValue(BaseModel):
    """One number a station recorded against this serial at the instant of production.

    The signal is the name the plant's own event carries (`PeakForce`), which is not
    always the name of the historised stream beside it (`JoiningForcePeak`). Nothing
    between the plant and here translates the two, so nothing can translate them wrongly.
    """

    station: str
    signal: str
    value: float


class ProcessCurve(BaseModel):
    """D6's force–distance curve for this part, as the samples the press recorded.

    The thing the two scalars cannot reconstruct: two presses reach the same peak at the
    same final position by different routes, and the route is the diagnosis (§3.4a).
    """

    station: str
    signal: str
    samples: list[float]


class Inspection(BaseModel):
    """§3.4's verdict for this part, or absent if the part has not been inspected.

    `defect_classes` and `confidences` are parallel arrays over **every** class the
    classifier scores, on good parts too — a good part is six low scores, not an absent
    vector. `confidence` is the confidence in the OK/NOK verdict and is not one of them;
    reading the vector as a distribution is the measured defect that once reported a good
    part as 27 % confident and ~30 % misaligned.

    Both arrays are nullable because M1 wrote rows before the vector existed. Null is
    "this row predates the widened event", not "this part scored nothing".
    """

    source_ts: datetime
    station: str
    result: str
    defect_classes: list[str] | None
    confidences: list[float] | None
    confidence: float | None
    model_version: str
    # Only rejects carry an image (§3.4). None here is a fact, not a missing value.
    image_url: str | None


class Disposition(BaseModel):
    """How the part left the line, and why. Absent while the part is still on it."""

    at: datetime
    disposition: str
    # Null for a good part: the plant sends an empty reason, and an empty reason stored as
    # text makes every good part look like a condition with a nameless cause.
    reason: str | None


class Part(BaseModel):
    """§14's end-to-end trace of one serial, every section read by that serial alone.

    A section that is empty or null is a section this system has no row for, and the part
    is answered anyway. The two shapes that produces are both ordinary rather than
    exceptional: a part between S2 and S3 has no verdict yet, and an assembly created
    before the gateway's history horizon has no creation instant, no carrier and no
    genealogy, because the one event carrying all three arrived before the gateway did.
    """

    assembly_serial: str
    # Null when this gateway never saw the assembly created — a true statement about the
    # part, and the reason the contract does not omit it.
    created_at: datetime | None
    carrier_id: int | None
    genealogy: list[ComponentOrigin]
    process_values: list[ProcessValue]
    process_curves: list[ProcessCurve]
    inspection: Inspection | None
    disposition: Disposition | None
