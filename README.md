# Machine Diagnostics Assistant

A learning project: an agent that answers questions about a simulated factory line from
the line's own recorded history, and cites the evidence for every claim it makes.

**Not production-ready, and not trying to be.** It exists to find out whether the hard
parts hold up when nothing is faked — a real OPC UA boundary between two stacks, and an
agent that cannot quietly invent an answer.

Currently at M1, the walking skeleton: thin everywhere, faked nowhere.

## Shape

Two Docker Compose stacks, joined by exactly one network carrying exactly one protocol.

- **`plant/`** — an OPC UA server simulating an assembly line, plus a separate inspection
  service that classifies rendered part images. Python.
- **`diagnostics/`** — a C# edge gateway that subscribes, backfills history and writes to
  Postgres; an analysis API; an agent pipeline; a chat UI. Nothing above the gateway knows
  OPC UA exists.

The diagnostics stack answers with the plant shut down. That is the whole point: it
answers from recorded history, not by asking the machine.

## Checks

```
make check    lint + types + tests — the gate, green before every commit
make ci       everything the CI pipeline runs, locally, with no remote
make fmt      format in place
```

Needs `uv` and Docker, and one host entry — `make preflight` tells you which.

## Reading it

| | |
|---|---|
| `docs/superpowers/specs/` | the design spec — source of truth |
| `docs/ENGINEERING.md` | how the code gets written, and why each tool was chosen over its alternatives |
| `CLAUDE.md` | the day-to-day rules |

## AI assistance

Built with AI assistance (Claude Code). The design decisions, the reviews and the
judgement calls are mine. The spec and the engineering handbook record the reasoning
behind both — including the places where the AI was wrong and the record says so.

## License

MIT — see [LICENSE](LICENSE).
