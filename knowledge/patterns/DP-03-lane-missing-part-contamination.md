---
id: DP-03
title: missing_part and contamination concentrated on one feeder lane
applies_to:
  defect_classes: [missing_part, contamination]
  dimensions: [lane]
  stations: [S1]
  question_types: [quality_investigation]
---

# DP-03 · `missing_part` and `contamination` concentrated on one feeder lane

## Pattern

`missing_part` and `contamination` concentrating on **one of the two feeder lanes**, at a
significant level, while the other lane sits at baseline.

The pairing again carries the information: a lane that is dirty both fouls the components it
carries and fails to present them reliably.

## Hypothesis

That feeder lane is contaminated. Clean it.

`basis: hypothesis`, with evidence strength.

## Checks to run

1. **Rule out the imaging first** for the `contamination` half — run the known-good master.
   Contamination is an appearance class and this check is not optional (CORE-01). A fouled
   lens produces contamination on *both* lanes, which is itself the discriminator.
2. **Look at the lane.** Residue, debris, obstruction where components are separated and
   presented.
3. **Lane fill and presentation behaviour** for the `missing_part` half — was the lane empty,
   or failing to present a part that was there?
4. **Pattern analysis by lane**, with significance.
5. **Lot against lane.** If the contamination tracks the *lot* rather than the lane, the
   material arrived dirty and the lane is innocent. These two look identical in a per-lane
   breakdown when one lane happens to be running the affected lot.

## What would refute this

- Both lanes affected roughly equally → not a lane problem. Look at the imaging chain (DP-04)
  or at something common to both.
- Significance failing → say so and stop.
- The concentration tracking the lot rather than the lane → material, not the lane.

## Stating the evidence

Share on the lane, expected, n, effect size, significance — and explicitly state the result of
the master-part check, because a lane hypothesis built on an unvalidated imaging chain is
worth nothing.

See also: `missing_part`, `contamination`, S1, A-101, A-102, DP-04, DP-10.
