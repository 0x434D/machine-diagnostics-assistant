---
id: misalignment
title: misalignment — components joined off-position
applies_to:
  defect_classes: [misalignment]
  dimensions: [carrier]
  question_types: [quality_investigation, knowledge]
---

# `misalignment`

The components are joined, but in the wrong relative position. Geometric, and usually
determined **before** the press acts: the press joins what it is given, where it is given it.

## First check: the carrier nest — wear and debris

Go to the carrier and look at the nest that locates the part. Wear, galling, debris in the
seat. A nest that no longer locates the component precisely lets it sit skewed, and the press
faithfully joins it skewed.

This is a hand-on-the-machine check and it comes first because carriers are the most common
route to misalignment on a line where **carriers circulate**. A worn carrier comes back every
twelfth cycle or so, producing a repeating signature rather than scatter.

## Then: does it concentrate on one carrier

Ask the pattern analysis, with significance — **not** a group-by you read by eye. This line
runs genuine carrier-to-carrier variation that is not a fault, so a carrier sitting above
average proves nothing on its own.

- **Significant concentration on one carrier** → that carrier. DP-02, particularly if
  `scratch` concentrates with it — a worn nest both mislocates and marks.
- **Spread evenly across carriers** → not a carrier problem. Look at the station: tooling
  alignment at S2, or presentation at S1. Something common to every part.

The contrast is the whole diagnostic value: the *distribution* across carriers separates a
carrier fault from a station fault, and nothing else does.

## What it is not

- Not `gap`. Wrong position versus not fully seated.
- Not, by default, a press fault. The press is downstream of where position is determined.

## The answer that is often correct

"Carrier 7 is above average, n=38, not significant — that is within normal spread." Say it
plainly when the numbers say it. Inventing a carrier fault out of ordinary variation is the
specific failure this class invites.

See also: S1, S2, `scratch`, DP-02, DP-10, CORE-02.
