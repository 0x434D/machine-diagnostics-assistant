"""Pydantic request/response models for the inspection HTTP API.

Kept apart from app.py: `mypy --strict`'s `disallow_any_explicit` flags every
`class X(BaseModel)` statement (pydantic's own metaclass takes `**kwargs: Any` in its
`__new__`, not any annotation of ours -- see `inspection.config.Settings` and
`simulator.config.Settings` for the same false positive on a single class each).
Three occurrences belong in one file, so scoping the mypy.ini override to this module
is narrower than relaxing it across the whole service.
"""

from __future__ import annotations

from pydantic import BaseModel


class TruthIn(BaseModel):
    defects: list[str]


class InspectIn(BaseModel):
    """Note what is absent: nothing about the true defect state (§3.4, §4.5)."""

    part_id: str
    image_b64: str
    carrier_id: int


class InspectOut(BaseModel):
    """`confidence` is the OK/NOK verdict's own confidence; `confidences` is
    independent per-class scores that do not sum to 1 (§3.4, amended)."""

    disposition: str
    defect_class: str | None
    confidence: float
    confidences: dict[str, float]
    model_version: str
