---
id: DP-09
title: Rising micro-stop count with no line stop
applies_to:
  stations: [S1, S2, S3, S4]
  dimensions: [time]
  question_types: [trend, stop_investigation, statistics]
---

# DP-09 · Rising micro-stop count with no line stop

## Pattern

The number of short interruptions rising over a window while **no single interruption crosses
the 60 s threshold**. Availability falls. No stop investigation is triggered, because by
definition there is no stop.

## Hypothesis

Something is degrading in a way that shows up as frequency rather than as duration. The
micro-stop **count** is the diagnostic signal; no individual instance is one.

`basis: hypothesis`, with evidence strength.

## Why this pattern is easy to miss

Every instance looks like noise, and one of them genuinely is: this line runs brief jams
roughly every twenty minutes as its normal condition. The pattern lives only in the aggregate,
and aggregates are what nobody looks at when each event clears itself in fifteen seconds.

A line that never stops and quietly loses ten percent of its output is a real and common
condition.

## Checks to run

1. **Count against baseline**, not against zero. The question is whether the rate moved.
2. **Where they cluster** — one station, one period, one carrier. A station-clustered rise is
   a machine condition; a carrier-clustered rise points at the carrier.
3. **A-900 instances**, their station and how long each stood before acknowledgement. Rising
   acknowledgement delay is an operator-availability signal, not a machine one, and confusing
   the two sends the wrong person.
4. **Cumulative lost output**, which is the figure that makes this worth acting on.
5. **Whether any cleared part is unaccounted for.** Parts removed by hand during a jam may
   have no completion record and must not be silently missing from a containment.

## What would refute this

- The count being inside normal variation. Say so; that is DP-10 in a different dimension.
- One long interruption dominating — that is a line stop and belongs to SOP-01.

## Stating the evidence

Count and rate against baseline with the window, where they cluster with significance, and
cumulative lost time or parts. "Micro-stops rose from X/h to Y/h, n=…, concentrated on S2" —
not "there seem to be more jams".

See also: A-900, SOP-01, SOP-05, DP-10.
