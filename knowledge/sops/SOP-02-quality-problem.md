---
id: SOP-02
title: Investigating rising scrap or a quality problem
applies_to:
  question_types: [quality_investigation]
---

# SOP-02 · Investigating rising scrap or a quality problem

The question is usually "scrap is up, why" or "we are seeing more X". The order below is
deliberate: it puts the two cheap disqualifying checks before the expensive reasoning.

## Procedure

1. **Resolve the window and check coverage.** A gap makes every rate in the window wrong.
2. **Establish that the rate actually moved.** This line runs a baseline of roughly 1.5 %
   scrap plus configured false rejects, drawn from distributions unrelated to any fault.
   A rate inside that band is not a quality problem and must be reported as such.
3. **Ask which classes moved**, not just how many parts. Six classes rise for different
   reasons and a total tells you nothing about which.
4. **Rule out the imaging chain if an appearance class moved.** `scratch` and
   `contamination` are judged from surface appearance. Before investigating production,
   establish whether the imaging changed — see DP-04 and the master-part check in CORE-01.
   Skipping this step is how a clean line gets taken apart.
5. **Ask what the defects track**: carrier, lane, lot, or time alone. Use the pattern
   analysis with its significance verdict; do not eyeball a group-by.
6. **Then, and only then**, apply the pattern document that fits what the data showed.
7. **If material is implicated, scope the containment.** A quality answer that identifies a
   bad lot and does not say which assemblies contain it is half an answer at three in the
   morning. See SOP-04.

## The check that is easy to skip

When `gap` rises, the obvious reading is that the press is drifting (DP-01). Before
accepting it, look at whether the joining force actually moved. **A stable force with rising
`gap` is a material signal, not a press signal** (DP-05) — and it is the case this line is
built to test, because the obvious answer is the wrong one.

The general form: a defect class has more than one cause, and the symptom alone does not
choose between them. Ask what separates them, then go and get that evidence.

## What the answer must contain

- Which classes moved and by how much, against the baseline — not a bare count.
- What the defects track, with sample size and significance.
- Every interpretation labelled as a hypothesis with its evidence strength.
- The imaging check, when an appearance class is involved, and its result.
- Containment scope if material is implicated.
- Plainly: "no significant pattern" when there is none.
