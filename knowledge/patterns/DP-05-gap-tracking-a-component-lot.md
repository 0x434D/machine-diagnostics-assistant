---
id: DP-05
title: Rising gap tracking a component lot with the joining force stable
applies_to:
  defect_classes: [gap]
  dimensions: [lot, lane]
  stations: [S1, S2]
  question_types: [quality_investigation, traceability]
---

# DP-05 · Rising `gap` tracking a component lot, joining force stable

## Pattern

`gap` defects rising — **and the peak joining force perfectly stable.** The rise begins at a
lot change on one feeder lane, and the affected assemblies contain components from that lot.

## Why this pattern exists

Because the symptom points at the wrong cause. Rising `gap` is exactly what DP-01 attributes
to a press drifting at S2, and the press is the obvious suspect. The only thing separating the
two readings is that the defects correlate with the **lot** rather than with the force trend —
and that correlation is invisible without as-built genealogy.

This is the case that tests whether the system can resist the obvious wrong answer when the
data supports a better one.

## Hypothesis

A component lot is out of specification — undersized components that do not seat fully — and
the press is doing exactly what it should.

`basis: hypothesis`, with evidence strength. **Plus a containment list**, which is not optional
here.

## Checks to run

1. **Gauge a pulled part**, and gauge the incoming component if you can. A dimensional claim
   about material deserves a dimensional measurement.
2. **Joining force across the window.** Stable force is the discriminator. State it explicitly
   as a finding rather than as an absence.
3. **The force–distance curve.** This is the discriminator (S2). Undersized components let the
   press travel further before it meets resistance, so the **contact point moves** while the
   shape after contact stays recognisable. That signature is this pattern; a changed shape
   after an unmoved contact point is DP-01.
4. **Joining distance**, as the scalar summary of the same thing: distance moving while peak
   force holds supports this pattern. Use it when the curve is unavailable, and say that you
   did.
5. **When the lot changed**, against when the defects started. The lot boundary should lead.
6. **Defect rate by lot**, with significance — the affected lot against the others on that
   lane.
7. **Scope the containment.** Which assemblies contain this lot, how many were rejected, and
   **how many shipped.** Include assemblies whose component serial was never read (A-103) as
   unknowns; they cannot be excluded.

## What would refute this

- **The force moving.** Then DP-01 is in play and this is not the answer.
- **Defects spread across lots**, or beginning well before the lot change.
- **Only a handful of parts affected, clustered in time** → DP-06, one bad component rather
  than a bad lot. Do not turn a single defective part into a supplier problem.

## Stating the evidence

Defect rate within the lot versus outside it, with n and significance; the curve signature — contact point moved, shape held — or the force
trend with figures showing it did not move, whichever you actually had; the lot change time against the defect onset; and the
containment counts. The sentence someone acts on is *"278 assemblies containing L-4471
shipped and need checking"*, not "we think the lot is bad".

See also: `gap`, S1, S2, DP-01, DP-06, SOP-04, SOP-02.
