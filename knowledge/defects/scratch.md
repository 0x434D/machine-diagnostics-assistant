---
id: scratch
title: scratch — surface damage on the assembly
applies_to:
  defect_classes: [scratch]
  dimensions: [carrier]
  question_types: [quality_investigation, knowledge]
---

# `scratch`

Surface marking on the assembly. An **appearance** class: judged from how the surface looks,
which is exactly the territory where an optical system can be wrong.

## First check: is it on the part, or only in the image

Take the rejected part and compare it against its stored reject image. Rejects carry an image;
good parts do not, and that is not a data gap.

- **Mark in the image, mark on the part** → real damage. Proceed below.
- **Mark in the image, nothing on the part** → a false reject. The part is fine and the
  imaging chain is the subject of the investigation, not production.

This ordering is deliberate and it is the opposite of the geometric classes. A scratch is a
few pixels of contrast; so is a smear on a lens, a reflection, or a hair. Investigating
production because of a mark that was never on a part is an expensive, common and entirely
avoidable mistake.

If false rejects are suspected more broadly, **run the known-good master part** — it exercises
illumination, optics, acquisition and classifier in one move (CORE-01).

## Then, if the damage is real: does it concentrate on one carrier

Scratches come from contact. The question is contact with *what*, and the distribution answers
it:

- **Concentrated on one carrier**, with significance → that carrier's nest or contact points.
  DP-02, especially alongside `misalignment`: a worn nest both mislocates and marks.
- **Spread across all carriers** → something fixed that every part passes: guides, rails,
  stops, transfer points. Not a carrier problem.

Then look at whether the marks are consistent in position and direction across parts.
Consistency means a fixed contact; scatter means handling.

## What it is not

- Not `crack`. Surface marking versus fracture.
- Not automatically a production problem at all, until the first check says it is.

See also: S3, `misalignment`, `contamination`, DP-02, DP-04, DP-10.
