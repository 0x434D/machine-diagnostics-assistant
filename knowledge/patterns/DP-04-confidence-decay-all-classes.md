---
id: DP-04
title: Confidence decaying across all classes with scrap flat
applies_to:
  defect_classes: [scratch, contamination, gap, crack, misalignment, missing_part]
  stations: [S3]
  dimensions: [time]
  question_types: [quality_investigation, trend]
---

# DP-04 · Confidence decaying across all classes with scrap flat

## Pattern

Verdict confidence falling across the whole inspected population, with **all six class scores
drifting together**, while the scrap rate stays roughly flat.

**Nothing alarms.** The cell has no sensor watching its own illumination, so this pattern has
no announcement — the only thing that changed in the data is the confidence.

**This shape is only possible because the six scores are independent.** Under a distribution
that summed to one, six values could not all fall — one would have to rise. That they all fall
is the signature, and it points away from production entirely.

## Hypothesis

The imaging chain is degrading — fouled optics, drifting illumination, a dirty window. Clean
the optics and restore the illumination reference.

`basis: hypothesis`, with evidence strength.

## Checks to run

1. **Run the known-good master part.** The documented first move, and here the *only* move
   that settles anything: a master that no longer scores clean means the imaging changed, not
   the production. There is no illumination signal to consult instead.
2. **The shape of the decay.** Gradual is fouling or emitters ageing. A step is something that
   failed — and an emitter failing lights the field unevenly, so check whether the affected
   rejects cluster in one region of the image rather than across the part.
3. **Confidence across the whole population**, not only rejects. Restricting to rejects hides
   the pattern, because rejects are selected on the very scores that are drifting.
4. **Scrap rate over the same window.** Flat scrap with falling confidence is this pattern.
   Rising scrap with falling confidence is something else and probably two things.
5. **Model version.** A version change either side of the drift means the populations are not
   directly comparable and the pattern may be an artefact of the comparison.

## What would refute this

- **The master part still scoring clean.** Then the imaging is sound and the confidence
  movement has another explanation. This is the only refutation available, which is worth
  saying plainly: with no illumination sensor, a clean master is the whole of the evidence.
- **One or two classes moving rather than all six** → a real defect population, not the chain.
- **Scrap rising with confidence falling** → not this pattern.

## The consequence that must be stated

While this holds, **every defect rate in the window is suspect in both directions**: false
rejects rise, and genuine defects are missed. Any answer covering such a window says so.

Do not silently correct rates for it. There is no defensible adjustment for unknown
degradation, and a corrected number with an invisible assumption is worse than an honest
caveated one.

## Known weakness of this pattern

With a simulated classifier the confidence decay is stipulated rather than emergent. The
diagnostic path is right; the signal is softer than it would be with a real model. Labelled
here so nobody mistakes a clean result for a validated one.

See also: S3, `contamination`, `scratch`, CORE-02, SOP-02.
