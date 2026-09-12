---
id: crack
title: crack — a component is fractured
applies_to:
  defect_classes: [crack]
  question_types: [quality_investigation, knowledge]
---

# `crack`

A component is fractured. Structural, and unlike most classes here, usually unambiguous once
you have the part in your hand.

## First check: where the crack sits

**Location is the diagnosis.** Look at where on the component the crack runs.

But read the location against the part's **risk zones**, not against the machine.

## Risk zones, and why they come first

In a pressing or forming operation, cracks appear where the **highest process forces coincide
with the least material**. That combination is a property of the part and the operation, not
of the day: it is the same place every time, and it can be determined **in advance by
simulation** rather than discovered by finding cracked parts.

This is clearest in deep drawing, where the zones of peak strain over thinned material are
computed before a tool is ever built — but the principle carries to any press operation. The
part has places where it is going to crack first, and they are knowable.

Two consequences, and both matter more than the fault they diagnose:

1. **The inspection can be aimed.** Knowing the risk zones means looking at them deliberately
   rather than hoping a crack is large enough to catch attention anywhere on the part.
2. **Location becomes evidence rather than description.** A crack *in* a risk zone and a crack
   *outside* every risk zone are different findings with different causes.

## Reading the location

**In a known risk zone** — the process ran to the edge of what the material tolerates there.
That is a genuine ambiguity and the data resolves it:

- **Force above nominal** → the process pushed harder than the zone tolerates. Check the peak
  force for that serial against the band, then whether it is elevated across a run or was
  elevated once.
- **Force normal** → then the material gave way at a normal load, which points at the
  material: thickness, or properties. Check what the affected parts share — lot above all.
- **Force normal and the part was not properly supported** → load went through the part along
  a path it was never meant to take. The force record will look entirely innocent, which is
  what makes this one easy to miss.

**Outside every risk zone** — this is not the forming process doing what forming processes do.
Look upstream and sideways: the component arrived damaged, or it was damaged in handling, or
it was mislocated so that load arrived somewhere it should never have been.

## Then, either way

1. **Consistency across parts.** Same location, same orientation, part after part, means a
   mechanism. Scatter means handling.
2. **What the affected parts share** — lot, lane, carrier — with significance, not by eye.
3. **Scope the containment** if a lot is implicated. A fractured component is not a defect
   somebody wants to find in the field.

## Missing input

**The risk-zone map for this part is not in this knowledge base.** Without it, "in a risk
zone" cannot be evaluated and this document degrades to "look at the crack and see whether the
location repeats" — which is weaker, and should be stated as weaker rather than papered over.

Filling it means one document per part geometry naming its risk zones, from the process
simulation that already exists wherever the tooling was designed properly. That is a concrete
addition, and it is exactly the kind of thing this knowledge base is shaped to accept.

## What it is not

Not a scratch. Surface marking and fracture are different classes with different causes; if
the distinction is not clear from the image, it is clear from the part.

## Honest gap

The repair — what to adjust, what to replace — is not documented here. This document gets a
maintainer to the right half of the machine; the procedure belongs to whoever owns it.

See also: S2, A-207, A-201, `scratch`, SOP-02, SOP-04.
