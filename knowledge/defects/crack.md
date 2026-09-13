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

**Location is the diagnosis.** Look at where on the component the crack runs — and read that
location against the joint's **risk zones**, not against the machine.

## Risk zones

A pressing operation cracks parts where the **highest stress coincides with the least
material**. That combination is a property of the part and the joint rather than of the day: it
is the same place every time, and it is computable **before any part is made** rather than
discovered by finding cracked ones.

For an interference fit, the zones are known from the mechanics:

- **The bore surface of the outer member.** Pressing the inner part in expands the outer one,
  and the resulting hoop — circumferential — tension is **highest at the bore and falls off
  towards the outside**. A thin wall around the bore is the classic risk zone, because that is
  where high stress and little material meet. This follows from the thick-walled-cylinder
  (Lamé) relations, so it can be computed analytically for simple geometry and by FEA for
  anything else.
- **The two ends of the engagement.** The edges of a press fit are stress concentrations and a
  classic crack-initiation site.

Two consequences, and both matter more than the fault they diagnose:

1. **The inspection can be aimed.** Knowing the risk zones means looking at them deliberately
   rather than hoping a crack is large enough to catch attention anywhere on the part.
2. **Location becomes evidence rather than description.** A crack *in* a risk zone and a crack
   *outside* every risk zone are different findings with different causes.

## Reading the location

**In a risk zone — bore surface or fit edge.** The joint exceeded what the material tolerates
there. Interference is the difference between the two components' dimensions, so the cause is
usually dimensional and the data resolves which side:

- **Force above nominal** → too much interference. The components are tighter together than
  intended: an oversized inner part, an undersized bore, or both inside tolerance and stacked
  the wrong way.
- **Force normal, material gave way anyway** → the material, not the fit. Properties below
  what the zone assumes. Check what the affected parts share, lot above all.
- **Force normal and the part was not properly supported** → load travelled through the part
  along a path it was never meant to take. The force record looks entirely innocent, which is
  what makes this one easy to miss.

**Outside every risk zone** — this is not the joint doing what joints do. Look upstream and
sideways: the component arrived damaged, it was damaged in handling, or it was mislocated so
that load arrived somewhere it should never have been.

## `crack` and `gap` are two ends of one axis

Worth holding onto, because it turns two defect classes into one measurement:

| Interference | Result | Class |
|---|---|---|
| too little | components do not seat fully | `gap` |
| correct | — | — |
| too much | hoop stress at the bore exceeds the material | `crack` |

Both are dimensional, both track the **component lot** rather than the clock, and both appear
in the **force–distance curve** (S2) — as where the press met resistance, and how steeply the
load built once it did. So a window showing `gap` *and* `crack` rising together is not two
problems; it is one dimensional spread that is too wide in both directions, which is a
different finding and a different conversation with the supplier.

## Then, either way

1. **Consistency across parts.** Same location, same orientation, part after part, means a
   mechanism. Scatter means handling.
2. **What the affected parts share** — lot, lane, carrier — with significance, not by eye.
3. **Scope the containment** if a lot is implicated. A fractured component is not a defect
   anybody wants to find in the field.

## Two assumptions this document makes

- **That the joint is an interference fit.** The spec says S2 "presses the parts together" and
  records a joining force and distance, which is what an interference fit looks like — but it
  does not say so outright. If the joint is something else, the risk zones are elsewhere and
  this section needs rewriting.
- **That the risk-zone map for this part exists somewhere.** It is not in this knowledge base.
  Without it, "in a risk zone" cannot be evaluated and this document degrades to "look at the
  crack and see whether the location repeats" — weaker, and to be stated as weaker rather than
  papered over. Filling it means one document per part geometry, from the analysis that was
  done wherever the joint was designed.

## What it is not

Not a scratch. Surface marking and fracture are different classes with different causes; if
the distinction is not clear from the image, it is clear from the part.

## Honest gap

The repair — what to adjust, what to replace — is not documented here. This document gets a
maintainer to the right half of the machine; the procedure belongs to whoever owns it.

See also: S2, A-207, A-201, `gap`, `scratch`, DP-05, SOP-02, SOP-04.
