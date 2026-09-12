---
id: S4
title: S4 Outfeed — sorts good from bad, returns the carrier
applies_to:
  stations: [S4]
  question_types: [stop_investigation, status, knowledge]
---

# S4 Outfeed

Last station. Sorts each assembly to good or reject according to the S3 verdict, and returns
the carrier to circulation.

## What it publishes

`State` · `StateReason` · `TaktTime` · `OutfeedFill` · `GoodCount` · `RejectCount`, and a
part-completed event per assembly (serial, disposition, reason).

## Why this station defines "stopped"

A line stop is defined by **output**: no part leaving S4 for longer than 60 s. Not by any
station's state. So S4's part-completed events are the clock against which every stop in this
system is measured, and a stop is real regardless of which station caused it.

## Carriers return here

Carriers circulate — twelve of them by default. A worn carrier therefore comes back, again
and again, which is the only reason carrier wear has a statistical signal to find at all. If
carriers passed once, DP-02 would be undetectable.

## States here

- **`Suspended(blocked)`** — outfeed full: whatever takes parts away has stopped. S4 is the
  tail of the line, so there is no downstream station and the category is
  `external_downstream`. Nothing on this line is at fault.
- **`Suspended(starved)`** — B3_4 empty: S3 stopped. Consequence.
- **`Held`** — a condition here needing an operator. Cause candidate.

## Failure modes

| Symptom | Check |
|---|---|
| outfeed fill rising to capacity | downstream removal, off this line. Scenario 2, external |
| blockage that has propagated back to S1 | same cause; the whole line standing does not make it internal |
| reject chute full | rejects not being cleared — a stop caused by the *handling* of scrap, not by scrap |

## The reading that is easy to get wrong

When S4 blocks, the blockage walks backwards until every station is `Suspended` and the whole
line stands. It looks systemic and it is not: a single external condition, with the line
faithfully doing what it should. The chain ends at S4 blocked and the category says so —
report the category, not the size of the disruption.
