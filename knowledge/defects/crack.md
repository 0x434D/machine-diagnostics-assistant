---
id: crack
title: crack — a component is fractured
applies_to:
  defect_classes: [crack]
  question_types: [quality_investigation, knowledge]
---

# `crack`

A component is fractured. Structural, and unlike most classes here, usually unambiguous once
you have the part in your hand.

## First check: where the crack sits

**Location is the diagnosis.** Look at where on the component the crack runs, relative to
where the press acts on it.

- **At the press interface** — where load is introduced — points to the joining operation:
  overforce, or a part not properly supported while the load went through it.
- **Away from the press interface** points upstream: the component arrived damaged, or was
  damaged in handling before it was ever joined.

This single observation splits the investigation in two, which is why it comes before any
data. Going to the force trend first tells you what the press did; it does not tell you
whether the press is where the part broke.

## Then, by branch

**If at the press interface:**
1. Peak force for that serial, against the tolerance band. The per-part record is
   authoritative for that part; the time series is for the trend.
2. Whether force is elevated across a run or was elevated once.
3. Seating — a part not properly supported cracks under an otherwise normal load, and the
   force record will look innocent.

**If away from the press interface:**
1. What the affected parts share — lot, lane.
2. Whether the crack is consistent in location and orientation across parts. Consistency
   points to a mechanism; scatter points to handling.

## What it is not

Not a scratch. Surface marking and fracture are different classes with different causes; if
the distinction is not clear from the image, it is clear from the part.

## Honest gap

The repair — what to adjust, what to replace — is not documented here. This document gets a
maintainer to the right half of the machine; the procedure belongs to whoever owns it.

See also: S2, A-207, `scratch`, SOP-02.
