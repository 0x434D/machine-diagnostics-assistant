# The knowledge base

Everything interpretive in this system lives here, as Markdown. The analysis computes what
follows necessarily from the data; these documents supply what requires experience to
interpret. Adding diagnostic competence means adding a file — that is the project's central
claim, and this directory is the mechanism that makes it literally true.

**This file is not a knowledge document.** It has no `id`, and the loader must ignore any
file without one.

## Layout

| Directory | Holds | Count |
|---|---|---|
| `core/` | the always-loaded method and evidence rules | 2 |
| `sops/` | one per investigation type | 6 |
| `stations/` | what each station does, and its failure modes | 4 |
| `alarms/` | one per alarm code, with documented checks | 11 |
| `defects/` | one per defect class | 6 |
| `patterns/` | pattern → hypothesis → checks to run | 10 |

## Front-matter

Routing is deterministic and comes from the documents themselves. There is no routing table
in code.

```yaml
---
id: DP-02
title: Misalignment concentrated on one carrier
applies_to:
  defect_classes: [misalignment, scratch]
  dimensions: [carrier]
---
```

| Key | Meaning |
|---|---|
| `id` | unique across the whole tree; also the MCP resource key |
| `title` | one line, human-readable |
| `always_load` | `true` only in `core/`. The method never arrives through retrieval |
| `applies_to.question_types` | from the fixed set in §6.1 of the spec |
| `applies_to.defect_classes` | `gap` `crack` `misalignment` `missing_part` `scratch` `contamination` |
| `applies_to.dimensions` | `carrier` `lane` `lot` `defect_class` `time` |
| `applies_to.stations` | `S1` `S2` `S3` `S4` |
| `applies_to.alarm_codes` | e.g. `A-207` |

The spec names the first four keys. `stations` and `alarm_codes` are extensions this tree
needs so that station and alarm documents can route at all; they follow the same shape and
nothing else about the mechanism changes.

## `always_load` is the critical flag

The two `core/` documents are loaded for every question and must never depend on retrieval
succeeding. A retrieval miss that silently becomes a missing method is the single most common
failure of runbook-driven agents: the agent improvises, and it sounds exactly as confident as
usual.

Free-text search still exists, for direct knowledge questions — *"what does contamination
mean?"* — never for the procedure.

## Ids and resources

`id` is the resource key: `sop://SOP-01`, `defect://misalignment`, `pattern://DP-02`,
`station://S2`, `alarm://A-207`. Defect ids are the class name so the URI resolves directly.

Filenames carry a slug for readability and are not load-bearing — the `id` is.

## Editing

Hot-reloaded at runtime; an SOP edit must not require a restart, because that is the edit made
dozens of times while tuning. Reload swaps an immutable index rather than mutating one, so a
reload mid-question is safe.

Keep documents small. A retrieval budget caps document count and total size, and ranking is by
specificity — context dilution degrades these systems, so the budget is a correctness measure
rather than an economy. A document that grows past a couple of screens is usually two
documents.

## What does not belong here

- **Repair procedures.** These documents get a maintainer to the right half of the machine.
  What to adjust belongs to whoever owns it, and CORE-01 forbids recommending an undocumented
  action.
- **Invented failure modes.** Every first check in `defects/` came from someone who does
  optical quality inspection for a living. Where a cause is not known, the documents say so
  rather than filling the space — which is the same rule the answers follow.
