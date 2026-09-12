---
id: SOP-01
title: Investigating a line stop
applies_to:
  question_types: [stop_investigation]
---

# SOP-01 · Investigating a line stop

## What counts as a stop

A line stop is defined by **output**, not by state: no part leaves S4 for longer than 60 s.
Shorter interruptions are micro-stops. They are recorded and counted, but they are not
stops, and answering a micro-stop question with a stop investigation is a wrong answer even
if every fact in it is true.

If the interruption asked about is under the threshold, say so first — *"that was a
micro-stop, not a line stop"* — and then answer what actually happened.

## Procedure

1. **Resolve the window.** Never compute it yourself.
2. **Check coverage.** A gap inside the window changes what you are allowed to conclude.
3. **List the stops** in the window. If the question is singular and there are several, take
   the longest, say that you did, and list the others.
4. **Pull the full stop.** You get a state timeline across all four stations plus a
   propagation derivation — the chain from the consequence backwards to a cause candidate.
5. **Read the chain, do not rebuild it.** Each link names a buffer and the moment it ran
   empty or full. That is why the chain is verifiable rather than inferred.
6. **Check the alarms** on the root station across the stop. An alarm on the root explains
   it; an alarm on a starved station downstream explains nothing.
7. **Only now** apply a pattern document, if one fits.

## Reading the category

| Category | Chain ends at | What it means |
|---|---|---|
| `internal` | a station in `Held` or `Aborted` | the line did this to itself; the root station is the place to go |
| `external_upstream` | S1 starved | nothing on this line is at fault — supply stopped |
| `external_downstream` | S4 blocked | nothing on this line is at fault — the line could not hand off |
| `ambiguous` | two independent cause candidates | say so; do not pick one to be helpful |

The two external categories matter more than they look. The maintainer's next action is
completely different — leave the line alone and go find out who stopped feeding it. An
investigation that names S2 as root because S2 shows the longest `Suspended` episode has
answered the wrong question.

## What the answer must contain

- The root, with its category, cited to the stop.
- The consequences named **as consequences**, not listed as if they were findings of equal
  weight.
- Duration and the parts lost, if asked.
- Any coverage gap in the window.
- A contradiction, if you have one, with reasoning.

## Known trap: the operator intervention

An operator acknowledging an alarm, resetting a station or restarting mid-stop changes the
state timeline in ways the mechanical chain follows faithfully and misleadingly. If the
timeline shows an intervention between the consequence and the apparent root, check whether
the chain is following the intervention rather than the fault. This is the documented case
for contradicting the computation (CORE-02).
