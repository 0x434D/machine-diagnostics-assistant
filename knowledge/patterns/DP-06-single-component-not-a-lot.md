---
id: DP-06
title: One defective component, not a lot problem
applies_to:
  defect_classes: [gap, crack, missing_part, misalignment, scratch, contamination]
  dimensions: [lot]
  question_types: [quality_investigation, traceability]
---

# DP-06 · One defective component, not a lot problem

## Pattern

A very small number of defective assemblies — often exactly one — with no significant
correlation to lot, lane, carrier or time. The rate does not move. Nothing else changed.

## Hypothesis

A single defective component reached the line. It made one bad assembly. There is no
population to contain.

`basis: hypothesis`, with evidence strength — and here the evidence strength is an argument
about **absence**: n is small, no dimension clears significance, the baseline did not move.

## Why this pattern exists

To stop the system over-generalising. Distinguishing "one bad component" from "a bad lot" is a
routine and consequential judgement in real production, and it runs in both directions:

| | One bad part | Bad lot |
|---|---|---|
| Count | one, or very few | a population |
| Rate against baseline | unmoved | moved, significantly |
| Correlation with lot | none | strong, and starting at the lot boundary |
| Action | scrap the part | quarantine the delivery, scope the containment |

Reporting a lot problem as one bad part leaves defective assemblies in the field. Reporting one
bad part as a lot problem quarantines a good delivery and costs real money. Both are failures
and neither is the safe default.

## Checks to run

1. **Confirm the defect on the part.** One part is easy to hold.
2. **Its genealogy** — component serials, lots, lane, carrier.
3. **The same lot's other assemblies.** If the lot is bad, they will show it. If they do not,
   the lot is not the story.
4. **The rate against baseline.** This line runs roughly 1.5 % scrap plus configured false
   rejects. A single reject is inside that, by construction.
5. **Every other dimension**, with significance, so that "no pattern" is a checked statement
   rather than a shrug.

## What would refute this

A significant correlation on any dimension. If the lot, lane or carrier clears the gate, this
is not a single bad part — go to the pattern that fits.

## Stating the evidence

Say what you checked and found nothing in. "One rejected assembly; no significant concentration
by lot (n=…), lane, carrier or time; scrap rate unchanged at X % against a baseline of Y %."
A negative finding stated with its figures is a finding.

See also: DP-05, DP-10, SOP-04, CORE-02.
