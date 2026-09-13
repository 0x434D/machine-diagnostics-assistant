---
id: DP-01
title: Rising gap with the joining process drifting at S2
applies_to:
  defect_classes: [gap]
  stations: [S2]
  dimensions: [time]
  question_types: [quality_investigation, trend]
---

# DP-01 · Rising `gap` with the joining process drifting at S2

## Pattern

`gap` defects rising over a window, **and** the peak joining force trending away from nominal
over the same window, with the defect rise following the force rather than leading it.

Often ends at A-207 and an `Aborted` S2 when the force finally leaves tolerance.

## Hypothesis

The joining process at S2 is drifting: the press is no longer reaching the intended
relationship between force and position, so components are left short of fully seated.

`basis: hypothesis`. Always.

## Checks to run

1. **Gauge a pulled part.** Confirm the gap is real and get a magnitude.
2. **Force trend across the window**, not the alarm instant. A drift walks; a step jumps.
   Only a walk supports this pattern.
3. **The curves for affected parts.** Shape changing after an unmoved contact point is this
   pattern; a contact point that has moved is DP-05.
4. **Ordering in time.** The force should move *before* the defect rate does. If the defects
   came first, this is not the pattern.
5. **Joining distance** alongside the force — under a press drift, both move together.
6. **Scope the affected parts.** Parts pressed off-nominal before any alarm fired already
   shipped. That is the number someone has to act on.

## What would refute this — read this before concluding

**A stable joining force refutes it.** Rising `gap` with a force that has not moved is not a
weak version of this pattern; it is a different pattern with a different cause, and the
correct answer is DP-05 — material.

That case is deliberately built into this line because DP-01 is the obvious reading and the
wrong one. Before citing DP-01, state what the force actually did. If it was stable, stop
here and go to DP-05.

Also refuting: defects correlating with a component lot rather than with the clock. Lot
correlation beats time correlation (CORE-02), and a press does not know what lot it is
pressing.

## Use the curve

The **force–distance curve** is the discriminating evidence (S2). Under this pattern the shape
after contact changes while the contact point stays where it was — the press is doing something
different with the same components. Under DP-05 the opposite holds.

Check the curves before citing this pattern. If they are unavailable, say so and treat the
stable-or-drifting peak force as a lean rather than a finding.

## Stating the evidence

Name the force movement with figures and window, the defect rate against baseline, the sample
size, and the ordering in time. "Force fell from X to Y over N hours while `gap` rose from
A % to B %, n=…" — not "the press is drifting".

See also: `gap`, S2, A-207, A-201, DP-05, SOP-02.
