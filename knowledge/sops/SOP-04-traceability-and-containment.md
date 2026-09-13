---
id: SOP-04
title: Tracing a part, a lot, or a containment scope
applies_to:
  question_types: [traceability]
---

# SOP-04 · Tracing a part, a lot, or a containment scope

Three questions share this procedure, and they are not interchangeable:

| Question | Starting point |
|---|---|
| "what happened to serial 88431?" | one assembly |
| "which parts contain lot L-4471?" | a supplier lot |
| "which parts are affected by the S2 drift?" | a condition over a window |

## Why the data supports this at all

Components are individually serialised and belong to supplier lots; the assembly gets its
own serial at S1. So genealogy is **as-built and exact** — which component went into which
assembly is recorded at the moment it happened.

Per-part process values are likewise recorded against the serial at the instant of
production. **Do not reconstruct which part was at a station at a given time** by joining the
time series against a timestamp. With buffers, variable takt, micro-stops and history gaps,
that association is an inference, and inferred traceability is exactly what makes a
containment list useless at the moment it matters. The authoritative record exists; use it.

## Tracing one assembly

1. Pull the part by serial: station history, genealogy, per-part process values, disposition.
2. Report its **component serials and their lots** — an assembly problem often is not the
   assembly's.
3. Report the disposition and reason. If it was rejected, the reject image exists; good
   parts have no image and that is not a data gap.
4. If asked why, the process values for that part are evidence about that part and nothing
   else. One part's joining force says nothing about a trend.

## Scoping a containment

This is the query traceability exists for.

1. State the criteria in the answer, in plain words, before the numbers.
2. Get the affected set.
3. Report three numbers, not one: **how many affected, how many already rejected, how many
   shipped and need checking**. The third is the one someone has to act on.
4. Attach the serials as a citation, not as prose. A containment list belongs in a
   resolvable, exportable citation.

## Lot problem or one bad part

Do not generalise one defective component into a lot problem, and do not shrink a lot problem
into one bad part. They lead to completely different actions — quarantine a delivery, or
scrap one assembly — and the distinction is a routine, consequential judgement in real
production. DP-06 carries the discriminator; use it rather than deciding by how many rejects
happen to be in view.

## What the answer must contain

- Exact scope: no misses, no false inclusions. This is deterministically checkable and it
  will be checked.
- Lots and component serials where they exist.
- What is still out there, stated as a number someone can act on.
- Any coverage gap inside the window, because a gap means the list may be short — and a
  containment list that is quietly short is the worst failure in this document.
