---
id: SOP-05
title: Answering a trend or statistics request
applies_to:
  question_types: [trend, statistics]
---

# SOP-05 · Answering a trend or statistics request

These two question types ask for figures rather than a diagnosis. The discipline is
different: the risk is not a wrong cause, it is a confident number that nobody computed.

## Procedure

1. **Resolve the window and check coverage.** A gap inside an aggregate silently changes
   every figure derived from it. Aggregates hide gaps better than timelines do.
2. **Get the figures from the analysis.** Every number in the answer comes from a tool
   result. You do not add, average, convert units or extrapolate — the moment you do, the
   figure has no citation and cannot be verified.
3. **Choose the chart from the vocabulary** before reaching for a free-form specification:
   timeseries with event bands, state Gantt, Pareto of defect classes, stacked bar by
   carrier or lane, rate over time, summary tiles.
4. **Bind the chart to the tool call by id.** Never type values into a chart.
5. **Say what the figures do and do not show.**

## Reading a trend honestly

- A trend over a window containing a stop is not a process trend; it is a process trend plus
  an interruption. Shade the stop rather than letting it look like a signal.
- Against a baseline of roughly 1.5 % scrap and genuine carrier-to-carrier variation, small
  movements are noise. If asked whether something is rising, say whether the movement clears
  the noise, not merely which direction it points.
- Two points are not a trend, and neither is a window shorter than the thing being observed.

## Statistics requests

"Production and error statistics for the last two hours" wants a compact picture: produced,
good, rejected, scrap rate, defect classes by frequency, stops and micro-stops, availability.
Lead with the shape — a Pareto of classes or a rate over time — and keep the prose to what
the chart does not already say.

Attach the caveat when the window is short. Two hours at a 6 s takt is roughly 1,200 parts,
which is plenty for a rate and thin for a per-carrier breakdown across twelve carriers.

## What would be a failure

A number in the prose that does not appear in any tool result. A chart whose values were
composed rather than referenced. A rate quoted to three decimals from a sample of forty.
