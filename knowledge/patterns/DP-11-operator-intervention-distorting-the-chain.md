---
id: DP-11
title: An operator intervention distorting the stop chain
applies_to:
  stations: [S1, S2, S3, S4]
  dimensions: [time]
  question_types: [stop_investigation]
---

# DP-11 · An operator intervention distorting the stop chain

## Pattern

A propagation chain terminating at a station whose episode began with a **person** rather than
with a fault. The shapes to recognise:

- A `Held` episode that starts *after* a disturbance somewhere else on the line.
- A `Held` or `Aborted` station carrying **no alarm of its own**.
- A Reset or restart on a station that was never at fault — §3.5 puts "the occasional
  unnecessary reset" in this line's normal behaviour, so it will occur.
- A restart followed shortly by another stop, because the root was not fixed before the reset.

## Hypothesis

The computed root follows the intervention rather than the fault. The real root lies
elsewhere, and the chain reached a person's decision and stopped there.

This is the documented case for populating **`contradiction`** (CORE-02): give the computed
root, your root, and the reasoning. Never quietly answer with a different root than the one you
were handed — silent disagreement is indistinguishable from an error.

## Why the computation cannot see this by itself

`Held` is classified as a cause candidate **precisely because it requires an operator.** That
is the same property that makes it indistinguishable from a station a person stopped
deliberately — to clear the line, to reach something, to work on a neighbour.

So the rule that makes `Held` informative is the rule that makes this case invisible to it.
Code cannot separate *held because it faulted* from *held because somebody stopped it*. That
distinction lives in the sequence and its timing, which is interpretation — which is why it
belongs in this document and not in the propagation computation.

## Checks to run

1. **Ordering.** Did the `Held` or the Reset begin *after* a disturbance elsewhere? A cause
   precedes its consequences. An intervention that starts second is a response to the first
   thing, not the origin of it.
2. **Is there an alarm on that station at all?** A cause candidate with no alarm of its own is
   a strong sign a person put it into that state.
3. **Acknowledgement timing** — raised, acknowledged, cleared, as three separate moments.
4. **Restart-then-stop-again.** A reset before the root was fixed produces two episodes from
   one fault. Report one stop with an intervening restart, not two stops.
5. **Whether the intervention explains the recovery.** If the line came back when a *different*
   station was cleared, the chain's root was not the thing that was wrong.

## Acknowledgement delay is not fault duration

An alarm standing for twenty minutes before anyone acknowledged it does not mean a twenty-minute
fault. It means nobody was there.

These are separate quantities, and the maintenance literature keeps them separate for exactly
this reason: time-to-acknowledge measures response and coverage, time-to-repair measures the
work, and only the second says anything about the machine. Folding them together attributes to
the equipment what belongs to staffing, and sends the wrong person to fix it.

When a stop's duration is dominated by the gap between raise and acknowledgement, **say so** —
that is the finding, and it is a different conversation from a machine fault.

## What would refute this

- **The intervention starting after the chain's root episode.** Then the chain is following the
  fault and reached the intervention on its way; nothing is distorted.
- **The station carrying its own alarm, raised before any disturbance elsewhere.** That is a
  genuine cause candidate and the computation is right.
- **No intervention in the timeline at all.** Do not reach for this pattern to explain a chain
  you merely find surprising.

## Stating the evidence

Cite the stop. Name the computed root and your root, both explicitly. Give the ordering with
timestamps — the disturbance, then the intervention — because the ordering *is* the argument.
The contradiction is a `hypothesis`, and its evidence strength is that sequence.

See also: SOP-01, CORE-01, CORE-02, A-900, DP-07, DP-08.
