---
id: S2
title: S2 Joining — presses the components together
applies_to:
  stations: [S2]
  question_types: [stop_investigation, quality_investigation, trend, status, knowledge]
---

# S2 Joining

Presses the two components into one assembly and marks the assembly serial physically.

## What it publishes

`State` · `StateReason` · `TaktTime` · `JoiningForcePeak` · `JoiningDistance` · `PartCount`

Per part, at the moment it is pressed: **peak joining force and joining distance, recorded
against that serial.** That per-part record is authoritative for that part. The time series
exists too, and is for trends — not for deciding what happened to an individual assembly.

## The two numbers, and what they cannot tell you

Peak force and joining distance are two scalars sampled from what is really a **force–distance
curve**, and the curve shape is what actually separates a press problem from a material
problem. Two presses can reach the same peak at the same final distance by entirely different
routes — one meeting a correctly-sized component late, one meeting an undersized component
early and coasting.

This system stores the scalars, not the curve. That is a real limit and it must be stated
rather than worked around:

> The peak force is stable across the window, which is evidence against a press drift but not
> proof — the force–distance curve is what distinguishes the two, and it is not recorded.

Use the scalars as the weaker proxy they are, and let **lot correlation** carry the
discrimination when it is available (DP-05). Where the scalars and the lot evidence disagree,
the lot evidence is stronger.

## States here

- **`Aborted`** — fault shutdown, e.g. joining force out of tolerance (A-207). Cause
  candidate; needs Clearing then Reset, so an operator was involved.
- **`Held`** — a condition needing an operator, e.g. a jam. Cause candidate.
- **`Suspended(starved)`** — B1_2 empty: S1 stopped. Consequence.
- **`Suspended(blocked)`** — B2_3 full: S3 stopped. Consequence.

S2 stopping is the most consequential stop on this line, because it sits in the middle:
downstream starves within roughly 30 s at capacity 5 and a 6 s takt, and upstream blocks
shortly after.

## Failure modes

| Symptom | Check |
|---|---|
| `gap` rising | whether joining force moved. Stable force with rising `gap` points at material, not the press — DP-05, not DP-01 |
| `crack` | where the crack sits, then peak force against the limit |
| force trending down with `gap` following it | joining process drifting — DP-01, the scenario-3 shape |
| `misalignment` spread evenly across all carriers | tooling alignment here rather than a carrier (DP-02 tests the alternative) |

## What S2 cannot cause

Anything about surface appearance that a camera judges. `scratch` and `contamination` are
assessed at S3 and can be produced by handling, by the carrier, or by the imaging chain
itself. Do not route an appearance class to the press because the press is where the force is.
