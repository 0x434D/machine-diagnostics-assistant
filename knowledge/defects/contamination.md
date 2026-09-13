---
id: contamination
title: contamination — foreign material on the part, or something that looks like it
applies_to:
  defect_classes: [contamination]
  dimensions: [lane]
  question_types: [quality_investigation, knowledge]
---

# `contamination`

Foreign material on the assembly — or something that resembles it. The second **appearance**
class, and the one most easily produced by the imaging chain rather than by production.

## First check: re-run the known-good master part

Before looking at a single production part, put the **master part** through the cell.

It exercises illumination, optics, acquisition and classifier together, and it answers the
only question worth asking first: *has the imaging changed, or has the production changed?*

- **Master scores clean** → imaging is sound. The rejects are real. Investigate the parts.
- **Master no longer scores clean** → the imaging changed. Every defect rate in this window is
  suspect, in both directions, and the investigation moves to the cell rather than the line.

The reason this comes first is that fouled optics, drifting illumination and a dirty window
all *look* like contamination on parts, and they look like it on every part. Checking the
reference before the population costs two minutes and routinely saves a shift.

## Then, if the parts really are contaminated: what does it track

1. **Lane.** `contamination` concentrated on one feeder lane, especially together with
   `missing_part`, is DP-03 — something about that lane. This is the scenario-5 shape.
2. **Location on the part.** Where the material sits says where it came from: the same place
   on every part points at a fixed source in the machine; scattered placement points at
   material arriving already dirty.
3. **Lot.** If it tracks the lot rather than the lane, it arrived with the components.

## The signature that means optics, not parts

Confidence decaying across **all six** classes while the scrap rate stays flat. That is only
possible because the six scores are independent — under a distribution they could not all fall
— and it is the signature of the imaging degrading rather than of any defect increasing
(DP-04).

**Nothing will alarm to tell you.** The cell has no sensor watching its own illumination, so a
light source dimming or fouling produces no warning at all — only sagging confidence, and only
if someone looks. That is exactly why the master part comes first in this document.

## What it is not

Not a verdict about material until the master part has cleared the imaging. That is the whole
discipline of this class.

See also: S3, `scratch`, DP-03, DP-04, CORE-01.
