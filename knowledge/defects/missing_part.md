---
id: missing_part
title: missing_part — one of the two components is absent
applies_to:
  defect_classes: [missing_part]
  dimensions: [lane]
  question_types: [quality_investigation, knowledge]
---

# `missing_part`

One of the two components is not there. The assembly was created and carried through the line
incomplete.

## First check: lane fill and the feeder at S1

Go to S1 and look at the two lanes: fill level, and whether the feeder is presenting
components cleanly. The simplest cause is the most common one — a lane ran empty, or the
feeder failed to present.

The lane fill signals are the record of this, and they lead the alarm: `LaneFill_1` or
`LaneFill_2` falling towards zero shows the condition developing before A-101 or A-102
confirms it.

## Then: which lane, and was it ever there

1. **Which lane is missing.** The class does not say; the genealogy does. An assembly with
   one component serial instead of two names the lane that failed.
2. **Was a component serial read?** This is the branch that matters:
   - No serial, no part → it was never fed. Go to the feeder and the supply.
   - Serial read, part absent → it was fed and then lost. Go to what happens between the read
     and the press: handling, transfer, a dropped part somewhere in the machine.
   - **No serial, part present** → this is A-103, a read failure, *not* a missing part. Do not
     conflate them; they lead to opposite actions.
3. **Is the part loose in the machine.** A dropped component is a jam risk for the next cycle,
   which makes this worth saying out loud even when the defect itself is understood.

## Concentration on one lane

`missing_part` concentrating on a single lane, especially together with `contamination`, is
DP-03 — something about that lane rather than about the line. Check it with significance
before claiming it.

## What it is not

Not a vision problem. A camera reporting an absent component is the easy case for an optical
system: it is a large, unambiguous, geometric difference. If you suspect the verdict anyway,
the image exists — look at it.

See also: S1, A-101, A-102, A-103, DP-03, SOP-04.
