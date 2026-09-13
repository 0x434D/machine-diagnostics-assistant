---
id: S1
title: S1 Feeding — separates components onto a carrier
applies_to:
  stations: [S1]
  question_types: [stop_investigation, quality_investigation, status, knowledge]
---

# S1 Feeding

First station. Separates components from **two feeder lanes** onto a carrier, reads each
component's serial, and creates the assembly serial.

## What it publishes

`State` · `StateReason` · `TaktTime` · `LaneFill_1` · `LaneFill_2` · `PartCount` ·
`Lane1_Lot` · `Lane2_Lot` · `CurrentAssemblySerial`

Events: a component read per lane (serial, lane, lot) and an assembly created (assembly
serial, its two component serials, carrier).

## Why this station matters beyond feeding

**Genealogy begins here.** The lot codes on the two lanes, and the component serials read
into each assembly, are what later makes it possible to say that defects track a *lot*
rather than the clock. Without this station's events, a material problem is indistinguishable
from a process drift — see DP-05.

A lot change at a lane is therefore a first-class event in any quality investigation. When
scrap rises, "did a lane change lot, and when" is a cheap and often decisive question.

## States here

- **`Suspended(starved)`** — no components arriving. S1 is the head of the line, so there is
  no upstream station: this is the boundary of the system and the category is
  `external_upstream`. Nothing on this line is at fault and nothing on it will fix it.
- **`Suspended(blocked)`** — B1_2 full, i.e. S2 has stopped. A consequence.
- **`Held`** — needs an operator here. Cause candidate.

## Failure modes

| Symptom | Check |
|---|---|
| lane fill falling to zero | supply upstream of the line — this is scenario 1 and it is external |
| `missing_part` defects downstream | lane fill and feeder presentation at S1 (see `missing_part`) |
| defects concentrated on one lane | what that lane carries: its lot, and its physical condition — DP-03 |
| a component with no serial read | the assembly's genealogy is incomplete; say so rather than assuming a component was absent |

**A no-read is not a missing part.** The code reader failing and the component being absent
produce similar-looking gaps in the record and completely different actions. Check whether the
assembly was created and whether the part is physically present before concluding either.

## What S1 cannot cause

Joining defects. S1 places components; it does not press them. A `gap` or a `crack` traced to
S1 has to go through the component — wrong part, wrong dimension, damaged on arrival — and
that is a material claim needing lot evidence, not a station claim.
