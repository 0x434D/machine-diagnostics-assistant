---
id: SOP-03
title: Reading line status right now
applies_to:
  question_types: [status]
---

# SOP-03 · Reading line status right now

"What is happening right now?" is a different question from any historical one: it is
answered from current state, and the honest answer is short.

## Procedure

1. **Read line status.** Four stations with their PackML state and reason, three buffer
   levels, the clock.
2. **Check the clock phase.** If the plant is booting or catching up, say so — the numbers
   are not yet what a user will assume they are.
3. **Translate the states, do not list them.** `Suspended(starved, B2_3)` means "S3 is
   waiting because the buffer ahead of S2 is empty", and the useful sentence names the
   station that is actually holding the line up.
4. **If something is stopped, find out why** — that is SOP-01, not this one. Say what is
   stopped, then investigate.
5. **Active alarms**, with how long they have been standing and whether anyone has
   acknowledged them.

## Reading buffers

Buffer levels are the leading indicator. A buffer draining towards empty says a stop is
propagating downstream and has not arrived yet; a buffer filling towards capacity says the
same going upstream. At capacity 5 and a 6 s takt, roughly 30 s separates a stop at one
station from its consequence at the next.

Saying "S3 will starve in about half a minute unless S2 restarts" is more useful than any
description of the present instant, and it follows from the buffer level rather than from
speculation.

## What not to do

Do not attach a trend, a pattern or a history lesson to a status question unless it was
asked for. The person asking is usually standing in front of the line.
