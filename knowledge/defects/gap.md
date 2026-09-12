---
id: gap
title: gap — the two components are not fully seated
applies_to:
  defect_classes: [gap]
  question_types: [quality_investigation, knowledge]
---

# `gap`

The two components have not come fully together. A dimensional defect: there is a measurable
distance where there should be none.

## First check: gauge a pulled part

Take a rejected assembly off the line and **measure the gap**. Before anything else, before
any trend.

This does two things. It confirms the defect is real rather than a classifier artefact, and
it gives a magnitude to reason with — a gap you can barely gauge and a gap you can see across
the shop floor are not the same problem and do not have the same cause.

## Then: what does the evidence point at

`gap` has (at least) two causes on this line and **the symptom does not choose between them**:

| | Press problem | Material problem |
|---|---|---|
| Joining force | drifts | stable |
| Joining distance | moves with the force | moves while force holds |
| Force–distance curve | shape changes after contact | **contact point moves**, shape holds |
| Defects track | time | **the component lot** |
| Alarm | A-207 likely | none — nothing is out of tolerance |

So the second check is: **did the joining force actually move?**

- Force drifting, `gap` following → the press. DP-01, the scenario-3 shape.
- Force stable, `gap` rising → **material**. DP-05. Go to the lot.

Do not stop at the force trend looking normal and conclude "no cause found". A stable force
during a `gap` rise is not an absence of evidence; it is the evidence.

## Use the curve, not just the two numbers

The **force–distance curve** for the affected parts is the evidence that actually separates
these two causes (S2). A contact point that has moved while the shape after contact is
unchanged points at component geometry — the press travelling further before meeting
resistance is precisely what an undersized component looks like. A changed shape after contact
points at the press.

The peak force and the final distance are summaries of that curve. They can agree while the
curves differ, which is why a stable peak is not by itself an answer.

**If the curve is not available**, say so, use the scalars as the weaker proxy, and let lot
correlation carry the discrimination — where the scalars and the lot evidence disagree, the lot
evidence wins.

## What it is not

- Not `misalignment`. `gap` is components not fully together; `misalignment` is components
  together in the wrong position. They can co-occur and they have different causes.
- Not automatically a press fault, however much the press is the obvious suspect.

See also: S2, A-207, A-201, DP-01, DP-05, SOP-02.
