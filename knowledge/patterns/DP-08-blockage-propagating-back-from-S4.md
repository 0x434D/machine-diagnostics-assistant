---
id: DP-08
title: Blockage propagating backwards from S4
applies_to:
  stations: [S1, S2, S3, S4]
  dimensions: [time]
  question_types: [stop_investigation, status]
---

# DP-08 · Blockage propagating backwards from S4

## Pattern

S4 blocked, then S3, then S2, then S1 — each `Suspended(blocked)`, each following the one
before by roughly the time it takes to *fill* a buffer. Outfeed fill rising to capacity ahead
of it. Eventually the entire line stands.

## Hypothesis

Nothing on this line is at fault. Whatever removes finished parts stopped. Category:
`external_downstream`.

The chain terminates at S4 blocked, which is the other edge of the system.

## Checks to run

1. **Outfeed fill** before the stop. How long the rise took says whether removal slowed or
   stopped outright.
2. **Reject chute** separately (A-402). A full reject chute is a different blockage with a
   different fix, and it has a quality dimension the outfeed does not.
3. **The ordering and the gaps**, stepping upstream at roughly buffer fill time.
4. **Whether anything on the line changed.** Usually nothing did — and saying so is the
   finding, not an absence of one.

## What would refute this

- Any station in `Held` or `Aborted`. That is a cause candidate inside the line.
- The cascade running downstream instead — DP-07.
- S4 blocked with the outfeed **not** full, which means something else is stopping the sort.

## The trap this pattern exists to name

A whole line standing looks systemic, urgent and internal. It is none of those. One external
condition, with the line faithfully doing what it should. Report the category rather than the
scale — every minute spent examining S1 through S3 is wasted, and the scale of the disruption
is what makes people spend it.

## Stating the evidence

The chain with each buffer and fill time, the category, the outfeed fill trend, the duration.
`derived`, from the propagation computation.

See also: S4, A-401, A-402, DP-07, SOP-01.
