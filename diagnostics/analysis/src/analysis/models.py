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
    defect_class: str
    count: int


class Gap(BaseModel):
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
    sample_serials: list[str]
    coverage: Coverage


class Part(BaseModel):
    assembly_serial: str
    source_ts: datetime
    station: str
    result: str
    defect_class: str | None
    confidence: float | None
    model_version: str
    # Only rejects carry an image (§3.4). None here is a fact, not a missing value.
    image_url: str | None
