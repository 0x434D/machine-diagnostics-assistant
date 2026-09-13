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

```
make verify      §1's authenticity proofs — stops containers, minutes long
make m1-report   the measured risk table
```

Needs `uv` and Docker. No host entries, no hostname editing: `opc.tcp://localhost:4840/plant`
works because the certificate's IP SAN covers it.

## What M1 measured

M1 exists to measure the four risks at the boundary before anything depends on them. Full
numbers in [`docs/superpowers/measurements/`](docs/superpowers/measurements/); `make
m1-report` regenerates the table and **exits non-zero if any risk has neither a pass nor a
recorded, justified deviation**.

| Risk | Outcome |
|---|---|
| **R1** asyncua history at ~20k rows/stream | **the risk was misplaced.** 19,800 rows per stream backfilled in 60 s against a 300 s budget, p99 1.3 s, sublinear. `read_rows == pg_rows` exactly, zero gaps. The danger is silent miscounting, not latency |
| **R2** UA-.NETStandard `HistoryRead` ergonomics | **confirmed larger than estimated.** No helper exists at 1.5.378.176; paging, decoding, continuation-point release and per-stream page sizing are all hand-written |
| **R3** endpoint URL vs. Docker hostname, cert SANs vs. service names | **pass.** All four client positions connect at `Sign`, including the real .NET client inside `field-net` with `checkDomain` on. Every `NoSecurity` position is refused |
| **R4** structured events carrying image bytes | **pass, 1,005× headroom** — but through a different limit than the spec named, because asyncua enforces neither `MaxByteStringLength` nor `MaxMessageSize` |

### What M1 found that the spec did not predict

Three ways this stack discards data and reports success: a read ceiling at 10,000 values;
event-history paging that returns one page and no continuation point; and a write queue that
caps at 10,000 and discards the **oldest**. The middle one cost 96% of the event history on a
run that reported success. All three are detectable from the client side, which is why the
guards live in the gateway rather than in a request that the server behave — that distinction
is what makes "point it at a real plant" a claim rather than a hope.

The recurring shape, six times: **the check was in the code but not in the path that decided.**
A guard that fired at 10,000 could not see a stop at 25. A probe asked for a security mode and
never asserted it got one, so an insecure request negotiated a signed session and exited 0. A
status field described the session rather than the pipeline. Only mutation found any of them.

## Known limits

- **M1 is one station, two signals and one event.** Four stations, buffers, carriers, PackML
  and the eight scenarios are M2.
- **The agent is not wired up.** The analysis API and its contract exist; the pipeline that
  calls a model does not.
- **No authentication anywhere yet.** That is M5, deliberately before the UI so it is not
  retrofitted.
- **Closed-window caching is not implemented.** It pays off under concurrency and M1 has one
  user.
- **Scenario 6 is stipulated, not emergent** — the confidence decay is configured rather than
  arising from a real model.

## Security posture, plainly

The OPC UA boundary is signed (`SecurityMode Sign`) with self-signed application-instance
certificates and mutual trust lists, so both sides authenticate each other by certificate.
Messages are signed but **not encrypted**: `SignAndEncrypt` and certificate-based *user*
authentication are the documented production step, not something done here.

Nothing crosses towards the plant. No writes, no method calls, no scenario control, no ground
truth. The gateway holds a read-only bind mount of the PKI and its own private key is 0600.

There is no user authentication on any HTTP endpoint yet, so **do not expose this to a network
you do not control.**

## Reading it

| | |
|---|---|
| `docs/superpowers/specs/` | the design spec — source of truth |
| `docs/ENGINEERING.md` | how the code gets written, and why each tool was chosen over its alternatives |
| `CLAUDE.md` | the day-to-day rules |

## AI assistance

Built with AI assistance (Claude Code), across parallel sessions holding separate parts of
the repository. The design decisions, the reviews and the judgement calls are mine.

The record is kept openly rather than tidied. The spec now carries measured numbers where it
carried guesses, and says so: §3.2's history depth was wrong for the purpose §3.2 states, and
§12 gained three risk rows nobody predicted. Commit messages say what was assumed and what was
measured instead. Where a plan step turned out to be wrong — a build context that cannot work,
a page size that cannot serve an image-bearing stream, an SDK pin no official image could
satisfy — the commit says which was wrong and why, rather than quietly working around it.

Every number in this README was measured on this repository. None is carried over from an
estimate.

## License

MIT — see [LICENSE](LICENSE).
