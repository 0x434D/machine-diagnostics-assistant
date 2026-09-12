---
id: DP-10
title: An apparent concentration that is within normal spread
applies_to:
  defect_classes: [gap, crack, misalignment, missing_part, scratch, contamination]
  dimensions: [carrier, lane, lot, defect_class, time]
  question_types: [quality_investigation, trend, statistics]
---

# DP-10 · An apparent concentration that is within normal spread

## Pattern

A dimension — carrier, lane, lot, class, time bucket — sitting visibly above the others, and
**failing the significance gate**. Small sample, modest effect, no verdict.

## Hypothesis

There is nothing here. The spread is the line's normal variation.

This document exists because that is a real, correct and frequently required answer, and
because it is the answer these systems are worst at giving.

## Why this is a pattern and not an absence of one

This line runs genuine carrier-to-carrier variation that is **not** a fault, plus a baseline
of roughly 1.5 % scrap and configured false rejects, all drawn from distributions unrelated to
any injected problem. So *something is always highest.* With twelve carriers, one of them tops
the table every window, and it means nothing.

Without background variation, finding a bad carrier would be a `GROUP BY`. With it, the
question needs a significance test — and the honest answer is often that the test did not pass.

## Checks to run

1. **Read the significance verdict, not the ranking.** Observed share, expected share, n,
   effect size, verdict. The ranking is not evidence.
2. **Check the minimum-sample gate.** Below it, there is no result to report at all — not a
   hedged one.
3. **Consider the multiplicity.** Twelve carriers, two lanes, six classes and several time
   buckets is a lot of chances for something to look interesting.
4. **Widen the window** if the sample is thin and the question allows it. Then re-test rather
   than re-reading the same table.

## What to say

> "Carrier 7 is 4 % above average, n=38, not significant — that is within normal spread."

Complete, correct, useful. Do **not** soften it into a suggestion, do not offer it as
"something to keep an eye on" unless asked, and do not attach a plausible mechanism to a
result that did not clear the gate. A mechanism makes a non-result sound like a finding, and
the person reading it will act on the mechanism.

## What would refute this

The test passing on a later or wider window. Then cite the pattern that fits, and say that the
earlier window was inconclusive rather than pretending it agreed.

See also: CORE-02, DP-02, DP-03, DP-06, SOP-02.
