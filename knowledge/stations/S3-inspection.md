---
id: S3
title: S3 Inspection — camera captures, vision system classifies
applies_to:
  stations: [S3]
  question_types: [quality_investigation, status, knowledge]
---

# S3 Inspection

The camera belongs to the station. **The vision system is a separate box beside it.** That
separation matters diagnostically: "the camera did not take a picture" and "the classifier
got it wrong" are different failures with different fixes.

## What it produces

`State` · `StateReason` · `TaktTime` · `PartCount`, and one inspection result event per part.

Each result carries **six independent per-class scores** — `gap`, `crack`, `misalignment`,
`missing_part`, `scratch`, `contamination` — each in [0, 1]. They do **not** sum to 1 and
there is no seventh "good" class. A good part simply scores low on all six.

The scalar confidence on the event is confidence in the **OK/NOK verdict**, not in any class.
A good part has high verdict confidence with all six class scores low. Reading the six scores
as a distribution produces nonsense — a good part reported as "27 % confident, 30 %
misaligned" is the signature of that mistake.

Two consequences worth holding on to:

- **A part can carry two classes at once**, and the pairs are informative: `misalignment` +
  `scratch` (DP-02), `missing_part` + `contamination` (DP-03).
- **All six scores can fall together.** That is impossible if they were a distribution, and
  it is the signature of the imaging chain degrading rather than production changing (DP-04).

## Images

**Only rejected parts carry an image.** Good parts get a result without one. A missing image
on a good part is not a data gap and must never be reported as one.

When an image does exist, it is evidence about the *image*, not automatically about the part.
If the reject image does not show the defect, the defect was not on the part.

## States here

- **`Suspended(starved)`** — B2_3 empty: S2 stopped. Consequence.
- **`Suspended(blocked)`** — B3_4 full: S4 stopped. Consequence.
- **`Held`** — a condition here needing an operator. Cause candidate.

## Failure modes

| Symptom | Check |
|---|---|
| confidence decaying across all classes, scrap rate flat | the imaging chain, not production — DP-04 |
| rejects whose images show no defect | false rejects; run the master part |
| a defect class rising with no correlate in carrier, lane or lot | imaging before production |
| no results while parts flow | acquisition or the vision box, not a quality problem at all |

## The station's own trap

S3 is where every quality problem on this line becomes *visible*, which makes it the station
everybody looks at and rarely the station at fault. Before concluding that S3 is the problem,
establish that the imaging changed — the master part is the check (CORE-01). Before
concluding that it is not, establish the same thing, because a drifting imaging chain
misstates every other rate in the window.
