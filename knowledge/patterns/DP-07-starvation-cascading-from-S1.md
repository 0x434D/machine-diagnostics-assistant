---
id: DP-07
title: Starvation cascading downstream from S1
applies_to:
  stations: [S1, S2, S3, S4]
  dimensions: [time]
  question_types: [stop_investigation, status]
---

# DP-07 · Starvation cascading downstream from S1

## Pattern

S1 starved, then S2, then S3, then S4 — each `Suspended(starved)`, each following the one
before by roughly the time it takes to drain a buffer. At capacity 5 and a 6 s takt that is
about 30 s per link. **No station raises an alarm of its own** beyond the lane-empty alarm at
S1, because nothing on the line is faulty.

## Hypothesis

Nothing on this line is at fault. Supply to S1 stopped. Category: `external_upstream`.

The propagation chain terminates at S1 starved, which is the edge of the system — there is no
upstream station to continue to.

## Checks to run

1. **Lane fill at S1** before the stop. Steady decline is consumption outrunning
   replenishment; an abrupt drop is a supply interruption.
2. **One lane or both.** Both is supply to the line; one is that lane.
3. **The ordering and the gaps.** The cascade should step downstream at roughly the buffer
   drain time. That regularity is what confirms propagation rather than four coincident
   faults.
4. **A lot boundary**, in case the lane simply finished a lot.

## What would refute this

- **An alarm on a station other than S1**, or any station in `Held` or `Aborted`. A cause
  candidate inside the line means the chain does not terminate at the edge, and the category
  is not external.
- **The cascade running the wrong way** — that is DP-08.
- **Timing that does not match buffer drain.** Stations stopping together, rather than in
  sequence, is not propagation.

## What the answer must not do

It must not name S2, S3 or S4 as causes. They are starved, which is a consequence by
definition. Listing four stopped stations as four findings misrepresents one external event as
a line-wide failure, and sends a maintainer to inspect equipment that did nothing wrong.

## Stating the evidence

The chain, cited to the stop, with each link's buffer and the time it ran empty. The category.
The duration and the parts not made. That is a `derived` finding, not a hypothesis — the
propagation computation produced it.

See also: S1, A-101, A-102, DP-08, SOP-01, CORE-01.
