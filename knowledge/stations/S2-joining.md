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

## The curve is the evidence; the two numbers are its summary

Per part, S2 records the **force–distance curve** — how the load built as the press travelled —
together with the peak force and the final distance.

Reach for the curve, because **the curve is what separates a press problem from a material
problem** and the two scalars are not. Two presses can reach the same peak at the same final
position by entirely different routes, and the route is the diagnosis:

- **Where the force begins to rise** is about the components. The press meets resistance when
  it makes contact, so a contact point that has moved says the incoming geometry changed —
  undersized components let the press travel further before the load builds at all.
- **How the load develops after contact** is about the process. Slope, peak, and where the
  peak falls relative to contact describe what the press did with what it was given.

A material problem therefore shifts *when* resistance appears while leaving the shape after
contact recognisable. A press drift changes the shape while contact stays where it was.

**If the curve is not available** for the window in question — say so, and fall back to peak
force and final distance as the weaker proxy they are. A stable peak force is then evidence
against a press drift rather than proof of its absence, and **lot correlation carries the
discrimination** (DP-05). Never present the scalars as if they had settled it.

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
