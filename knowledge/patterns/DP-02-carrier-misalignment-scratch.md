---
id: DP-02
title: Misalignment and scratch concentrated on one carrier
applies_to:
  defect_classes: [misalignment, scratch]
  dimensions: [carrier]
  question_types: [quality_investigation]
---

# DP-02 · Misalignment and scratch concentrated on one carrier

## Pattern

`misalignment` and `scratch` both concentrating on a **single carrier**, at a significant
level, with the rest of the fleet at baseline. No line stop; the line runs normally and makes
bad parts on every twelfth-ish cycle.

The **pairing is the signal.** A worn or damaged nest does two things at once: it stops
locating the component precisely, and it marks the surface where it holds it. Either class
alone on one carrier is weaker evidence than the two together.

## Hypothesis

That carrier's nest is worn or damaged. Inspect it, and take it out of circulation.

`basis: hypothesis`, with evidence strength.

## Checks to run

1. **Hand on the nest.** Wear, galling, debris in the seat. This is the first check for
   `misalignment` and it is what confirms or kills the hypothesis in thirty seconds.
2. **Compare the mark against the image.** For the `scratch` half, establish the damage is on
   the part rather than in the image.
3. **Pattern analysis with significance**, per carrier — not a group-by read by eye.
4. **Consistency of position.** Marks and offsets in the same place on every affected part
   point to a fixed contact on that carrier.
5. **Which parts rode it.** Every assembly carried by that carrier in the window is a
   containment scope, whether or not it was rejected.

## What would refute this

- **Significance failing.** This line runs genuine carrier-to-carrier variation that is not a
  fault. A carrier above average with n=38 and no significance is DP-10, not this.
- **Even spread across carriers** → the station, not the carrier: tooling at S2, or
  presentation at S1.
- **A clean nest** on inspection. The data pointed somewhere; the machine settles it.

## Stating the evidence

Share on the carrier, expected share, sample size, effect size, significance — for example
"92 % of misalignment defects on carrier 7, n=214, p<0.001". Without figures this is a guess
about a carrier, and carriers are removed from service on the strength of it.

See also: `misalignment`, `scratch`, S4, DP-10, CORE-02.
