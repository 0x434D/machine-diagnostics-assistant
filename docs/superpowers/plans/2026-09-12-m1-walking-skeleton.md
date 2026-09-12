# M1 Walking Skeleton — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the whole machine → edge → storage → analysis → agent → UI chain one station wide, with nothing faked, so that the four boundary risks in §12 are measured before anything is built on top of them.

**Architecture:** Two Docker Compose stacks joined by exactly one external network carrying exactly one protocol. The plant stack runs an asyncua OPC UA server for a single station (S3 Inspection) plus a separate inspection service that classifies rendered part images. The diagnostics stack runs a C#/.NET edge gateway that subscribes, backfills via `HistoryRead`, buffers to a local SQLite queue and writes raw + normalised rows to Postgres in one transaction; a FastAPI analysis service; a minimal agent pipeline with one tool and real citation verification; and a chat UI. Nothing above the gateway knows OPC UA exists.

**Tech Stack:** Python 3.13 + `uv` (simulator, inspection, analysis, agent), C#/.NET 9 + OPC Foundation UA-.NETStandard 1.5.378.176 (gateway), PostgreSQL 17, TypeScript + React + Vite + pnpm (UI), Docker Compose ×2, `claude-sonnet-5` via the `anthropic` SDK.

**Spec:** `docs/superpowers/specs/2026-09-12-machine-diagnostics-assistant-design.md` (at commit `385cea3`, which adds §10.7)

---

## Global Constraints

Every task's requirements implicitly include this section. Values are copied verbatim from the spec.

**Boundary (§2.1, §4.5)**
- Exactly three networks: `plant-net` (internal), `diag-net` (internal), `field-net` (**external**).
- Exactly two containers join `field-net`: the simulator and the edge gateway. A third is an architecture violation.
- Across the boundary: OPC UA only, read-only. No writes, no method calls, no images for good parts, no ground truth, no scenario control, no second protocol.
- The diagnostics stack must start, run and answer questions with the plant stack completely down.

**Time (§3.2, §4.2)**
- Live speed is exactly 1.0.
- All timestamps stored as UTC; all shift logic in `Europe/Berlin`. Shifts: early 06–14, late 14–22, night 22–06.
- `SourceTimestamp` = simulated time. `ServerTimestamp` = real wall clock. **All analysis uses `SourceTimestamp`, always.**

**Security (§4.6, §10.5)**
- `SecurityMode Sign` with self-signed application instance certificates and mutual trust lists.
- Not in scope: `SignAndEncrypt`, OPC UA user authentication, TLS between containers.
- Trust stores live on volumes so first-connect trust survives restarts.
- Certificate generation is a scripted setup step, not a manual one.
- Every HTTP service binds to localhost.

**Toolchain (§10.7)**
- Python version pinned in `.python-version` and `requires-python`; **uv provisions the interpreter**, the base image does not dictate it. Default 3.13.
- `uv.lock` is committed. Builds run `uv sync --locked`, which **fails** on drift. `--frozen` uses whatever is present without checking, so it belongs only in the dependency-only Docker layer where the lockfile is bind-mounted deliberately.
- uv binary copied from its official pinned image: `COPY --from=ghcr.io/astral-sh/uv:<pinned> /uv /uvx /bin/`. Never fetched by a script at build time.
- Dockerfile layering: copy `pyproject.toml` + `uv.lock`, run `uv sync --frozen --no-install-workspace`, *then* copy source and `uv sync --locked`.
- Container env: `UV_COMPILE_BYTECODE=1`, `UV_LINK_MODE=copy`.
- Multi-stage: build with uv, run from a slim image carrying only the resolved environment.
- **Two workspaces, one per stack** — `plant/` and `diagnostics/` each own a uv workspace. Never one at the repository root.
- .NET pinned by `global.json`, locked by `packages.lock.json`, restored with `--locked-mode`.
- Node pinned by `.nvmrc` + `engines` + pnpm via corepack, locked by `pnpm-lock.yaml` + `--frozen-lockfile`.
- **Every base image is pinned by digest, not by tag.**

**Quality gates (§10.8)**
- `make check` (= `lint` + `test`) must be green before every commit. `make fmt` formats in
  place, `make lint` checks without changing.
- Python: `ruff format`, `ruff check`, `mypy --strict`. C#: `dotnet format
  --verify-no-changes`, plus `Nullable=enable`, `TreatWarningsAsErrors=true`,
  `AnalysisMode=All`. TypeScript: `oxlint check`, `tsc --noEmit` with `strict`.
- Warnings are errors. No blanket suppressions — every `# type: ignore`, `# noqa`,
  `oxlint-ignore` or `#pragma warning disable` carries a specific rule code and a reason.
- New code is typed: no untyped signatures in Python, no `any` in TypeScript.
- **Every task ends with `make check` green, then its commit.** A commit asserts the gates
  passed. Never `--no-verify`.

**Configuration (§10.3)**
- Each stack has its own `.env`. No secrets in the repository.
- Everything with a number in it is configuration, not a constant buried in code: takt, catch-up speed, history depth, deadbands, page sizes, reject rate, seed.

**Determinism (§3.6, §10.6)**
- Seeded RNG throughout. Same seed + same scenario reproduces the run exactly.

**Observability (§10.4)**
- Structured JSON logs everywhere, with a correlation id threading a question through agent → analysis → database.

**Model (§6.9)**
- Default model `claude-sonnet-5`. On this model: `thinking={"type":"adaptive"}` is the only on-mode (`budget_tokens` returns 400), assistant prefill returns 400, `temperature`/`top_p`/`top_k` return 400. Forced `tool_choice` is accepted.

**Pinned versions** (resolved 2026-09-12; re-resolve digests with `scripts/pin-images.sh` before first build)

| Artifact | Pin |
|---|---|
| Python | 3.13 (`.python-version`), `requires-python = ">=3.13,<3.14"` |
| uv | 0.11.13 |
| `ghcr.io/astral-sh/uv:0.11.13` | `sha256:841c8e6fe30a8b07b4478d12d0c608cba6de66102d29d65d1cc423af86051563` |
| `debian:bookworm-slim` | `sha256:88200866dfff7ea7f5cbcb6ec7c8a701889efe6fe859fe64d6990e4b07ea4171` |
| `postgres:17-bookworm` | `sha256:051f7b7b3abdd564d5d1bd1e8c4b9c1b6e77087d1dd22020ede611c096a272e0` |
| `mcr.microsoft.com/dotnet/sdk:9.0` | `sha256:20387c6674c30e46def0cc8cb557bd2a69b35afdf18c19c3694638d0a90897e4` |
| `mcr.microsoft.com/dotnet/aspnet:9.0` | `sha256:779b0b298964fe93e6c6388c8bb823393a7c301b79cd5b3d80632bc886b2ffd4` |
| `node:22-bookworm-slim` | `sha256:83f487e0a63425e5b4d146fb5e5be574bcbe1b7b843d3ebafdd95eaf7767a7e5` |
| `asyncua` | 2.0.1 (declares `requires-python >=3.10` — 3.13 is in range) |
| .NET SDK | 9.0.116 |
| `OPCFoundation.NetStandard.Opc.Ua.Client` | 1.5.378.176 |
| `OPCFoundation.NetStandard.Opc.Ua.Configuration` | 1.5.378.176 |
| `Npgsql` | 10.0.3 |
| `Microsoft.Data.Sqlite` | 10.0.12 |
| `anthropic` | 1.5.0 |
| `fastapi` | 0.141.1 |
| Node | 22, pnpm 11 via corepack |

---

## Scope

**In M1** — one station (S3 Inspection), two signals, one event carrying an image; OPC UA across two stacks with `Sign` and mutual certificate trust; gateway with durable queue, reconnect, `HistoryRead` backfill, gap markers, overflow detection and `/status`; Postgres with raw landing + normalised model in one transaction; the analysis contract with a stats endpoint and a citation resolver; a minimal agent pipeline with one tool and real citation verification; a chat box; four of the nine §1 authenticity proofs as executable tests; and a measurement report that sets the spec's open numbers.

**Explicitly not in M1** — the other three stations, PackML state machines, buffers, carriers, serial genealogy, component lots, scenarios, the noise floor, the plant HMI, propagation analysis, significance testing, the knowledge base, routing, the composer, MCP, Zitadel and any authentication, charts, the evidence panel beyond one citation, the eval harness, and the improvement loop.

**Judgement calls made against an underspecified §13** — each is justified where it appears and listed again in *Spec ambiguities* at the end:
- The inspection service ships in M1 (Task 5), because "one event with an image" plus §3.4's two-box split cannot both hold without it.
- Station topology discovery ships in M1 (Task 11), because the alternative is hardcoding `S3` in the gateway and ripping it out in M2.
- Two analysis endpoints ship, not one (Task 12), because §6.5 citation verification and §7.2 clickable evidence force a resolver alongside the tool.
- The simulated clock ships in M1 (Task 3), because catch-up is what produces the history that R1 and R2 measure.

---

## Pre-flight findings

These were measured against `asyncua` 2.0.1 on Python 3.14 before this plan was written, using the probe reproduced in Task 10. They are stated here because they change what several tasks must do. **Re-run them on the pinned Python 3.13 in Task 1** — the numbers below are indicative, not the M1 result.

**F1 — `read_raw_history` silently truncates at 10,000 values.** 10,800 values were written to the historian; a full paged traversal returned exactly 10,000, with no exception and no bad `StatusCode`. The ceiling held at 10,000 for storage page sizes of both 1,000 and 2,000, and for depths of both 10,800 and 12,000. **18 h at 6 s takt is 10,800 values per signal — just over the ceiling.** Consequence: the gateway must never issue an unbounded history traversal. It issues bounded time windows and reconciles a count per window (Task 10).

**F2 — paged reads return duplicates at every page boundary.** 3,000 values written, 3,007 returned with a 500-row page. The continuation point is the `SourceTimestamp` of the first row of the next page, and the next query re-includes it (`BETWEEN` is inclusive). Consequence: §4.4's upsert on `(station, signal, source_timestamp)` is not an edge-case defence against backfill overlapping live data — it fires on every page boundary of every backfill. Task 9 builds it; Task 10 proves it absorbs them.

**F3 — the historian's continuation point is timezone-naive.** `HistorySQLite.read_node_history` returns a naive `datetime` (SQLite `PARSE_DECLTYPES`), so feeding it back in-process raises `TypeError: can't compare offset-naive and offset-aware datetimes`. The wire path is unaffected — `Primitives.DateTime.unpack` returns a UTC-aware value — so this bites only code that calls the storage directly. Consequence: the catch-up generator must not page the storage in-process (Task 4).

**F4 — writes are not the bottleneck.** Stock `HistorySQLite.save_node_value` sustained ~10,000 rows/s despite committing per row, and the full `write_value` → internal subscription → historian path lost zero rows out of 2,000. The earlier hypothesis that the internal historian subscription coalesces fast writes is **wrong** — datachange notifications fire per change, not per publishing interval. R1's risk is correctness, not throughput.

**F5 — retention is wall-clock, history is simulated-clock.** `save_node_value` deletes rows where `SourceTimestamp < now() - period` using the real `now()`. Rows written with simulated timestamps 18 h in the past survive the 7-day default, but any `period` shorter than the history depth silently erases the history as it is written. Consequence: historise with `period=None, count=0` explicitly, and comment why (Task 4).

---

## Boundary risk measurement plan

This is what M1 is for. Each risk states what is measured, what counts as a result, and what happens if the result is bad. The measurements are embedded as the acceptance tests of the tasks that create the risk surface; Task 17 aggregates them into a report and writes the surviving numbers back into the spec.

### R1 — asyncua history backend at ~10k events (§12 row 1)

*Risk as stated: backfill slow or unreliable.* F1–F4 relocate it: the danger is silent miscounting, not latency.

**Measured**

| Quantity | How |
|---|---|
| `plant_rows` | count in the plant historian per stream, from the simulator's own generator ledger |
| `read_rows` | count the gateway receives from `HistoryRead` for the same window |
| `pg_rows` | count in Postgres after upsert |
| `duplicate_rate` | `read_rows − plant_rows`, expected ≈ pages − 1 |
| `catchup_wall` | wall seconds to generate the configured depth |
| `backfill_wall` | wall seconds from gateway connect to backfill complete |
| `page_p50`, `page_p99` | per-`HistoryRead`-call latency |
| `linearity` | per-row backfill cost at 18 h ÷ per-row cost at 1 h |

**Counts as a pass**
- `pg_rows == plant_rows` **exactly**, for all three streams, with zero `ingest_gaps` rows. This is the criterion that matters; F1 means it does not hold by default.
- `catchup_wall` ≤ 180 s for the configured depth (the spec's 600× aspiration is ≈108 s for 18 h; record the achieved multiple).
- `backfill_wall` ≤ 300 s, `page_p99` ≤ 10 s.
- `linearity` ≤ 2.0.

**If it fails**
- `pg_rows < plant_rows` → the window chunker is wrong or a cap is still binding. Halve the window size and re-measure. If a fixed cap survives chunking, construct `HistorySQLite` with an explicit larger `max_history_data_response_size` and re-measure; if the ceiling persists, replace the backend with a custom `HistoryStorageInterface` (six methods: `new_historized_node`, `save_node_value`, `read_node_history`, `new_historized_event`, `save_event`, `read_event_history`) over SQLite with an index on `SourceTimestamp` and a rowid cursor. **This is a blocker, not a tuning knob** — ship no milestone on a backfill that miscounts.
- `pg_rows > plant_rows` → the upsert key is wrong. Fix the key, not the reader.
- `linearity > 2.0` → raise the page size and re-measure; if still superlinear, the custom backend above also fixes it.
- `catchup_wall` too slow → reduce catch-up speed or depth and record the chosen values into §3.2 and §15 via Task 17. Depth may not drop below the night-shift floor derived in Task 3.

### R2 — UA-.NETStandard `HistoryRead` client ergonomics (§12 row 2)

*Risk as stated: gateway work larger than estimated.*

Established before planning: in 1.5.378.176 the reference client ships **no** `HistoryRead` example — `ClientSamples.cs` has none, and the `HistoryClient` helper in the current `master` branch belongs to the unreleased 2.0 line. `ISession` exposes only the raw service `HistoryReadAsync(RequestHeader, ExtensionObject historyReadDetails, TimestampsToReturn, bool releaseContinuationPoints, HistoryReadValueIdCollection nodesToRead, CancellationToken)`. Everything above that the gateway writes itself.

**Measured** — four mechanics, each pass/fail, plus a size figure.

| # | Mechanic | Evidence of pass |
|---|---|---|
| M1 | Paged `ReadRawModifiedDetails` round-trip | exact expected row count for a known window |
| M2 | Paged `ReadEventDetails` with an `EventFilter` whose `SelectClauses` match the custom event type | every event field arrives non-null and correctly typed, image bytes included |
| M3 | Continuation points released on abort | `releaseContinuationPoints: true` path exercised; server continuation-point count returns to zero |
| M4 | Both timestamps preserved end to end | `SourceTimestamp` = simulated, `ServerTimestamp` = wall, differing by ≈ the catch-up offset |

Plus: `history_loc` = non-generated lines of C# in the history module, and a written list of *what the SDK does not do for you* — the actual answer to "ergonomics".

**Counts as a pass** — all four mechanics green and `history_loc` ≤ 400.

**If it fails** — timebox two working days, then in order: (a) evaluate `2.0.0-preview.2`, which ships the `HistoryClient` helper, and record that M1 depends on a preview package; (b) fall back to variables-only backfill and reconstruct events from the raw landing table, recording that this weakens §4.3's "one mechanism, three situations"; (c) if M2 specifically is the blocker, keep events on the live subscription only and accept that an event gap cannot be closed after an outage — this contradicts §4.4 and must be escalated, not absorbed.

### R3 — endpoint URL vs Docker hostname (§12 row 3)

*Risk as stated: `docker compose up` does not just work.*

**Measured** — a 3 × 2 matrix, every cell recorded with the exact `StatusCode` on failure.

| Client position | Dialled URL | NoSecurity | Sign |
|---|---|---|---|
| gateway container on `field-net` | `opc.tcp://line-simulator:4840/plant` | | |
| host, published port | `opc.tcp://localhost:4840/plant` | | |
| host, with `127.0.0.1 line-simulator` in `/etc/hosts` | `opc.tcp://line-simulator:4840/plant` | | |

Plus: whether `set_match_discovery_client_ip(False)` is required (asyncua's own source documents it for "behind NAT or inside Docker container"), and whether both stacks reconnect after `docker compose down && up` **without re-trusting by hand**.

**Counts as a pass** — row 1 green at `Sign`; at least one host row green at `Sign`; restart survives with no manual trust step.

**If it fails** — row 1 failing is a blocker; work the matrix cell by cell against the design in *The one-name boundary* below. If only the host rows fail at `Sign`, record it and keep host access for browsing at `NoSecurity` — but note that this weakens §1's first authenticity proof, so prefer fixing the `/etc/hosts` row.

### R4 — structured events carrying image bytes (§12 row 4)

*Risk as stated: rejects arrive without evidence.*

**Measured**

| Quantity | How |
|---|---|
| `img_p50`, `img_p99` | rendered PNG size at the configured resolution, over ≥500 rejects |
| `max_publish_bytes` | largest observed publish response |
| `ceiling` | binary search on image size until delivery fails; record the size and the exact failing `StatusCode` and which limit produced it |
| `burst_loss` | rejects lost when B rejects land inside one publishing interval, B = queue size |
| `overflow_seen` | whether `StatusCode.Overflow` is ever set, and whether a gap marker follows |

Known limits to record against: UA-.NETStandard's reference config sets `MaxByteStringLength` 4194304, `MaxMessageSize` 4194304, `MaxBufferSize` 65535; asyncua's `TransportLimits` defaults to 65535 send/recv buffers with a 100 MB max message, so chunking is on by default.

**Counts as a pass** — `ceiling ≥ 4 × img_p99`; `burst_loss == 0` at B = 20; and if `overflow_seen` is true, a gap marker exists for exactly that interval.

**If it fails** — raise `MaxByteStringLength` and `MaxMessageSize` on both ends and re-measure; if the ceiling still crowds `img_p99`, lower the render resolution. Either way the chosen values become configuration (§10.3) and are recorded in Task 17. `burst_loss > 0` without an overflow bit is a blocker: it means loss is undetectable, which breaks §4.4's premise.

---

## Which station M1 uses, and why

**S3 Inspection.** The argument, against the alternatives:

**R4 is only reachable at S3.** Images exist nowhere else in the line (§3.4). §13's M1 line requires "one event with an image", and §12 assigns R4 to M1 "with one station". Any station other than S3 leaves a named boundary risk unmeasured, which is the one thing M1 exists to prevent.

**S3 already has exactly two signals, and they are usefully different in kind.** §4.1 gives S3 `TaktTime` and `PartCount`. `TaktTime` is a noisy float where a deadband is meaningful; `PartCount` is a monotonic counter where a deadband must never be applied, because one would silently lose parts. The pair therefore tests the gateway's per-signal deadband configuration in both directions — which a pair of similar floats would not. §5.1 makes deadbands the gateway's job but never says they are per-signal; M1 settles that they must be.

**S3 carries the heaviest history rows,** so it stresses R1 hardest. An event per part with a ByteString payload is the largest thing the historian and the backfill will ever move.

**The cost, stated plainly: §3.4a is not fully exercised.** S2's peak joining force and joining distance, recorded against a serial at the instant of production, are the spec's canonical per-part process values, and M1 does not build them. The counter-argument for choosing S2 anyway fails because S2's `PartProcessedEvent` carries no image, which would leave R4 unmeasured — trading a measured risk for an unmeasured one.

What survives the deferral is the part that matters. §3.4a's real claim is not "floats" but *"the association is known exactly at the instant of production and only approximately afterwards"*. S3's inspection event is written against `assembly_serial` at the moment of inspection and is never reconstructed by time-joining the series. M1 therefore builds and proves the **pattern**; M2 adds the numeric-float instance of it at S2. Task 12 asserts that `GET /parts/{serial}` reads `inspection_results` directly and performs no time-range join, so the shortcut §3.4a forbids cannot quietly appear later.

**What M1 leaves off S3.** §4.1 also lists `State` and `StateReason` for every station. Those are PackML, which §13 places in M2. M1 omits them from the address space rather than exposing them as static values — a node that never changes is a fake, and §13's standard for M1 is that nothing in it is faked.

---

## The one-name boundary

*Endpoint URL, certificate SANs and published port, solved together. Expect this to be M1's hardest task (Task 6).*

### Why the three are one problem

Four independent mechanisms all key off the same string:

1. **Session validation.** A UA client dials a URL, discovers endpoints, then creates a session. UA-.NETStandard compares what it dialled against what the server advertises. A mismatch is `BadTcpEndpointUrlInvalid` — §12 names it.
2. **The URI SAN.** asyncua's `CertificateValidator` with `CertificateValidatorOptions.URI` raises `BadCertificateUriInvalid` unless the peer's `ApplicationUri` appears verbatim in its certificate's URI SANs.
3. **The DNS SAN.** When the .NET session is created with `checkDomain: true`, the host component of the endpoint URL must appear in the server certificate's DNS SANs, or the connection fails as `BadCertificateHostNameInvalid`.
4. **The bind address.** asyncua binds its listener to the host component of `set_endpoint(...)`. The advertised URL and the listening interface are the same string.

So the endpoint URL is simultaneously a bind address, a discovery advertisement, a session-validation token and a certificate constraint. Changing it to make one work breaks another. That is why §12 lists the endpoint trap and the SAN trap as separate rows and then says to solve them together.

### The design

**One name, `line-simulator`, and one port, `4840`, used identically everywhere.**

| Where | Value | Forced by |
|---|---|---|
| Compose service name, plant stack | `line-simulator` | Docker DNS is what the gateway resolves on `field-net` |
| `server.set_endpoint(...)` | `opc.tcp://line-simulator:4840/plant` | must match what the gateway dials (mechanism 1) |
| Published port mapping | `4840:4840` | a single advertised URL names one port; `14840:4840` would make the host dial a port the server never advertises (mechanism 1) |
| Server certificate URI SAN | `urn:machine-agent:plant:line-simulator` | must equal `ApplicationUri` (mechanism 2) |
| Server certificate DNS SANs | `line-simulator`, `localhost`, IP `127.0.0.1` | must cover every host component any client will dial (mechanism 3) |
| Host `/etc/hosts` | `127.0.0.1 line-simulator` | gives the host the same name Docker DNS gives the gateway |
| Gateway certificate URI SAN | `urn:machine-agent:diagnostics:edge-gateway` | the server validates the client the same way |
| Gateway certificate DNS SAN | `edge-gateway` | symmetry; the server may check the client's domain |

Two deliberate choices inside that table:

**Multi-SAN server certificate.** asyncua's convenience helper `setup_self_signed_certificate(...)` takes a single `host_name` and emits exactly one DNS SAN. That is one name too few, so M1 calls the lower-level `generate_self_signed_app_certificate(key, app_uri, subject_attrs, subject_alt_names, extended, days)` directly with a SAN **list**. One certificate then satisfies every row of the R3 matrix, which is why the matrix is measurable at all.

**Pre-seeded mutual trust, not trust-on-first-use.** §14 requires the connection to survive a restart "without re-trusting by hand", and §4.6 requires generation to be scripted or `docker compose up` stops being one command. So each stack runs a `pki-init` service that completes before its other services start:

```
pki/                      ← repo root, bind-mounted read-write into both pki-init services
  line-simulator/         key.pem (gitignored), cert.der
  edge-gateway/           key.pem (gitignored), cert.der, cert.pfx
  trusted/                line-simulator.der, edge-gateway.der   ← both parties trust this dir
```

Each `pki-init` generates its own party's keypair if absent and copies its public certificate into `pki/trusted/`. Each runtime service trusts everything in `pki/trusted/`. Bringing either stack up first works; bringing both up converges; restarting re-uses what is there.

`pki/` is a **setup-time artifact on the host filesystem, deliberately not a Docker network.** §2.1's boundary claim is about runtime channels, and a real deployment exchanges trust out of band too. Task 15's network test asserts the claim that actually matters: exactly two containers on `field-net`.

### The ordering that makes it work

The gateway's certificate must exist before the simulator can validate it, and vice versa — but neither is running when `pki-init` runs, which is what removes the circularity. Do **not** let .NET mint its own certificate via `CheckApplicationInstanceCertificatesAsync`: it derives the DNS SAN from the container hostname, which is a random container ID, so the SAN would differ on every recreate and mutual trust would break on restart — the precise failure §14 forbids. `pki-init` writes a `.pfx` into the .NET `own` store instead, and the gateway config sets `SetAddAppCertToTrustedStore(false)`.

### The one manual step, named

`127.0.0.1 line-simulator` in the host's `/etc/hosts` is a developer-workstation prerequisite, like installing Docker. It is not a `docker compose` step and cannot be, because the host is not on Docker's DNS. §14 says "no manual steps, no fixing hostnames by hand" — this is in tension with that, and the tension is recorded in *Spec ambiguities*. The README states it as a prerequisite, `make preflight` checks for it and prints the exact line to add, and Task 6 measures whether `opc.tcp://localhost:4840/plant` happens to work anyway, which would retire the step entirely.

---

## What "M1 is done" looks like

A single sequence, run from a clean checkout, that can be watched end to end. `make m1-demo` runs it; each step below is something visible.

**0. Preflight.** `make preflight` checks Docker, the external network, and the `/etc/hosts` entry, printing exactly what to fix.

**1. The plant boots and builds its own history.**
```
docker compose -f plant/compose.yml up
```
Logs show `pki-init` generating or reusing certificates, then the clock phases in order: `booting → catchup → live`, with catch-up progress and a final line giving the achieved multiple and the exact row counts written per stream. `curl localhost:4840` is not a thing — instead `docker compose -f plant/compose.yml exec line-simulator python -m simulator.status` prints simulated time, phase, speed and history depth.

**2. A foreign client browses the address space.** UaExpert (or `make browse`, which runs an independent UA-.NETStandard browser that is *not* the gateway) connects to `opc.tcp://line-simulator:4840/plant` at `Sign` and walks `Objects/Line/Stations/S3_Inspection`. This is §1's first authenticity proof, watched rather than asserted.

**3. The diagnostics stack starts and closes the gap.**
```
docker compose -f diagnostics/compose.yml up
```
`watch -n1 curl -s localhost:8080/status | jq` shows the state machine move `disconnected → connecting → backfilling → live`, with `backfill_progress`, `queue_depth`, `last_event_source_ts`, `overflow_count` and `rows_written` all moving. Backfill completing with `plant_rows == pg_rows` is R1's pass condition, visible live.

**4. The chat box answers with a citation that opens.** `http://localhost:5173` → ask *"How many parts were rejected in the last hour, and what were the defects?"* → a streamed answer naming counts by defect class, a `caveats` entry if the window has gaps, and a citation chip for one reject serial. Clicking it opens the underlying row: serial, timestamp, defect class, confidence, and the image. §13's "one real citation", and §7.2's rule that a citation you cannot open is barely a citation.

**5. Downstream outage — the queue fills and drains.**
```
docker compose -f diagnostics/compose.yml stop postgres
```
`/status` shows `queue_depth` climbing while `last_event_source_ts` keeps advancing. Open the SQLite queue file on its volume mid-outage and see the rows. Restart Postgres; watch `queue_depth` fall to zero. Then `make verify-no-gaps` proves `pg_rows == plant_rows` still holds. §1's second authenticity proof.

**6. Upstream outage — reconnect and backfill close it.**
```
docker compose -f plant/compose.yml down
```
`/status` goes to `disconnected`. **The chat box still answers the same question** — that is §1's stack-independence proof, and it is the most important thing to watch, so the demo pauses here and asks again. Bring the plant back up; `/status` goes `connecting → backfilling → live` and the outage window closes via `HistoryRead`. §1's third authenticity proof.

**7. The numbers.** `make m1-report` prints the R1–R4 table with measured values against thresholds, and writes `docs/superpowers/measurements/2026-09-12-m1-boundary-risks.md`.

**8. The spec carries the real numbers.** `git log -1 docs/superpowers/specs/` shows a commit updating §3.2 and §15 with the measured catch-up speed, history depth and takt — §15's stated requirement that these be set from measurement rather than guessed.

**Done means:** steps 1–8 run clean from `git clone`, `make test` is green including the four authenticity proofs, R1–R4 all record a pass or a recorded, justified deviation, and the spec commit exists.

---

## File structure

```
machine-agent/
  Makefile                              preflight · m1-demo · test · m1-report · browse
  global.json                           .NET SDK pin
  .nvmrc                                Node pin
  scripts/
    pin-images.sh                       re-resolve every base image digest
    gen-certs.py                        multi-SAN generation, shared by both pki-init services
  pki/                                  setup-time trust material (private keys gitignored)

  plant/
    compose.yml                         line-simulator · inspection-service · pki-init
    .env.example
    pyproject.toml                      uv workspace root (members: simulator, inspection)
    uv.lock                             committed
    .python-version
    Dockerfile                          shared multi-stage, built per service via --package
    simulator/
      pyproject.toml
      src/simulator/
        config.py                       every number from §10.3
        clock.py                        past → catch-up → live; night-shift depth floor
        address_space.py                Objects/Line/Stations/S3_Inspection
        events.py                       custom InspectionResultEventType
        historian.py                    storage wiring, period=None, generator ledger
        station_s3.py                   the takt loop, part ids, calls inspection-service
        server.py                       endpoint, security, trust, run loop
        status.py                       `python -m simulator.status`
      tests/
    inspection/
      pyproject.toml
      src/inspection/
        render.py                       part image with defects drawn
        classifier.py                   Classifier protocol + SimulatedClassifier
        truth.py                        side channel, keyed by part id
        app.py                          POST /inspect
      tests/

  diagnostics/
    compose.yml                         postgres · edge-gateway · analysis · agent · ui · pki-init
    .env.example
    pyproject.toml                      uv workspace root (members: analysis, agent)
    uv.lock
    .python-version
    Dockerfile
    gateway/
      Gateway.csproj                    packages.lock.json committed
      Dockerfile
      Program.cs                        host, DI, /status
      Opc/
        UaConnection.cs                 config builder, session, reconnect
        Subscriptions.cs                two variables + the event, deadbands
        HistoryBackfill.cs              windowed HistoryRead, continuation points   ← R2
        TopologyDiscovery.cs            browse stations
      Ingest/
        LocalQueue.cs                   durable SQLite queue
        PostgresWriter.cs               raw + normalised, one transaction
        Reconciler.cs                   per-window row-count reconciliation         ← R1
      Status/StatusEndpoint.cs
      Migrations/001_m1.sql
      Gateway.Tests/
    analysis/
      pyproject.toml
      src/analysis/
        app.py · db.py · routes_inspection.py · routes_parts.py
      tests/
    agent/
      pyproject.toml
      src/agent/
        app.py · pipeline.py · tools.py · citations.py · provider.py · answer.py
      tests/
    ui/
      package.json · pnpm-lock.yaml · vite.config.ts
      src/  App.tsx · Chat.tsx · CitationChip.tsx · EvidencePanel.tsx
      src/generated/analysis.ts         generated from the OpenAPI contract

  contracts/
    analysis.openapi.yaml               single source of truth
    answer.schema.json

  measurements/
    run_r1_r2.py · run_r3.py · run_r4.py · report.py

  docs/superpowers/
    specs/2026-09-12-machine-diagnostics-assistant-design.md
    plans/2026-09-12-m1-walking-skeleton.md
    measurements/2026-09-12-m1-boundary-risks.md
```

---

## Task 1: Repository skeleton, uv workspaces and pinned toolchain

The layering here is copied by every Python service in every later milestone. Get it exact.

**Files:**
- Create: `plant/pyproject.toml`, `plant/.python-version`, `plant/Dockerfile`, `plant/simulator/pyproject.toml`, `plant/inspection/pyproject.toml`
- Create: `diagnostics/pyproject.toml`, `diagnostics/.python-version`, `diagnostics/Dockerfile`, `diagnostics/analysis/pyproject.toml`, `diagnostics/agent/pyproject.toml`
- Create: `global.json`, `.nvmrc`, `.gitignore`, `Makefile`, `scripts/pin-images.sh`
- Create: `ruff.toml`, `mypy.ini`, `Directory.Build.props`, `.pre-commit-config.yaml`
- Test: `plant/simulator/tests/test_toolchain.py`

**Interfaces:**
- Consumes: nothing.
- Produces: two uv workspaces whose members build with `uv sync --locked --package <name>`; a `Dockerfile` per stack taking `--build-arg PACKAGE=<member>`; `make` targets `preflight`, `fmt`, `lint`, `test`, `check`, `verify`, `lock-check`; the §10.8 quality-gate configuration that every later task's commit asserts.

- [x] **Step 1: Write the failing toolchain test**

This is the spec's own open question from §10.7 ("confirm `asyncua` supports it during M1 rather than assuming"), turned into a test.

```python
# plant/simulator/tests/test_toolchain.py
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # plant/


def test_runs_on_pinned_python() -> None:
    pinned = (ROOT / ".python-version").read_text().strip()
    assert sys.version.startswith(pinned), f"expected {pinned}, running {sys.version}"


def test_asyncua_imports_and_reports_expected_version() -> None:
    import asyncua

    assert asyncua.__version__ == "2.0.1"


def test_workspace_members_pin_the_same_python() -> None:
    root = tomllib.loads((ROOT / "pyproject.toml").read_text())
    members = root["tool"]["uv"]["workspace"]["members"]
    assert set(members) == {"simulator", "inspection"}
    for member in members:
        cfg = tomllib.loads((ROOT / member / "pyproject.toml").read_text())
        assert cfg["project"]["requires-python"] == ">=3.13,<3.14"


def test_plant_workspace_does_not_reach_into_diagnostics() -> None:
    """§10.7: two workspaces, one per stack. A shared root lockfile would make
    §10.1's 'the two stacks share no code' false at build time."""
    assert not (ROOT.parent / "uv.lock").exists(), (
        "no lockfile may exist at the repository root"
    )
    assert (ROOT / "uv.lock").exists()
    assert (ROOT.parent / "diagnostics" / "uv.lock").exists()
```

- [x] **Step 2: Run it to watch it fail**

Run: `cd plant && uv run --frozen --package simulator pytest simulator/tests/test_toolchain.py -v`
Expected: FAIL — the workspace does not exist yet.

- [x] **Step 3: Create both workspace roots**

```toml
# plant/pyproject.toml
[tool.uv.workspace]
members = ["simulator", "inspection"]
```

```toml
# diagnostics/pyproject.toml
[tool.uv.workspace]
members = ["analysis", "agent"]
```

```
# plant/.python-version  and  diagnostics/.python-version
3.13
```

```toml
# plant/simulator/pyproject.toml
[project]
name = "simulator"
version = "0.1.0"
requires-python = ">=3.13,<3.14"
dependencies = [
    "asyncua==2.0.1",
    "pydantic-settings==2.15.0",
    "httpx==0.28.1",
]

[dependency-groups]
dev = ["pytest==9.1.1", "pytest-asyncio==1.4.0"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

```toml
# plant/inspection/pyproject.toml
[project]
name = "inspection"
version = "0.1.0"
requires-python = ">=3.13,<3.14"
dependencies = [
    "fastapi==0.141.1",
    "uvicorn==0.52.4",
    "pillow==12.3.0",
    "pydantic-settings==2.15.0",
]

[dependency-groups]
dev = ["pytest==9.1.1", "httpx==0.28.1"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

```toml
# diagnostics/analysis/pyproject.toml
[project]
name = "analysis"
version = "0.1.0"
requires-python = ">=3.13,<3.14"
dependencies = [
    "fastapi==0.141.1",
    "uvicorn==0.52.4",
    "psycopg[binary,pool]==3.3.5",
    "pydantic-settings==2.15.0",
]

[dependency-groups]
dev = ["pytest==9.1.1", "httpx==0.28.1"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

```toml
# diagnostics/agent/pyproject.toml
[project]
name = "agent"
version = "0.1.0"
requires-python = ">=3.13,<3.14"
dependencies = [
    "fastapi==0.141.1",
    "uvicorn==0.52.4",
    "anthropic==1.5.0",
    "httpx==0.28.1",
    "pydantic-settings==2.15.0",
]

[dependency-groups]
dev = ["pytest==9.1.1", "pytest-asyncio==1.4.0"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

Create the source trees so the members are buildable:

```bash
mkdir -p plant/simulator/src/simulator plant/inspection/src/inspection \
         diagnostics/analysis/src/analysis diagnostics/agent/src/agent
for p in plant/simulator/src/simulator plant/inspection/src/inspection \
         diagnostics/analysis/src/analysis diagnostics/agent/src/agent; do touch "$p/__init__.py"; done
mkdir -p plant/simulator/tests plant/inspection/tests \
         diagnostics/analysis/tests diagnostics/agent/tests
(cd plant && uv lock)
(cd diagnostics && uv lock)
```

- [x] **Step 4: Run the test to verify it passes**

Run: `cd plant && uv run --frozen --package simulator pytest simulator/tests/test_toolchain.py -v`
Expected: PASS, 4 tests. `uv python install` will fetch CPython 3.13 on first run — that is uv provisioning the interpreter, which is §10.7's point.

- [x] **Step 5: Write the shared Dockerfile**

One per stack, parameterised by workspace member. The interpreter comes from uv, not from the base image, so the base is bare Debian.

```dockerfile
# plant/Dockerfile   (diagnostics/Dockerfile is identical except the COPY lines in the
#                     dependency layer, which name that workspace's members)
# syntax=docker/dockerfile:1
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.11.13@sha256:841c8e6fe30a8b07b4478d12d0c608cba6de66102d29d65d1cc423af86051563
ARG BASE=debian:bookworm-slim@sha256:88200866dfff7ea7f5cbcb6ec7c8a701889efe6fe859fe64d6990e4b07ea4171

FROM ${UV_IMAGE} AS uv

FROM ${BASE} AS build
COPY --from=uv /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_INSTALL_DIR=/opt/python \
    UV_PYTHON_PREFERENCE=only-managed \
    UV_PROJECT_ENVIRONMENT=/opt/venv
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /src

# 1. the interpreter, pinned by .python-version
COPY .python-version ./
RUN uv python install

# 2. dependencies only — this layer survives every source edit
ARG PACKAGE
COPY pyproject.toml uv.lock ./
COPY simulator/pyproject.toml simulator/
COPY inspection/pyproject.toml inspection/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-workspace --package "${PACKAGE}"

# 3. source last
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --package "${PACKAGE}"

FROM ${BASE} AS runtime
COPY --from=build /opt/python /opt/python
COPY --from=build /opt/venv  /opt/venv
COPY --from=build /src       /src
ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1
WORKDIR /src
RUN useradd --system --uid 10001 app && chown -R app /src
USER app
```

`UV_PROJECT_ENVIRONMENT=/opt/venv` keeps the environment outside `/src`, so step 3's `COPY . .` cannot clobber it. `UV_PYTHON_PREFERENCE=only-managed` forbids falling back to a system interpreter, which is what makes "the base image does not dictate it" true rather than aspirational.

- [x] **Step 6: Pin the other two toolchains and write the digest script**

```json
// global.json
{ "sdk": { "version": "9.0.116", "rollForward": "latestFeature" } }
```

```
# .nvmrc
22
```

```bash
#!/usr/bin/env bash
# scripts/pin-images.sh — re-resolve every base image digest without pulling.
set -euo pipefail
IMAGES=(
  "debian:bookworm-slim"
  "postgres:17-bookworm"
  "node:22-bookworm-slim"
  "ghcr.io/astral-sh/uv:0.11.13"
  "mcr.microsoft.com/dotnet/sdk:9.0"
  "mcr.microsoft.com/dotnet/aspnet:9.0"
)
accept='application/vnd.oci.image.index.v1+json,application/vnd.docker.distribution.manifest.list.v2+json'
for img in "${IMAGES[@]}"; do
  repo="${img%:*}"; tag="${img##*:}"
  case "$repo" in
    mcr.microsoft.com/*) url="https://mcr.microsoft.com/v2/${repo#mcr.microsoft.com/}/manifests/${tag}"; hdr=() ;;
    ghcr.io/*) path="${repo#ghcr.io/}"
       tok=$(curl -sf "https://ghcr.io/token?scope=repository:${path}:pull" | sed -n 's/.*"token":"\([^"]*\)".*/\1/p')
       url="https://ghcr.io/v2/${path}/manifests/${tag}"; hdr=(-H "Authorization: Bearer ${tok}") ;;
    */*) path="$repo"; tok=$(curl -sf "https://auth.docker.io/token?service=registry.docker.io&scope=repository:${path}:pull" | sed -n 's/.*"token":"\([^"]*\)".*/\1/p')
       url="https://registry-1.docker.io/v2/${path}/manifests/${tag}"; hdr=(-H "Authorization: Bearer ${tok}") ;;
    *) path="library/$repo"; tok=$(curl -sf "https://auth.docker.io/token?service=registry.docker.io&scope=repository:${path}:pull" | sed -n 's/.*"token":"\([^"]*\)".*/\1/p')
       url="https://registry-1.docker.io/v2/${path}/manifests/${tag}"; hdr=(-H "Authorization: Bearer ${tok}") ;;
  esac
  digest=$(curl -sfI "${hdr[@]}" -H "Accept: ${accept}" "$url" | tr -d '\r' | sed -n 's/^[Dd]ocker-[Cc]ontent-[Dd]igest: //p')
  printf '%-46s %s\n' "$img" "${digest:-UNRESOLVED}"
done
```

```gitignore
# .gitignore
pki/*/key.pem
pki/*/*.pfx
**/.venv/
**/__pycache__/
**/bin/
**/obj/
node_modules/
*.env
!*.env.example
```

- [x] **Step 7: Write the Makefile skeleton**

```makefile
SHELL := /bin/bash
.PHONY: preflight lock-check fmt lint test check verify

preflight:
	@docker --version >/dev/null || { echo "docker missing"; exit 1; }
	@docker network inspect field-net >/dev/null 2>&1 || docker network create field-net
	@grep -qE '^[[:space:]]*127\.0\.0\.1[[:space:]]+line-simulator' /etc/hosts \
	  || { echo "MISSING host entry. Add this line to /etc/hosts:"; \
	       echo "    127.0.0.1 line-simulator"; exit 1; }
	@echo "preflight ok"

lock-check:
	cd plant && uv lock --check
	cd diagnostics && uv lock --check

test: lock-check
	cd plant && uv run --frozen --package simulator pytest simulator/tests -q
	cd plant && uv run --frozen --package inspection pytest inspection/tests -q
	cd diagnostics && uv run --frozen --package analysis pytest analysis/tests -q
	cd diagnostics && uv run --frozen --package agent pytest agent/tests -q
	cd diagnostics/gateway && dotnet test --locked-mode

fmt:
	cd plant && uv run ruff format . && uv run ruff check --fix .
	cd diagnostics && uv run ruff format . && uv run ruff check --fix .
	cd diagnostics/gateway && dotnet format

lint: lock-check
	cd plant && uv run ruff format --check . && uv run ruff check . && uv run mypy --strict .
	cd diagnostics && uv run ruff format --check . && uv run ruff check . && uv run mypy --strict .
	cd diagnostics/gateway && dotnet format --verify-no-changes && dotnet build --locked-mode

check: lint test

verify:
	cd plant && uv run --frozen --package simulator pytest simulator/tests -q -m authenticity
```

`uv lock --check` is the drift guard §10.7 asks for: it fails if the lockfile would change, rather than letting it move silently.

**`check` is the gate; `verify` is separate, deliberately.** The authenticity proofs in
Task 15 stop and restart containers — minutes per run, and flaky when Docker is under load.
Putting them in the default gate would make every commit slow enough that people stop
running it. They are marked `-m authenticity`, excluded from `make test`, and run by
`make verify` and by CI. Task 15 must register that pytest marker.

C# warnings-as-errors and nullable reference types go in a root `Directory.Build.props` so
every project inherits them, rather than being repeated per csproj:

```xml
<Project>
  <PropertyGroup>
    <Nullable>enable</Nullable>
    <TreatWarningsAsErrors>true</TreatWarningsAsErrors>
    <AnalysisMode>All</AnalysisMode>
    <EnforceCodeStyleInBuild>true</EnforceCodeStyleInBuild>
  </PropertyGroup>
</Project>
```

The pre-commit hook runs only the fast half — `ruff format --check`, `ruff check`,
`dotnet format --verify-no-changes` — so mistakes surface in seconds. `make check` remains
the full gate.

TypeScript tooling (`oxlint`, `tsc --noEmit`, `vitest`) lands with the first frontend code in
Task 14, wired into the same four targets.

- [x] **Step 8: Commit**

```bash
make check
git add plant diagnostics global.json .nvmrc .gitignore Makefile scripts/pin-images.sh \
        ruff.toml mypy.ini Directory.Build.props .pre-commit-config.yaml
git commit -m "build: uv workspaces per stack, pinned toolchain, image digests and quality gates

Confirms §10.7's open question: asyncua 2.0.1 declares requires-python >=3.10
and runs on the pinned CPython 3.13."
```

---

## Task 2: Certificates — one name, many SANs, mutual trust

Pure crypto, testable on its own. Task 6 makes the connection work; this task makes the material correct.

**Files:**
- Create: `scripts/gen-certs.py`, `plant/pki-init.Dockerfile`, `diagnostics/pki-init.Dockerfile`
- Test: `plant/simulator/tests/test_certificates.py`

**Interfaces:**
- Consumes: Task 1's toolchain.
- Produces: `pki/<party>/key.pem`, `pki/<party>/cert.der`, `pki/edge-gateway/cert.pfx`, and `pki/trusted/<party>.der` for both parties. `gen_party(party, app_uri, dns_names, ip_addresses, role, out_root) -> None`, idempotent.

- [x] **Step 1: Write the failing certificate test**

```python
# plant/simulator/tests/test_certificates.py
import ipaddress
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.x509.oid import ExtendedKeyUsageOID

from simulator.pki import PARTIES, gen_party

SERVER_URI = "urn:machine-agent:plant:line-simulator"
CLIENT_URI = "urn:machine-agent:diagnostics:edge-gateway"


@pytest.fixture
def pki(tmp_path: Path) -> Path:
    for party in PARTIES.values():
        gen_party(party, tmp_path)
    return tmp_path


def _cert(root: Path, name: str) -> x509.Certificate:
    return x509.load_der_x509_certificate((root / name / "cert.der").read_bytes())


def test_server_uri_san_equals_application_uri(pki: Path) -> None:
    """asyncua raises BadCertificateUriInvalid unless ApplicationUri is a URI SAN."""
    san = (
        _cert(pki, "line-simulator")
        .extensions.get_extension_for_class(x509.SubjectAlternativeName)
        .value
    )
    assert san.get_values_for_type(x509.UniformResourceIdentifier) == [SERVER_URI]


def test_server_dns_sans_cover_every_name_a_client_may_dial(pki: Path) -> None:
    """The DNS SAN must cover the host component of the endpoint URL, or .NET's
    checkDomain fails as BadCertificateHostNameInvalid. Three clients, three names."""
    san = (
        _cert(pki, "line-simulator")
        .extensions.get_extension_for_class(x509.SubjectAlternativeName)
        .value
    )
    assert set(san.get_values_for_type(x509.DNSName)) == {"line-simulator", "localhost"}
    assert ipaddress.ip_address("127.0.0.1") in san.get_values_for_type(x509.IPAddress)


def test_roles_are_distinct(pki: Path) -> None:
    """asyncua checks EXT_KEY_USAGE against the peer's expected role."""
    server = (
        _cert(pki, "line-simulator")
        .extensions.get_extension_for_class(x509.ExtendedKeyUsage)
        .value
    )
    client = (
        _cert(pki, "edge-gateway")
        .extensions.get_extension_for_class(x509.ExtendedKeyUsage)
        .value
    )
    assert ExtendedKeyUsageOID.SERVER_AUTH in server
    assert ExtendedKeyUsageOID.CLIENT_AUTH in client


def test_key_usage_satisfies_asyncua_validator(pki: Path) -> None:
    """CertificateValidatorOptions.KEY_USAGE requires all four of these."""
    ku = (
        _cert(pki, "line-simulator")
        .extensions.get_extension_for_class(x509.KeyUsage)
        .value
    )
    assert ku.digital_signature and ku.content_commitment
    assert ku.key_encipherment and ku.data_encipherment


def test_both_parties_land_in_the_shared_trust_dir(pki: Path) -> None:
    assert (pki / "trusted" / "line-simulator.der").exists()
    assert (pki / "trusted" / "edge-gateway.der").exists()


def test_gateway_gets_a_pfx_for_the_dotnet_store(pki: Path) -> None:
    assert (pki / "edge-gateway" / "cert.pfx").stat().st_size > 0


def test_regeneration_is_idempotent(pki: Path) -> None:
    """docker compose up runs pki-init every time; it must not churn the identity,
    or §14's 'survives a restart without re-trusting by hand' fails."""
    before = (pki / "line-simulator" / "cert.der").read_bytes()
    gen_party(PARTIES["line-simulator"], pki)
    assert (pki / "line-simulator" / "cert.der").read_bytes() == before
```

- [x] **Step 2: Run it to verify it fails**

Run: `cd plant && uv run --frozen --package simulator pytest simulator/tests/test_certificates.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'simulator.pki'`.

- [x] **Step 3: Write the generator**

```python
# plant/simulator/src/simulator/pki.py
"""Application instance certificates for the OPC UA boundary (§4.6).

asyncua ships setup_self_signed_certificate(), but it emits exactly one DNS SAN.
The boundary needs three names on one certificate (see the plan's "one-name
boundary"), so this calls the lower-level generator with a SAN list.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from pathlib import Path

from asyncua.crypto.cert_gen import (
    dump_private_key_as_pem,
    generate_private_key,
    generate_self_signed_app_certificate,
)
from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, pkcs12
from cryptography.x509.oid import ExtendedKeyUsageOID

VALID_DAYS = 825  # under the 825-day maximum most validators accept


@dataclass(frozen=True)
class Party:
    name: str
    app_uri: str
    dns_names: tuple[str, ...]
    ip_addresses: tuple[str, ...] = ()
    server_auth: bool = False
    client_auth: bool = False
    want_pfx: bool = False
    subject: dict[str, str] = field(default_factory=dict)


PARTIES: dict[str, Party] = {
    "line-simulator": Party(
        name="line-simulator",
        app_uri="urn:machine-agent:plant:line-simulator",
        # Every host component any client may dial, on one certificate:
        #   gateway on field-net -> line-simulator
        #   host via published port -> localhost / 127.0.0.1
        dns_names=("line-simulator", "localhost"),
        ip_addresses=("127.0.0.1",),
        server_auth=True,
        subject={
            "commonName": "line-simulator",
            "organizationName": "machine-agent",
            "countryName": "DE",
        },
    ),
    "edge-gateway": Party(
        name="edge-gateway",
        app_uri="urn:machine-agent:diagnostics:edge-gateway",
        dns_names=("edge-gateway",),
        client_auth=True,
        want_pfx=True,
        subject={
            "commonName": "edge-gateway",
            "organizationName": "machine-agent",
            "countryName": "DE",
        },
    ),
}


def gen_party(party: Party, out_root: Path) -> None:
    """Generate this party's keypair if absent and publish its public cert to trusted/.

    Idempotent: an existing key and certificate are left untouched, so re-running
    pki-init on every `docker compose up` does not invalidate established trust.
    """
    out = out_root / party.name
    out.mkdir(parents=True, exist_ok=True)
    trusted = out_root / "trusted"
    trusted.mkdir(parents=True, exist_ok=True)

    key_file, cert_file = out / "key.pem", out / "cert.der"

    if key_file.exists() and cert_file.exists():
        cert = x509.load_der_x509_certificate(cert_file.read_bytes())
    else:
        key = generate_private_key()
        sans: list[x509.GeneralName] = [x509.UniformResourceIdentifier(party.app_uri)]
        sans += [x509.DNSName(n) for n in party.dns_names]
        sans += [x509.IPAddress(ipaddress.ip_address(a)) for a in party.ip_addresses]

        usages: list[x509.ObjectIdentifier] = []
        if party.server_auth:
            usages.append(ExtendedKeyUsageOID.SERVER_AUTH)
        if party.client_auth:
            usages.append(ExtendedKeyUsageOID.CLIENT_AUTH)

        cert = generate_self_signed_app_certificate(
            key, party.app_uri, party.subject, sans, extended=usages, days=VALID_DAYS
        )
        key_file.write_bytes(dump_private_key_as_pem(key))
        key_file.chmod(0o600)
        cert_file.write_bytes(cert.public_bytes(encoding=Encoding.DER))

        if party.want_pfx:
            # UA-.NETStandard's Directory store reads own/private/*.pfx. Minting the
            # certificate here rather than letting CheckApplicationInstanceCertificatesAsync
            # do it keeps the DNS SAN stable: .NET would derive it from the container
            # hostname, which changes on every recreate and breaks pre-seeded trust.
            (out / "cert.pfx").write_bytes(
                pkcs12.serialize_key_and_certificates(
                    name=party.name.encode(),
                    key=key,
                    cert=cert,
                    cas=None,
                    encryption_algorithm=NoEncryption(),
                )
            )

    (trusted / f"{party.name}.der").write_bytes(
        cert.public_bytes(encoding=Encoding.DER)
    )


def main() -> None:
    import sys

    root = Path(sys.argv[1] if len(sys.argv) > 1 else "/pki")
    for party in PARTIES.values():
        gen_party(party, root)
        print(f"pki: {party.name} ready ({', '.join(party.dns_names)})")


if __name__ == "__main__":
    main()
```

Both stacks run the same module. Each generates both parties' public material if missing, which is what lets either stack be brought up first.

- [x] **Step 4: Run the test to verify it passes**

Run: `cd plant && uv run --frozen --package simulator pytest simulator/tests/test_certificates.py -v`
Expected: PASS, 7 tests.

- [x] **Step 5: Generate once and inspect by eye**

```bash
cd plant && uv run --frozen --package simulator python -m simulator.pki ../pki
openssl x509 -in ../pki/line-simulator/cert.der -inform DER -noout -text | grep -A4 "Subject Alternative Name"
```
Expected: `URI:urn:machine-agent:plant:line-simulator, DNS:line-simulator, DNS:localhost, IP Address:127.0.0.1`

- [x] **Step 6: Commit**

```bash
git add scripts plant/simulator/src/simulator/pki.py plant/simulator/tests/test_certificates.py
git commit -m "feat(pki): multi-SAN application instance certificates with pre-seeded mutual trust"
```

---

## Task 3: The simulated clock, and the history depth the spec actually needs

**Files:**
- Create: `plant/simulator/src/simulator/clock.py`, `plant/simulator/src/simulator/config.py`
- Test: `plant/simulator/tests/test_clock.py`

**Interfaces:**
- Consumes: Task 1.
- Produces: `Phase` (`BOOTING`/`CATCHUP`/`LIVE`), `SimulatedClock(cfg, wall_fn)` with `.now() -> datetime` (UTC-aware), `.phase -> Phase`, `.catchup_duration -> timedelta`; `required_history_depth(at_local) -> timedelta`; `Settings` carrying every §10.3 number.

- [x] **Step 1: Write the failing clock test**

The third test is the one that matters — it checks the spec's own claim about why 18 h was chosen.

```python
# plant/simulator/tests/test_clock.py
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from simulator.clock import Phase, SimulatedClock, required_history_depth
from simulator.config import ClockConfig

BERLIN = ZoneInfo("Europe/Berlin")


def _clock(depth_h: float, speed: float, wall: list[datetime]) -> SimulatedClock:
    return SimulatedClock(
        ClockConfig(history_depth=timedelta(hours=depth_h), catchup_speed=speed),
        wall_fn=lambda: wall[0],
    )


def test_catchup_duration_matches_the_spec_arithmetic() -> None:
    """§3.2 says 18 h at 600x is about 108 s of wall clock. Simulated time gains
    (speed - 1) seconds per wall second, so the depth closes in depth/(speed-1)."""
    wall = [datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)]
    clock = _clock(18, 600, wall)
    assert abs(clock.catchup_duration.total_seconds() - 108.18) < 0.1


def test_phases_run_in_order_and_live_speed_is_exactly_one() -> None:
    boot = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)
    wall = [boot]
    clock = _clock(18, 600, wall)

    assert clock.phase is Phase.CATCHUP
    assert clock.now() == boot - timedelta(hours=18)

    wall[0] = boot + timedelta(seconds=54)  # halfway
    assert clock.phase is Phase.CATCHUP
    assert clock.now() < wall[0]

    wall[0] = boot + timedelta(seconds=200)  # past catch-up
    assert clock.phase is Phase.LIVE
    assert clock.now() == wall[0]  # exactly 1.0, not approximately

    wall[0] = boot + timedelta(seconds=260)
    assert clock.now() == wall[0]


def test_eighteen_hours_does_not_always_contain_a_completed_night_shift() -> None:
    """§3.2: 'The 18 h default exists so the previous night shift lies fully inside
    history at startup.' That holds for a morning boot and fails for an evening one.
    Night is 22-06 Europe/Berlin; the most recent *completed* night shift at 21:59
    started 23 h 59 min earlier."""
    morning = datetime(2026, 9, 12, 9, 0, tzinfo=BERLIN)
    assert required_history_depth(morning) <= timedelta(hours=18)

    evening = datetime(2026, 9, 12, 21, 59, tzinfo=BERLIN)
    assert required_history_depth(evening) > timedelta(hours=18)


def test_required_depth_survives_the_autumn_dst_night() -> None:
    """The night of the autumn transition is nine hours long, which is the worst
    case the default must cover."""
    evening_after_transition = datetime(2026, 10, 25, 21, 59, tzinfo=BERLIN)
    worst = required_history_depth(evening_after_transition)
    assert timedelta(hours=24) < worst <= timedelta(hours=26)


def test_the_default_depth_covers_every_boot_time_in_the_year() -> None:
    """This is the test that produces the number Task 17 writes into §3.2."""
    start = datetime(2026, 1, 1, tzinfo=BERLIN)
    worst = max(
        required_history_depth(start + timedelta(hours=h)) for h in range(0, 366 * 24)
    )
    assert worst <= ClockConfig.DEFAULT_HISTORY_DEPTH
```

- [x] **Step 2: Run it to verify it fails**

Run: `cd plant && uv run --frozen --package simulator pytest simulator/tests/test_clock.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'simulator.clock'`.

- [x] **Step 3: Write the config**

```python
# plant/simulator/src/simulator/config.py
"""Every number in the plant is configuration, never a constant buried in code (§10.3)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from pydantic_settings import BaseSettings, SettingsConfigDict


@dataclass(frozen=True)
class ClockConfig:
    history_depth: timedelta
    catchup_speed: float
    live_speed: float = 1.0

    # Covers the worst boot time in the year, including the nine-hour autumn
    # DST night. Confirmed by test_the_default_depth_covers_every_boot_time_in_the_year.
    DEFAULT_HISTORY_DEPTH = timedelta(hours=26)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PLANT_", env_file=".env")

    # clock
    history_depth_hours: float = 26.0
    catchup_speed: float = 600.0

    # line
    takt_seconds: float = 6.0
    seed: int = 20260912

    # inspection — M1's reject rate is a measurement knob for R4, not §3.5's
    # 1.5 % noise floor, which arrives with the noise model in M2.
    reject_rate: float = 0.05
    image_width: int = 640
    image_height: int = 480

    # boundary
    endpoint_url: str = "opc.tcp://line-simulator:4840/plant"
    application_uri: str = "urn:machine-agent:plant:line-simulator"
    pki_root: str = "/pki"
    history_page_size: int = 1000
    inspection_url: str = "http://inspection-service:8100"
```

- [x] **Step 4: Write the clock**

```python
# plant/simulator/src/simulator/clock.py
"""past -> catch-up -> live (§3.2). All timestamps UTC; shift logic Europe/Berlin."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, time, timedelta, timezone
from enum import Enum
from zoneinfo import ZoneInfo

from simulator.config import ClockConfig

BERLIN = ZoneInfo("Europe/Berlin")
NIGHT_START = time(22, 0)
NIGHT_END = time(6, 0)


class Phase(str, Enum):
    BOOTING = "booting"
    CATCHUP = "catchup"
    LIVE = "live"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SimulatedClock:
    """Simulated time starts history_depth in the past and closes the gap at
    catchup_speed, then tracks the wall clock at exactly 1.0.

    Simulated time gains (speed - 1) seconds per wall second, so the gap closes
    after history_depth / (speed - 1).
    """

    def __init__(
        self, cfg: ClockConfig, wall_fn: Callable[[], datetime] = _utc_now
    ) -> None:
        if cfg.catchup_speed <= 1.0:
            raise ValueError("catchup_speed must exceed 1.0 or history never closes")
        self._cfg = cfg
        self._wall = wall_fn
        self._boot = wall_fn()

    @property
    def boot_wall(self) -> datetime:
        return self._boot

    @property
    def history_start(self) -> datetime:
        return self._boot - self._cfg.history_depth

    @property
    def catchup_duration(self) -> timedelta:
        return self._cfg.history_depth / (self._cfg.catchup_speed - 1.0)

    @property
    def phase(self) -> Phase:
        return (
            Phase.CATCHUP
            if self._wall() < self._boot + self.catchup_duration
            else Phase.LIVE
        )

    def now(self) -> datetime:
        wall = self._wall()
        if wall >= self._boot + self.catchup_duration:
            return wall
        elapsed = (wall - self._boot).total_seconds()
        return self.history_start + timedelta(seconds=elapsed * self._cfg.catchup_speed)


def last_completed_night_shift(at_local: datetime) -> tuple[datetime, datetime]:
    """The most recent night shift (22:00-06:00 local) that has already ended."""
    end_day = at_local.date()
    end = datetime.combine(end_day, NIGHT_END, tzinfo=BERLIN)
    if end > at_local:
        end = datetime.combine(end_day - timedelta(days=1), NIGHT_END, tzinfo=BERLIN)
    start = datetime.combine(end.date() - timedelta(days=1), NIGHT_START, tzinfo=BERLIN)
    return start, end


def required_history_depth(at_local: datetime) -> timedelta:
    """How deep history must reach for the last completed night shift to lie
    fully inside it, if the plant boots at at_local.

    §3.2 asserts 18 h suffices. It does for a morning boot. For an evening boot
    the last completed night shift ended that morning and started the previous
    evening, which is nearly 24 h back -- and nearly 25 h across the autumn DST
    night, which runs nine hours.
    """
    start, _ = last_completed_night_shift(at_local)
    return at_local.astimezone(timezone.utc) - start.astimezone(timezone.utc)
```

- [x] **Step 5: Run the test to verify it passes**

Run: `cd plant && uv run --frozen --package simulator pytest simulator/tests/test_clock.py -v`
Expected: PASS, 5 tests. Record the value `test_the_default_depth_covers_every_boot_time_in_the_year` computes — it is one of the numbers Task 17 writes into §3.2.

- [x] **Step 6: Commit**

```bash
git add plant/simulator/src/simulator/clock.py plant/simulator/src/simulator/config.py plant/simulator/tests/test_clock.py
git commit -m "feat(simulator): simulated clock, and the history depth a night-shift question needs

§3.2's 18 h default holds only for a morning boot. An evening boot needs nearly
24 h, and nearly 25 h across the autumn DST night. Default raised to 26 h."
```

---

## Task 4: The address space, the historian and catch-up generation

**Files:**
- Create: `plant/simulator/src/simulator/address_space.py`, `events.py`, `historian.py`, `station_s3.py`
- Test: `plant/simulator/tests/test_address_space.py`, `plant/simulator/tests/test_generation.py`

**Interfaces:**
- Consumes: `Settings`, `SimulatedClock`, `Phase` (Task 3).
- Produces:
  - `AddressSpace` dataclass with `.line`, `.stations`, `.s3`, `.takt`, `.part_count`, `.event_type`, `.event_gen`
  - `async build_address_space(server, idx) -> AddressSpace`
  - `async attach_historian(server, space, db_path, page_size) -> HistorySQLite`
  - `PartOutcome(disposition: str, defect_class: str | None, confidence: float, image: bytes | None)`
  - `ProduceFn = Callable[[str, datetime], Awaitable[PartOutcome]]`
  - `Ledger` with `.takt`, `.part_count`, `.events`, `.images` counters
  - `async generate(space, clock, settings, produce, ledger) -> None`

- [x] **Step 1: Write the failing address-space test**

```python
# plant/simulator/tests/test_address_space.py
import pytest
from asyncua import Server, ua

from simulator.address_space import build_address_space


@pytest.mark.asyncio
async def test_s3_exposes_exactly_the_two_signals_and_no_packml() -> None:
    """§4.1 lists State and StateReason for every station, but §13 puts PackML in M2.
    A node that never changes is a fake, and §13's standard for M1 is that nothing
    in it is faked -- so they are absent, not static."""
    server = Server()
    await server.init()
    idx = await server.register_namespace("http://machine-agent/plant")
    space = await build_address_space(server, idx)

    names = {(await c.read_browse_name()).Name for c in await space.s3.get_children()}
    assert "TaktTime" in names
    assert "PartCount" in names
    assert "State" not in names
    assert "StateReason" not in names


@pytest.mark.asyncio
async def test_topology_is_browsable_under_line_stations() -> None:
    """§4.1: the gateway discovers the line's topology by browsing, never by config."""
    server = Server()
    await server.init()
    idx = await server.register_namespace("http://machine-agent/plant")
    await build_address_space(server, idx)

    line = await server.nodes.objects.get_child([f"{idx}:Line"])
    stations = await line.get_child([f"{idx}:Stations"])
    found = [(await s.read_browse_name()).Name for s in await stations.get_children()]
    assert found == ["S3_Inspection"]


@pytest.mark.asyncio
async def test_event_type_carries_an_image_field() -> None:
    server = Server()
    await server.init()
    idx = await server.register_namespace("http://machine-agent/plant")
    space = await build_address_space(server, idx)

    props = {
        (await p.read_browse_name()).Name
        for p in await space.event_type.get_properties()
    }
    assert {
        "AssemblySerial",
        "Disposition",
        "DefectClass",
        "Confidence",
        "Image",
    } <= props
```

- [x] **Step 2: Run it to verify it fails**

Run: `cd plant && uv run --frozen --package simulator pytest simulator/tests/test_address_space.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'simulator.address_space'`.

- [x] **Step 3: Build the address space**

```python
# plant/simulator/src/simulator/address_space.py
"""Objects/Line/Stations/S3_Inspection, per §4.1, narrowed to M1.

M1 carries TaktTime and PartCount -- §4.1's two variables for S3 -- and the
inspection event. They are deliberately different in kind: TaktTime is a noisy
float where a deadband is meaningful, PartCount a monotonic counter where a
deadband would silently lose parts. The pair tests the gateway's per-signal
deadband configuration in both directions.
"""

from __future__ import annotations

from dataclasses import dataclass

from asyncua import Node, Server, ua

EVENT_FIELDS: tuple[tuple[str, ua.VariantType], ...] = (
    ("AssemblySerial", ua.VariantType.String),
    ("Disposition", ua.VariantType.String),
    ("DefectClass", ua.VariantType.String),
    ("Confidence", ua.VariantType.Double),
    ("ModelVersion", ua.VariantType.String),
    ("Image", ua.VariantType.ByteString),
)


@dataclass
class AddressSpace:
    idx: int
    line: Node
    stations: Node
    s3: Node
    takt: Node
    part_count: Node
    event_type: Node
    event_gen: object  # asyncua EventGenerator


async def build_address_space(server: Server, idx: int) -> AddressSpace:
    objects = server.nodes.objects
    line = await objects.add_object(idx, "Line")
    stations = await line.add_object(idx, "Stations")
    s3 = await stations.add_object(idx, "S3_Inspection")

    takt = await s3.add_variable(idx, "TaktTime", 0.0, ua.VariantType.Double)
    part_count = await s3.add_variable(idx, "PartCount", 0, ua.VariantType.UInt32)

    event_type = await server.create_custom_event_type(
        idx,
        "InspectionResultEventType",
        ua.ObjectIds.BaseEventType,
        [(name, vtype) for name, vtype in EVENT_FIELDS],
    )

    # ORDER MATTERS. get_event_generator() adds the GeneratesEvent reference from
    # the emitting node to the event type and sets its EventNotifier bit.
    # historize_node_event() later reads exactly those GeneratesEvent references to
    # decide which event types to historise -- so the generator must exist first,
    # or event history is silently created with no columns.
    event_gen = await server.get_event_generator(event_type, s3)

    return AddressSpace(
        idx, line, stations, s3, takt, part_count, event_type, event_gen
    )
```

- [x] **Step 4: Run the address-space test to verify it passes**

Run: `cd plant && uv run --frozen --package simulator pytest simulator/tests/test_address_space.py -v`
Expected: PASS, 3 tests.

- [x] **Step 5: Write the failing generation test**

```python
# plant/simulator/tests/test_generation.py
from datetime import datetime, timedelta, timezone

import pytest
from asyncua import Server

from simulator.address_space import build_address_space
from simulator.config import ClockConfig, Settings
from simulator.clock import SimulatedClock
from simulator.historian import Ledger, attach_historian
from simulator.station_s3 import PartOutcome, generate_history


async def _stub_produce(part_id: str, ts: datetime) -> PartOutcome:
    reject = part_id.endswith("7")
    return PartOutcome(
        disposition="reject" if reject else "good",
        defect_class="gap" if reject else None,
        confidence=0.91,
        image=b"\x89PNG" + b"\x00" * 4096 if reject else None,
    )


@pytest.mark.asyncio
async def test_catchup_writes_every_expected_row(tmp_path) -> None:
    """R1's core assertion: the ledger and the historian agree exactly.
    Silent loss here is invisible to every layer above."""
    settings = Settings(history_depth_hours=1.0, takt_seconds=6.0, catchup_speed=600.0)
    server = Server()
    await server.init()
    idx = await server.register_namespace("http://machine-agent/plant")
    space = await build_address_space(server, idx)
    await attach_historian(server, space, tmp_path / "h.db", settings.history_page_size)

    clock = SimulatedClock(ClockConfig(timedelta(hours=1), 600.0))
    ledger = Ledger()
    async with server:
        await generate_history(space, clock, settings, _stub_produce, ledger)

    expected = int(3600 / 6.0)  # 600 parts in one hour at 6 s takt
    assert ledger.takt == expected
    assert ledger.part_count == expected
    assert ledger.events == expected
    assert ledger.images == sum(
        1 for i in range(expected) if f"A-{i:08d}".endswith("7")
    )


@pytest.mark.asyncio
async def test_source_timestamps_are_simulated_not_wall_clock(tmp_path) -> None:
    """§4.2: SourceTimestamp is simulated time. During catch-up the two diverge
    sharply, which is the whole point."""
    settings = Settings(history_depth_hours=1.0, takt_seconds=6.0)
    server = Server()
    await server.init()
    idx = await server.register_namespace("http://machine-agent/plant")
    space = await build_address_space(server, idx)
    await attach_historian(server, space, tmp_path / "h.db", settings.history_page_size)

    clock = SimulatedClock(ClockConfig(timedelta(hours=1), 600.0))
    async with server:
        await generate_history(space, clock, settings, _stub_produce, Ledger())
        rows = await space.takt.read_raw_history(
            clock.history_start - timedelta(minutes=1),
            datetime.now(timezone.utc) + timedelta(days=1),
            0,
        )

    assert rows, "history is empty"
    oldest = min(r.SourceTimestamp for r in rows)
    assert oldest < datetime.now(timezone.utc).replace(tzinfo=timezone.utc) - timedelta(
        minutes=50
    )
```

- [x] **Step 6: Run it to verify it fails**

Run: `cd plant && uv run --frozen --package simulator pytest simulator/tests/test_generation.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'simulator.historian'`.

- [x] **Step 7: Write the historian wiring**

```python
# plant/simulator/src/simulator/historian.py
"""Historian wiring, with the two traps that bite here spelled out."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from asyncua import Server
from asyncua.server.history_sql import HistorySQLite

from simulator.address_space import AddressSpace


@dataclass
class Ledger:
    """What the generator believes it wrote. R1 reconciles this against the
    historian, against what HistoryRead returns, and against Postgres."""

    takt: int = 0
    part_count: int = 0
    events: int = 0
    images: int = 0
    image_bytes: int = 0


async def attach_historian(
    server: Server, space: AddressSpace, db_path: Path, page_size: int
) -> HistorySQLite:
    storage = HistorySQLite(str(db_path), max_history_data_response_size=page_size)

    # HistoryManager.init() already ran inside Server.init() against the default
    # in-memory HistoryDict, so replacing the storage means initialising it here.
    await storage.init()
    server.iserver.history_manager.set_storage(storage)

    # period=None, count=0 is deliberate, not laziness. save_node_value() deletes
    # rows where SourceTimestamp < now() - period using the REAL wall clock, while
    # our SourceTimestamps are simulated and up to history_depth in the past. Any
    # period shorter than the history depth erases history as it is written.
    await server.historize_node_data_change(
        [space.takt, space.part_count], period=None, count=0
    )
    await server.historize_node_event(space.s3, period=None, count=0)
    return storage
```

- [x] **Step 8: Write the station loop**

```python
# plant/simulator/src/simulator/station_s3.py
"""S3 Inspection: the takt loop, catch-up generation and live production."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from asyncua import ua

from simulator.address_space import AddressSpace
from simulator.clock import Phase, SimulatedClock
from simulator.config import Settings
from simulator.historian import Ledger

MODEL_VERSION = "simulated-1"


@dataclass(frozen=True)
class PartOutcome:
    disposition: str  # "good" | "reject"
    defect_class: str | None
    confidence: float
    image: bytes | None  # §3.4: only rejects carry their image


ProduceFn = Callable[[str, datetime], Awaitable[PartOutcome]]


def serial_for(index: int) -> str:
    return f"A-{index:08d}"


async def _emit_part(
    space: AddressSpace,
    index: int,
    sim_ts: datetime,
    takt: float,
    outcome: PartOutcome,
    ledger: Ledger,
) -> None:
    serial = serial_for(index)

    # Variables. write_value with an explicit SourceTimestamp reaches the historian
    # through the internal datachange subscription, which fires per change rather
    # than per publishing interval -- so catch-up rates do not coalesce values.
    await space.takt.write_value(
        ua.DataValue(ua.Variant(takt, ua.VariantType.Double), SourceTimestamp=sim_ts)
    )
    ledger.takt += 1
    await space.part_count.write_value(
        ua.DataValue(
            ua.Variant(index + 1, ua.VariantType.UInt32), SourceTimestamp=sim_ts
        )
    )
    ledger.part_count += 1

    # The event. Its Time field is the event analogue of SourceTimestamp.
    ev = space.event_gen.event
    ev.AssemblySerial = serial
    ev.Disposition = outcome.disposition
    ev.DefectClass = outcome.defect_class or ""
    ev.Confidence = outcome.confidence
    ev.ModelVersion = MODEL_VERSION
    ev.Image = outcome.image or b""
    await space.event_gen.trigger(time_attr=sim_ts, message=f"inspection {serial}")
    ledger.events += 1
    if outcome.image:
        ledger.images += 1
        ledger.image_bytes += len(outcome.image)


async def generate_history(
    space: AddressSpace,
    clock: SimulatedClock,
    settings: Settings,
    produce: ProduceFn,
    ledger: Ledger,
) -> None:
    """Catch-up: build the configured depth of history in process (§3.2).

    Timestamps are computed directly from the takt rather than sampled from the
    clock, so the history is exactly regular and the expected row count is known
    in advance -- which is what makes R1's reconciliation meaningful.

    Do not page the storage in-process to verify: HistorySQLite returns a
    timezone-naive continuation point and comparing it against an aware datetime
    raises TypeError. Read back through the server instead.
    """
    takt = settings.takt_seconds
    total = int(clock._cfg.history_depth.total_seconds() // takt)
    for i in range(total):
        sim_ts = clock.history_start + timedelta(seconds=i * takt)
        await _emit_part(
            space, i, sim_ts, takt, await produce(serial_for(i), sim_ts), ledger
        )


async def run_live(
    space: AddressSpace,
    clock: SimulatedClock,
    settings: Settings,
    produce: ProduceFn,
    ledger: Ledger,
    start_index: int,
) -> None:
    """Live: one part per takt at exactly 1.0 (§3.2)."""
    index = start_index
    while True:
        if clock.phase is Phase.LIVE:
            sim_ts = clock.now()
            await _emit_part(
                space,
                index,
                sim_ts,
                settings.takt_seconds,
                await produce(serial_for(index), sim_ts),
                ledger,
            )
            index += 1
        await asyncio.sleep(settings.takt_seconds)
```

- [x] **Step 9: Run the generation tests to verify they pass**

Run: `cd plant && uv run --frozen --package simulator pytest simulator/tests/test_generation.py -v`
Expected: PASS, 2 tests.

- [x] **Step 10: Commit**

```bash
git add plant/simulator/src/simulator plant/simulator/tests
git commit -m "feat(simulator): S3 address space, historian and catch-up generation with a row ledger"
```

---

## Task 5: The inspection service, and the event that carries a real image

§13's M1 line says "one event with an image", and §3.4 says the camera belongs to the station while the vision system is a separate box beside it. Both cannot hold with the simulator classifying its own images, so the inspection service ships in M1. It is small, and it fixes the interface that a real model later drops into unchanged — which is the thing §3.4 says must not move.

**Files:**
- Create: `plant/inspection/src/inspection/render.py`, `classifier.py`, `truth.py`, `app.py`
- Create: `plant/simulator/src/simulator/inspection_client.py`
- Test: `plant/inspection/tests/test_inspection.py`, `plant/simulator/tests/test_inspection_client.py`

**Interfaces:**
- Consumes: Task 4's `PartOutcome`, `ProduceFn`.
- Produces:
  - `render_part(part_id, defects, width, height, seed) -> bytes` (PNG)
  - `class Classifier(Protocol)` with `classify(image: bytes, ctx: PartContext) -> InspectionResult`
  - `SimulatedClassifier(truth: TruthChannel, seed: int)`
  - HTTP: `POST /truth/{part_id}` (side channel) and `POST /inspect` (the real interface)
  - `InspectionClient(base_url).produce(part_id, sim_ts) -> PartOutcome`, satisfying `ProduceFn`

- [ ] **Step 1: Write the failing inspection test**

```python
# plant/inspection/tests/test_inspection.py
import json

import pytest
from fastapi.testclient import TestClient

from inspection.app import app, DEFECT_CLASSES
from inspection.render import render_part

client = TestClient(app)


def test_defect_classes_match_the_spec() -> None:
    assert DEFECT_CLASSES == [
        "gap",
        "crack",
        "misalignment",
        "missing_part",
        "scratch",
        "contamination",
    ]


def test_render_is_deterministic_for_a_seed() -> None:
    a = render_part("A-00000042", ["gap"], 640, 480, seed=7)
    b = render_part("A-00000042", ["gap"], 640, 480, seed=7)
    assert a == b and a.startswith(b"\x89PNG")


def test_inspect_request_carries_no_ground_truth() -> None:
    """§3.4: the classifier resolves truth through a side channel keyed by part id,
    NEVER through the request. A real model would ignore the side channel and its
    signature would not change -- so the request body must not mention truth."""
    client.post("/truth/A-00000001", json={"defects": ["crack"]})
    image = render_part("A-00000001", ["crack"], 64, 64, seed=1)

    sent = {"part_id": "A-00000001", "image_b64": "", "carrier_id": 3}
    assert "defect" not in json.dumps(sent)
    assert "truth" not in json.dumps(sent)

    import base64

    sent["image_b64"] = base64.b64encode(image).decode()
    result = client.post("/inspect", json=sent).json()
    assert result["disposition"] == "reject"
    assert result["defect_class"] == "crack"


def test_a_part_with_no_truth_entry_passes() -> None:
    import base64

    image = render_part("A-00000002", [], 64, 64, seed=1)
    result = client.post(
        "/inspect",
        json={
            "part_id": "A-00000002",
            "image_b64": base64.b64encode(image).decode(),
            "carrier_id": 1,
        },
    ).json()
    assert result["disposition"] == "good"


def test_confidences_are_a_distribution_over_all_classes() -> None:
    """§3.4: plausible per-class confidence distributions, so the confidence field
    carries information."""
    import base64

    client.post("/truth/A-00000003", json={"defects": ["scratch"]})
    image = render_part("A-00000003", ["scratch"], 64, 64, seed=1)
    result = client.post(
        "/inspect",
        json={
            "part_id": "A-00000003",
            "image_b64": base64.b64encode(image).decode(),
            "carrier_id": 1,
        },
    ).json()

    conf = result["confidences"]
    assert set(conf) == set(DEFECT_CLASSES)
    assert abs(sum(conf.values()) - 1.0) < 1e-6
    assert max(conf, key=conf.get) == "scratch"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd plant && uv run --frozen --package inspection pytest inspection/tests -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'inspection.app'`.

- [ ] **Step 3: Write the renderer**

```python
# plant/inspection/src/inspection/render.py
"""The simulator renders the part image -- it knows the geometry and the defects (§3.4)."""

from __future__ import annotations

import io
import random

from PIL import Image, ImageDraw

BODY = (172, 176, 182)
BACKGROUND = (24, 26, 30)
DEFECT_INK = {
    "gap": (250, 90, 60),
    "crack": (255, 210, 60),
    "misalignment": (90, 170, 255),
    "missing_part": (40, 40, 48),
    "scratch": (235, 235, 245),
    "contamination": (120, 200, 120),
}


def render_part(
    part_id: str, defects: list[str], width: int, height: int, seed: int
) -> bytes:
    """Deterministic for (part_id, defects, size, seed) -- §3.6 requires the whole
    plant to be reproducible from a seed."""
    rng = random.Random(f"{seed}:{part_id}")
    img = Image.new("RGB", (width, height), BACKGROUND)
    draw = ImageDraw.Draw(img)

    # two joined components, which is what S2 pressed together
    mid = width // 2
    draw.rectangle([mid - width // 3, height // 4, mid, 3 * height // 4], fill=BODY)
    draw.rectangle([mid, height // 4, mid + width // 3, 3 * height // 4], fill=BODY)

    for defect in defects:
        ink = DEFECT_INK.get(defect, (255, 0, 255))
        x = rng.randint(width // 4, 3 * width // 4)
        y = rng.randint(height // 3, 2 * height // 3)
        if defect == "scratch":
            draw.line(
                [x, y, x + rng.randint(20, 60), y + rng.randint(-8, 8)],
                fill=ink,
                width=2,
            )
        elif defect == "gap":
            draw.rectangle([mid - 3, height // 4, mid + 3, 3 * height // 4], fill=ink)
        elif defect == "missing_part":
            draw.rectangle(
                [mid, height // 4, mid + width // 3, 3 * height // 4], fill=ink
            )
        else:
            r = rng.randint(6, 18)
            draw.ellipse([x - r, y - r, x + r, y + r], fill=ink)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
```

- [ ] **Step 4: Write the classifier behind a swappable interface**

```python
# plant/inspection/src/inspection/classifier.py
"""inspect(image_bytes, part_context) -> InspectionResult.

SimulatedClassifier resolves truth through a side channel keyed by part id.
A real ModelClassifier would ignore the channel entirely and this signature
would not change -- that is the whole point of the split (§3.4).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Protocol

DEFECT_CLASSES = [
    "gap",
    "crack",
    "misalignment",
    "missing_part",
    "scratch",
    "contamination",
]
MODEL_VERSION = "simulated-1"


@dataclass(frozen=True)
class PartContext:
    part_id: str
    carrier_id: int


@dataclass(frozen=True)
class InspectionResult:
    disposition: str  # "good" | "reject"
    defect_class: str | None
    confidence: float
    confidences: dict[str, float]
    model_version: str = MODEL_VERSION


class Classifier(Protocol):
    def classify(self, image: bytes, ctx: PartContext) -> InspectionResult: ...


@dataclass
class TruthChannel:
    """The side channel. Keyed by part id, reachable only inside plant-net, and
    never part of an /inspect request body."""

    _truth: dict[str, list[str]] = field(default_factory=dict)

    def declare(self, part_id: str, defects: list[str]) -> None:
        self._truth[part_id] = defects

    def lookup(self, part_id: str) -> list[str]:
        return self._truth.get(part_id, [])


class SimulatedClassifier:
    def __init__(self, truth: TruthChannel, seed: int) -> None:
        self._truth = truth
        self._seed = seed

    def classify(self, image: bytes, ctx: PartContext) -> InspectionResult:
        defects = self._truth.lookup(ctx.part_id)
        rng = random.Random(f"{self._seed}:{ctx.part_id}:{len(image)}")

        # A plausible distribution: mass concentrated on the true class, the rest
        # spread with noise, so the confidence field carries information.
        weights = {c: rng.uniform(0.01, 0.08) for c in DEFECT_CLASSES}
        top = defects[0] if defects else None
        if top:
            weights[top] = rng.uniform(0.55, 0.95)
        total = sum(weights.values())
        confidences = {c: w / total for c, w in weights.items()}

        if not defects:
            return InspectionResult(
                "good", None, max(confidences.values()), confidences
            )
        return InspectionResult("reject", top, confidences[top], confidences)
```

- [ ] **Step 5: Write the service**

```python
# plant/inspection/src/inspection/app.py
from __future__ import annotations

import base64
import os

from fastapi import FastAPI
from pydantic import BaseModel

from inspection.classifier import (
    DEFECT_CLASSES,
    PartContext,
    SimulatedClassifier,
    TruthChannel,
)

app = FastAPI(title="inspection-service")
_truth = TruthChannel()
_classifier = SimulatedClassifier(
    _truth, seed=int(os.environ.get("PLANT_SEED", "20260912"))
)


class TruthIn(BaseModel):
    defects: list[str]


class InspectIn(BaseModel):
    """Note what is absent: nothing about the true defect state. §4.5 and §3.4."""

    part_id: str
    image_b64: str
    carrier_id: int


@app.post("/truth/{part_id}")
def declare_truth(part_id: str, body: TruthIn) -> dict[str, str]:
    """Side channel. Inside plant-net only; never reachable across field-net."""
    _truth.declare(part_id, body.defects)
    return {"status": "ok"}


@app.post("/inspect")
def inspect(body: InspectIn) -> dict[str, object]:
    image = base64.b64decode(body.image_b64)
    result = _classifier.classify(image, PartContext(body.part_id, body.carrier_id))
    return {
        "disposition": result.disposition,
        "defect_class": result.defect_class,
        "confidence": result.confidence,
        "confidences": result.confidences,
        "model_version": result.model_version,
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
```

- [ ] **Step 6: Run the inspection tests to verify they pass**

Run: `cd plant && uv run --frozen --package inspection pytest inspection/tests -v`
Expected: PASS, 5 tests.

- [ ] **Step 7: Wire the simulator to the service**

```python
# plant/simulator/src/simulator/inspection_client.py
"""The simulator renders; the inspection service classifies. Two boxes, one wire."""

from __future__ import annotations

import base64
import random
from datetime import datetime

import httpx

from simulator.config import Settings
from simulator.station_s3 import PartOutcome

DEFECT_CLASSES = [
    "gap",
    "crack",
    "misalignment",
    "missing_part",
    "scratch",
    "contamination",
]


class InspectionClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        self._s = settings
        self._http = client
        self._rng = random.Random(settings.seed)

    async def produce(self, part_id: str, sim_ts: datetime) -> PartOutcome:
        from inspection.render import render_part  # noqa: PLC0415 -- see note below

        defects = (
            [self._rng.choice(DEFECT_CLASSES)]
            if self._rng.random() < self._s.reject_rate
            else []
        )
        image = render_part(
            part_id, defects, self._s.image_width, self._s.image_height, self._s.seed
        )

        # Truth goes down the side channel, keyed by part id.
        await self._http.post(
            f"{self._s.inspection_url}/truth/{part_id}", json={"defects": defects}
        )

        # The request itself carries only what a camera would hand over.
        response = await self._http.post(
            f"{self._s.inspection_url}/inspect",
            json={
                "part_id": part_id,
                "image_b64": base64.b64encode(image).decode(),
                "carrier_id": 1,
            },
        )
        result = response.json()
        rejected = result["disposition"] == "reject"
        return PartOutcome(
            disposition=result["disposition"],
            defect_class=result["defect_class"],
            confidence=result["confidence"],
            # §3.4: only rejected parts carry their image into the OPC UA event.
            image=image if rejected else None,
        )
```

The renderer lives in the `inspection` package but the *simulator* renders (§3.4: "the simulator renders the image"). Because the two are separate uv workspace members that must not depend on each other, copy `render.py` into `plant/simulator/src/simulator/render.py` and import it from there; the inspection service keeps its own copy for its tests. Duplicating ~40 lines is the correct cost of the workspace split, exactly as §10.7 argues for the duplicated lockfiles. Replace the import above with `from simulator.render import render_part`.

- [ ] **Step 8: Write the failing wiring test**

```python
# plant/simulator/tests/test_inspection_client.py
import httpx
import pytest

from simulator.config import Settings
from simulator.inspection_client import InspectionClient


@pytest.mark.asyncio
async def test_only_rejects_carry_an_image() -> None:
    """§3.4: only rejected parts carry their image into the OPC UA event. Good parts
    get a result without one -- which is what keeps images inside the single
    permitted channel rather than requiring a second."""
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append({"url": str(request.url), "body": request.content})
        if "/truth/" in str(request.url):
            return httpx.Response(200, json={"status": "ok"})
        reject = b"A-00000007" in request.content
        return httpx.Response(
            200,
            json={
                "disposition": "reject" if reject else "good",
                "defect_class": "gap" if reject else None,
                "confidence": 0.88,
                "confidences": {},
                "model_version": "simulated-1",
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = InspectionClient(
            Settings(reject_rate=1.0, image_width=64, image_height=64), http
        )
        rejected = await client.produce("A-00000007", None)
        good = await client.produce("A-00000008", None)

    assert rejected.image is not None and rejected.image.startswith(b"\x89PNG")
    assert good.image is None


@pytest.mark.asyncio
async def test_truth_never_appears_in_the_inspect_request() -> None:
    bodies: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if "/inspect" in str(request.url):
            bodies.append(request.content)
            return httpx.Response(
                200,
                json={
                    "disposition": "good",
                    "defect_class": None,
                    "confidence": 0.9,
                    "confidences": {},
                    "model_version": "simulated-1",
                },
            )
        return httpx.Response(200, json={"status": "ok"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await InspectionClient(
            Settings(reject_rate=1.0, image_width=64, image_height=64), http
        ).produce("A-1", None)

    assert bodies
    for body in bodies:
        assert b"defect" not in body
        assert b"truth" not in body
```

- [ ] **Step 9: Run both suites to verify they pass**

Run: `cd plant && uv run --frozen --package simulator pytest simulator/tests -q && uv run --frozen --package inspection pytest inspection/tests -q`
Expected: PASS.

- [ ] **Step 10: Record R4's first number**

Run the renderer over 500 part ids at the configured resolution and record `img_p50` and `img_p99`:

```bash
cd plant && uv run --frozen --package simulator python - <<'EOF'
import statistics
from simulator.render import render_part
sizes = [len(render_part(f"A-{i:08d}", ["gap"], 640, 480, seed=20260912)) for i in range(500)]
sizes.sort()
print(f"img_p50={sizes[len(sizes)//2]} img_p99={sizes[int(len(sizes)*0.99)]} max={sizes[-1]}")
EOF
```

Write the result into `measurements/r4-image-sizes.txt`. Task 14 uses `img_p99` as the base of the ceiling search.

- [ ] **Step 11: Commit**

```bash
git add plant/inspection plant/simulator/src/simulator/render.py plant/simulator/src/simulator/inspection_client.py plant/simulator/tests measurements
git commit -m "feat(inspection): separate vision service with a truth side channel, images on rejects only"
```

---

## Task 6: The one-name boundary — endpoint URL, SANs and published port

**Expect this to be the hardest task in M1.** Read *The one-name boundary* above before starting; it explains why these three cannot be solved separately. This task proves the server half and matrix rows 2 and 3. Task 7 closes row 1 with the client that actually matters.

**Files:**
- Create: `plant/simulator/src/simulator/server.py`, `plant/simulator/src/simulator/status.py`
- Create: `plant/compose.yml`, `plant/.env.example`
- Create: `measurements/run_r3.py`
- Test: `plant/simulator/tests/test_boundary.py`

**Interfaces:**
- Consumes: Tasks 2–5.
- Produces: `async build_server(settings) -> tuple[Server, AddressSpace]`; a running endpoint at `opc.tcp://line-simulator:4840/plant` accepting `Basic256Sha256_Sign` with mutual trust; `run_r3.py` emitting the matrix as JSON.

- [ ] **Step 1: Write the failing boundary test**

```python
# plant/simulator/tests/test_boundary.py
import pytest
from asyncua import Client, ua
from asyncua.crypto import security_policies

from simulator.config import Settings
from simulator.server import build_server

PKI = "pki"


@pytest.mark.asyncio
async def test_server_advertises_only_sign_no_unsecured_endpoint() -> None:
    """§4.6: SecurityMode Sign. §10.5 rules out SignAndEncrypt for M1. An open
    endpoint would make the boundary narrow but unauthenticated."""
    settings = Settings(endpoint_url="opc.tcp://127.0.0.1:48410/plant", pki_root=PKI)
    server, _ = await build_server(settings)
    async with server:
        endpoints = await server.get_endpoints()

    modes = {e.SecurityMode for e in endpoints}
    assert modes == {ua.MessageSecurityMode.Sign}
    assert all("Basic256Sha256" in e.SecurityPolicyUri for e in endpoints)


@pytest.mark.asyncio
async def test_an_untrusted_client_is_rejected() -> None:
    """Mutual trust means the server refuses a certificate it has never been given."""
    settings = Settings(endpoint_url="opc.tcp://127.0.0.1:48411/plant", pki_root=PKI)
    server, _ = await build_server(settings)
    async with server:
        client = Client(settings.endpoint_url)
        await client.set_security(
            security_policies.SecurityPolicyBasic256Sha256,
            certificate="tests/fixtures/stranger-cert.der",
            private_key="tests/fixtures/stranger-key.pem",
            server_certificate=f"{PKI}/line-simulator/cert.der",
            mode=ua.MessageSecurityMode.Sign,
        )
        with pytest.raises(Exception) as excinfo:
            await client.connect()
        assert "Untrusted" in str(excinfo.value) or "BadCertificate" in str(
            excinfo.value
        )


@pytest.mark.asyncio
async def test_the_trusted_gateway_certificate_connects_and_browses() -> None:
    settings = Settings(endpoint_url="opc.tcp://127.0.0.1:48412/plant", pki_root=PKI)
    server, space = await build_server(settings)
    async with server:
        client = Client(settings.endpoint_url)
        await client.set_security(
            security_policies.SecurityPolicyBasic256Sha256,
            certificate=f"{PKI}/edge-gateway/cert.der",
            private_key=f"{PKI}/edge-gateway/key.pem",
            server_certificate=f"{PKI}/line-simulator/cert.der",
            mode=ua.MessageSecurityMode.Sign,
        )
        async with client:
            node = client.get_node(space.s3.nodeid)
            names = {
                (await c.read_browse_name()).Name for c in await node.get_children()
            }
            assert {"TaktTime", "PartCount"} <= names
```

Generate the stranger fixture once with `simulator.pki.gen_party` into `tests/fixtures/` using a third `Party` that is never published to `pki/trusted/`.

- [ ] **Step 2: Run it to verify it fails**

Run: `cd plant && uv run --frozen --package simulator pytest simulator/tests/test_boundary.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'simulator.server'`.

- [ ] **Step 3: Write the server**

```python
# plant/simulator/src/simulator/server.py
"""The plant's single exposed port (§2.1, §4.6).

The endpoint URL is simultaneously a bind address, a discovery advertisement, a
session-validation token and a certificate constraint. One name, one port,
everywhere -- see the plan's "one-name boundary".
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from asyncua import Server, ua
from asyncua.crypto.truststore import TrustStore
from asyncua.crypto.validator import CertificateValidator, CertificateValidatorOptions

from simulator.address_space import AddressSpace, build_address_space
from simulator.config import ClockConfig, Settings
from simulator.clock import SimulatedClock

NAMESPACE = "http://machine-agent/plant"
log = logging.getLogger(__name__)


async def build_server(settings: Settings) -> tuple[Server, AddressSpace]:
    pki = Path(settings.pki_root)

    server = Server()
    await server.init()

    # The host component of this URL is what asyncua binds to, what it advertises
    # in discovery, and what a client's session validation is checked against. It
    # must equal the Compose service name, and the port must be identical inside
    # and out -- a published 14840:4840 would make the host dial a port the server
    # never advertises, which is §12's BadTcpEndpointUrlInvalid trap verbatim.
    server.set_endpoint(settings.endpoint_url)
    await server.set_application_uri(settings.application_uri)
    server.set_server_name("machine-agent plant")

    await server.load_certificate(str(pki / "line-simulator" / "cert.der"))
    await server.load_private_key(str(pki / "line-simulator" / "key.pem"))

    # Sign only. No open endpoint, and no SignAndEncrypt (§10.5 defers it).
    server.set_security_policy([ua.SecurityPolicyType.Basic256Sha256_Sign])

    # Mutual trust from the shared, pre-seeded trust directory, so a restart does
    # not need re-trusting by hand (§14).
    trust = TrustStore([pki / "trusted"], [])
    await trust.load()
    server.set_certificate_validator(
        CertificateValidator(
            CertificateValidatorOptions.TRUSTED_VALIDATION
            | CertificateValidatorOptions.PEER_CLIENT,
            trust,
        )
    )

    # asyncua rewrites advertised endpoint hosts during discovery to match the
    # client's view. Inside Docker the client IP the server sees is not the client
    # IP the client has, so the source-IP rewrite is disabled; asyncua's own source
    # documents this flag for exactly this case. Whether the URL rewrite must also
    # be disabled is what matrix rows 2 and 3 decide -- leave it at its default
    # here and record the answer.
    server.set_match_discovery_client_ip(False)

    idx = await server.register_namespace(NAMESPACE)
    space = await build_address_space(server, idx)
    return server, space


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format='{"lvl":"%(levelname)s","msg":"%(message)s"}'
    )
    settings = Settings()

    from simulator.historian import Ledger, attach_historian
    from simulator.inspection_client import InspectionClient
    from simulator.station_s3 import generate_history, run_live
    import httpx

    server, space = await build_server(settings)
    await attach_historian(
        server, space, Path("/data/history.db"), settings.history_page_size
    )

    clock = SimulatedClock(
        ClockConfig(
            history_depth=ClockConfig.DEFAULT_HISTORY_DEPTH,
            catchup_speed=settings.catchup_speed,
        )
    )
    ledger = Ledger()

    async with server, httpx.AsyncClient(timeout=30.0) as http:
        produce = InspectionClient(settings, http).produce
        log.info(
            "phase=catchup depth=%s speed=%s",
            clock._cfg.history_depth,
            settings.catchup_speed,
        )
        await generate_history(space, clock, settings, produce, ledger)
        log.info(
            "phase=live ledger takt=%d part_count=%d events=%d images=%d image_bytes=%d",
            ledger.takt,
            ledger.part_count,
            ledger.events,
            ledger.images,
            ledger.image_bytes,
        )
        await run_live(
            space, clock, settings, produce, ledger, start_index=ledger.events
        )


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 4: Run the boundary test to verify it passes**

Run: `cd plant && uv run --frozen --package simulator pytest simulator/tests/test_boundary.py -v`
Expected: PASS, 3 tests.

- [ ] **Step 5: Write the plant compose file**

```yaml
# plant/compose.yml
name: machine-agent-plant

services:
  pki-init:
    build:
      context: .
      dockerfile: Dockerfile
      args: { PACKAGE: simulator }
    command: ["python", "-m", "simulator.pki", "/pki"]
    volumes:
      - ../pki:/pki
    networks: [plant-net]

  inspection-service:
    build:
      context: .
      dockerfile: Dockerfile
      args: { PACKAGE: inspection }
    command: ["uvicorn", "inspection.app:app", "--host", "0.0.0.0", "--port", "8100"]
    environment:
      PLANT_SEED: "${PLANT_SEED:-20260912}"
    networks: [plant-net]          # never field-net: the truth side channel lives here

  line-simulator:
    build:
      context: .
      dockerfile: Dockerfile
      args: { PACKAGE: simulator }
    command: ["python", "-m", "simulator.server"]
    depends_on:
      pki-init:
        condition: service_completed_successfully
      inspection-service:
        condition: service_started
    environment:
      PLANT_ENDPOINT_URL: "opc.tcp://line-simulator:4840/plant"
      PLANT_INSPECTION_URL: "http://inspection-service:8100"
      PLANT_PKI_ROOT: "/pki"
      PLANT_SEED: "${PLANT_SEED:-20260912}"
      PLANT_CATCHUP_SPEED: "${PLANT_CATCHUP_SPEED:-600}"
      PLANT_TAKT_SECONDS: "${PLANT_TAKT_SECONDS:-6}"
      PLANT_REJECT_RATE: "${PLANT_REJECT_RATE:-0.05}"
    volumes:
      - ../pki:/pki:ro
      - plant-history:/data
    ports:
      - "4840:4840"                # identical inside and out -- see §12 and Task 6
    networks: [plant-net, field-net]

volumes:
  plant-history:

networks:
  plant-net:
    internal: true
  field-net:
    external: true                 # exactly two containers join this, ever
```

- [ ] **Step 6: Write the R3 matrix runner**

```python
# measurements/run_r3.py
"""R3: endpoint URL vs Docker hostname. Six cells, every failure recorded with
its exact StatusCode (§12 row 3)."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys

from asyncua import Client, ua
from asyncua.crypto import security_policies

PKI = "pki"
CELLS = [
    ("host-published-port", "opc.tcp://localhost:4840/plant"),
    ("host-etc-hosts", "opc.tcp://line-simulator:4840/plant"),
]


async def try_connect(url: str, secure: bool) -> dict[str, object]:
    client = Client(url, timeout=10)
    try:
        if secure:
            await client.set_security(
                security_policies.SecurityPolicyBasic256Sha256,
                certificate=f"{PKI}/edge-gateway/cert.der",
                private_key=f"{PKI}/edge-gateway/key.pem",
                server_certificate=f"{PKI}/line-simulator/cert.der",
                mode=ua.MessageSecurityMode.Sign,
            )
        async with client:
            await client.nodes.root.get_children()
        return {"ok": True, "status": "Good"}
    except Exception as exc:  # noqa: BLE001 -- the failure text IS the measurement
        return {"ok": False, "status": f"{type(exc).__name__}: {exc}"}


def gateway_cell(secure: bool) -> dict[str, object]:
    """Row 1 uses the real UA-.NETStandard client from inside field-net.
    Requires Task 7's --connect-test mode."""
    cmd = [
        "docker",
        "run",
        "--rm",
        "--network",
        "field-net",
        "-v",
        f"{subprocess.check_output(['pwd']).decode().strip()}/pki:/pki:ro",
        "machine-agent/edge-gateway:dev",
        "--connect-test",
        "opc.tcp://line-simulator:4840/plant",
        "--security",
        "Sign" if secure else "None",
    ]
    out = subprocess.run(cmd, capture_output=True, text=True)
    return {
        "ok": out.returncode == 0,
        "status": (out.stdout + out.stderr).strip()[-200:],
    }


async def main() -> None:
    matrix: dict[str, dict[str, object]] = {}
    for secure in (False, True):
        mode = "Sign" if secure else "NoSecurity"
        matrix[f"gateway-field-net/{mode}"] = gateway_cell(secure)
        for name, url in CELLS:
            matrix[f"{name}/{mode}"] = await try_connect(url, secure)

    json.dump(matrix, sys.stdout, indent=2)
    print()
    required = ["gateway-field-net/Sign"]
    host_rows = [k for k in matrix if k.startswith("host-") and k.endswith("/Sign")]
    passed = all(matrix[k]["ok"] for k in required) and any(
        matrix[k]["ok"] for k in host_rows
    )
    print(f"\nR3: {'PASS' if passed else 'FAIL'}", file=sys.stderr)
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 7: Bring the plant up and run rows 2 and 3**

```bash
make preflight
docker compose -f plant/compose.yml up -d --build
docker compose -f plant/compose.yml logs -f line-simulator     # watch booting -> catchup -> live
cd .. && uv run --frozen --package simulator python measurements/run_r3.py
```

Record every cell. Row 1 will fail until Task 7 builds the gateway image; that is expected here.

**If `host-published-port/Sign` fails with `BadTcpEndpointUrlInvalid`:** the client dialled `localhost` and the server advertises `line-simulator`. Try, in order: (a) confirm the `/etc/hosts` row passes, which is the intended resolution; (b) set `server.set_match_discovery_endpoint_url(True)` explicitly and re-measure; (c) record it as a known limit and keep the host path for browsing only. Do **not** publish a different external port to work around it — that breaks the single advertised URL and re-creates the trap elsewhere.

**If it fails with `BadCertificateHostNameInvalid`:** the DNS SAN list is short. Task 2's `test_server_dns_sans_cover_every_name_a_client_may_dial` should have caught it; add the missing name there first, then regenerate.

- [ ] **Step 8: Commit**

```bash
git add plant/compose.yml plant/.env.example plant/simulator/src/simulator/server.py plant/simulator/tests/test_boundary.py measurements/run_r3.py
git commit -m "feat(plant): signed OPC UA endpoint on one name and one port, with the R3 matrix"
```

---

## Task 7: Gateway — connect at Sign, reconnect, and `/status`

**Files:**
- Create: `diagnostics/gateway/Gateway.csproj`, `Program.cs`, `Opc/UaConnection.cs`, `Status/StatusEndpoint.cs`, `Dockerfile`
- Test: `diagnostics/gateway/Gateway.Tests/ConnectionTests.cs`

**Interfaces:**
- Consumes: the plant endpoint from Task 6; `pki/` from Task 2.
- Produces: `UaConnection.ConnectAsync(CancellationToken) -> Task<ISession>`; `GatewayStatus` record with `State`, `LastEventSourceTs`, `QueueDepth`, `BackfillProgress`, `OverflowCount`, `RowsWritten`; `GET /status`; a `--connect-test <url> --security <None|Sign>` mode for R3 row 1.

> **Note on the SDK surface.** These bindings were read from UA-.NETStandard 1.5.378.176 before this plan was written. Where a member name differs, let the compiler point at it and fix — do not go researching. The reference sample loads its configuration from XML; this gateway builds it in code so that the certificate store paths come from `pki/`.

- [ ] **Step 1: Create the project with a committed lock file**

```bash
mkdir -p diagnostics/gateway && cd diagnostics/gateway
dotnet new web -o . --framework net9.0
dotnet add package OPCFoundation.NetStandard.Opc.Ua.Client --version 1.5.378.176
dotnet add package OPCFoundation.NetStandard.Opc.Ua.Configuration --version 1.5.378.176
dotnet add package Npgsql --version 10.0.3
dotnet add package Microsoft.Data.Sqlite --version 10.0.12
dotnet restore --use-lock-file      # writes packages.lock.json (§10.7)
```

Add to `Gateway.csproj`:
```xml
<PropertyGroup>
  <RestorePackagesWithLockFile>true</RestorePackagesWithLockFile>
  <TreatWarningsAsErrors>true</TreatWarningsAsErrors>
  <Nullable>enable</Nullable>
</PropertyGroup>
```

- [ ] **Step 2: Write the failing connection test**

```csharp
// diagnostics/gateway/Gateway.Tests/ConnectionTests.cs
using Gateway.Opc;
using Xunit;

public class ConnectionTests
{
    [Fact]
    public void ApplicationUriMustMatchTheCertificateUriSan()
    {
        // asyncua raises BadCertificateUriInvalid unless these are byte-identical.
        var options = GatewayOptions.FromEnvironment(new Dictionary<string, string?>
        {
            ["GATEWAY_APPLICATION_URI"] = "urn:machine-agent:diagnostics:edge-gateway",
            ["GATEWAY_PKI_ROOT"] = "/pki",
            ["GATEWAY_ENDPOINT_URL"] = "opc.tcp://line-simulator:4840/plant",
        });

        var uriSan = CertificateInspector.UriSan(options.OwnCertificatePath);
        Assert.Equal(options.ApplicationUri, uriSan);
    }

    [Fact]
    public void SecurityModeIsSignAndNeverNone()
    {
        var options = GatewayOptions.Default();
        Assert.Equal("Sign", options.SecurityMode);
    }
}
```

- [ ] **Step 3: Run it to verify it fails**

Run: `cd diagnostics/gateway && dotnet test --locked-mode`
Expected: FAIL — `GatewayOptions` and `CertificateInspector` do not exist.

- [ ] **Step 4: Write the connection**

```csharp
// diagnostics/gateway/Opc/UaConnection.cs
using Microsoft.Extensions.Logging;
using Opc.Ua;
using Opc.Ua.Client;
using Opc.Ua.Configuration;

namespace Gateway.Opc;

public sealed class UaConnection : IAsyncDisposable
{
    private readonly GatewayOptions _options;
    private readonly ITelemetryContext _telemetry;
    private readonly ILogger<UaConnection> _logger;
    private ApplicationConfiguration? _configuration;
    private SessionReconnectHandler? _reconnectHandler;

    public ISession? Session { get; private set; }
    public event Action<string>? StateChanged;

    public UaConnection(GatewayOptions options, ITelemetryContext telemetry)
    {
        _options = options;
        _telemetry = telemetry;
        _logger = telemetry.LoggerFactory.CreateLogger<UaConnection>();
    }

    public async Task<ApplicationConfiguration> BuildConfigurationAsync()
    {
        var application = new ApplicationInstance(_telemetry)
        {
            ApplicationName = "machine-agent edge gateway",
            ApplicationType = ApplicationType.Client,
        };

        // Built in code, not loaded from XML, so the store paths come from pki/.
        _configuration = await application
            .Build(_options.ApplicationUri, "urn:machine-agent:diagnostics:edge-gateway:product")
            .AsClient()
            .SetDefaultSessionTimeout(60_000)
            .AddSecurityConfigurationStores(
                subjectName: "CN=edge-gateway, O=machine-agent, C=DE",
                appRoot:     _options.OwnStoreRoot,      // pki/edge-gateway
                trustedRoot: _options.TrustedStoreRoot,  // pki/trusted
                issuerRoot:  _options.TrustedStoreRoot,
                rejectedRoot: _options.RejectedStoreRoot)
            // The certificate is minted by pki-init, never here: letting .NET mint
            // it would derive the DNS SAN from the container hostname, which changes
            // on every recreate and breaks the pre-seeded trust §14 requires.
            .SetAutoAcceptUntrustedCertificates(false)
            .SetAddAppCertToTrustedStore(false)
            .SetRejectSHA1SignedCertificates(true)
            .SetRejectUnknownRevocationStatus(false)   // self-signed, no CRL distribution
            .SetTransportQuotas(new TransportQuotas
            {
                OperationTimeout = 120_000,
                // Sized against R4's measured img_p99; raise both together.
                MaxByteStringLength = _options.MaxByteStringLength,
                MaxMessageSize = _options.MaxMessageSize,
                MaxBufferSize = 65_535,
            })
            .Create()
            .ConfigureAwait(false);

        // Verifies the pki-init certificate is present and usable. It does not mint
        // one, because AddAppCertToTrustedStore is off and the material already exists.
        var ok = await application.CheckApplicationInstanceCertificatesAsync(silent: true).ConfigureAwait(false);
        if (!ok)
        {
            throw new InvalidOperationException(
                $"no usable application instance certificate in {_options.OwnStoreRoot}; did pki-init run?");
        }

        return _configuration;
    }

    public async Task<ISession> ConnectAsync(CancellationToken ct)
    {
        var config = _configuration ?? await BuildConfigurationAsync().ConfigureAwait(false);
        StateChanged?.Invoke("connecting");

        var useSecurity = _options.SecurityMode != "None";
        var endpointDescription = await CoreClientUtils
            .SelectEndpointAsync(config, _options.EndpointUrl, useSecurity, _telemetry, ct)
            .ConfigureAwait(false)
            ?? throw new ServiceResultException(StatusCodes.BadNotConnected,
                $"no endpoint at {_options.EndpointUrl}");

        var endpoint = new ConfiguredEndpoint(
            null, endpointDescription, EndpointConfiguration.Create(config));

        var factory = new DefaultSessionFactory(_telemetry);
        var session = await factory.CreateAsync(
            config,
            endpoint,
            updateBeforeConnect: false,
            // checkDomain compares the endpoint URL's host against the server
            // certificate's DNS SANs. Kept on deliberately: switching it off would
            // hide exactly the SAN mismatch §12 warns about.
            checkDomain: true,
            sessionName: "machine-agent-gateway",
            sessionTimeout: 60_000,
            identity: new UserIdentity(),   // anonymous; §10.5 defers user auth
            preferredLocales: null,
            ct).ConfigureAwait(false);

        session.KeepAliveInterval = 5_000;
        session.DeleteSubscriptionsOnClose = false;
        session.TransferSubscriptionsOnReconnect = true;
        session.KeepAlive += OnKeepAlive;

        _reconnectHandler = new SessionReconnectHandler(_telemetry, true, 30_000);
        Session = session;
        StateChanged?.Invoke("live");
        _logger.LogInformation("session established {Session}", session.SessionName);
        return session;
    }

    private void OnKeepAlive(ISession session, KeepAliveEventArgs e)
    {
        if (Session is null || !Session.Equals(session) || !ServiceResult.IsBad(e.Status))
        {
            return;
        }

        StateChanged?.Invoke("disconnected");
        var state = _reconnectHandler!.BeginReconnect(session, 2_000, OnReconnectComplete);
        if (state == SessionReconnectHandler.ReconnectState.Triggered)
        {
            e.CancelKeepAlive = true;
        }
    }

    private void OnReconnectComplete(object? sender, EventArgs e)
    {
        if (!ReferenceEquals(sender, _reconnectHandler) || _reconnectHandler!.Session is null)
        {
            return;
        }

        if (!ReferenceEquals(Session, _reconnectHandler.Session))
        {
            var old = Session;
            Session = _reconnectHandler.Session;
            Utils.SilentDispose(old);
        }

        // A new or reactivated session means the plant may have been away. Backfill
        // closes whatever gap exists -- one mechanism, three situations (§4.3).
        StateChanged?.Invoke("backfilling");
    }

    public async ValueTask DisposeAsync()
    {
        if (Session is not null)
        {
            Session.KeepAlive -= OnKeepAlive;
            await Session.CloseAsync().ConfigureAwait(false);
            Session.Dispose();
        }
        _reconnectHandler?.Dispose();
    }
}
```

- [ ] **Step 5: Write `/status` and the connect-test mode**

```csharp
// diagnostics/gateway/Status/StatusEndpoint.cs
namespace Gateway.Status;

public sealed record GatewayStatus(
    string State,                      // disconnected | connecting | backfilling | live
    DateTime? LastEventSourceTs,       // simulated time, never wall clock (§4.2)
    int QueueDepth,
    double BackfillProgress,           // 0.0 .. 1.0
    int OverflowCount,
    long RowsWritten,
    string? HistoryAvailableFrom);     // "plant booting -- history available from 03:14" (§4.3)

public static class StatusEndpoint
{
    public static void Map(WebApplication app, Func<GatewayStatus> snapshot)
        => app.MapGet("/status", () => Results.Json(snapshot()));
}
```

`Program.cs` handles `--connect-test <url> --security <None|Sign>` by building the configuration, attempting `ConnectAsync`, printing `Good` or the `ServiceResultException`'s `StatusCode`, and exiting 0 or 1. That is what `measurements/run_r3.py` shells into for matrix row 1.

- [ ] **Step 6: Write the gateway Dockerfile**

```dockerfile
# diagnostics/gateway/Dockerfile
# syntax=docker/dockerfile:1
FROM mcr.microsoft.com/dotnet/sdk:9.0@sha256:20387c6674c30e46def0cc8cb557bd2a69b35afdf18c19c3694638d0a90897e4 AS build
WORKDIR /src
COPY Gateway.csproj packages.lock.json global.json ./
RUN dotnet restore --locked-mode
COPY . .
RUN dotnet publish -c Release -o /app --no-restore

FROM mcr.microsoft.com/dotnet/aspnet:9.0@sha256:779b0b298964fe93e6c6388c8bb823393a7c301b79cd5b3d80632bc886b2ffd4 AS runtime
WORKDIR /app
COPY --from=build /app .
RUN useradd --system --uid 10002 gateway && mkdir -p /queue && chown gateway /queue
USER gateway
ENTRYPOINT ["dotnet", "Gateway.dll"]
```

- [ ] **Step 7: Close R3 row 1 and re-run the whole matrix**

```bash
docker build -t machine-agent/edge-gateway:dev diagnostics/gateway
uv run --frozen --package simulator python measurements/run_r3.py | tee measurements/r3-matrix.json
```

Expected: `gateway-field-net/Sign` green. **This is R3's pass condition and the moment the boundary is proven.** Record all six cells verbatim; Task 17 puts them in the report.

- [ ] **Step 8: Commit**

```bash
git add diagnostics/gateway measurements/r3-matrix.json
git commit -m "feat(gateway): signed OPC UA session with reconnect, /status and a connect-test mode

Closes R3: the endpoint URL, certificate SANs and published port resolve on one name."
```

---

## Task 8: Subscription ingest into a durable local queue

**Files:**
- Create: `diagnostics/gateway/Opc/Subscriptions.cs`, `Ingest/LocalQueue.cs`, `Ingest/IngestRecord.cs`
- Test: `diagnostics/gateway/Gateway.Tests/LocalQueueTests.cs`

**Interfaces:**
- Consumes: `UaConnection.Session` (Task 7).
- Produces: `IngestRecord(Kind, NodeId, SourceTs, ServerTs, StatusCode, PayloadJson, ImageBytes)` where `Kind` is `"datachange"` or `"event"`; `LocalQueue.EnqueueAsync(IngestRecord)`, `DequeueBatchAsync(int) -> IReadOnlyList<(long Id, IngestRecord)>`, `AckAsync(IEnumerable<long>)`, `DepthAsync() -> int`; `Subscriptions.StartAsync(session, space, onRecord, ct)`.

- [ ] **Step 1: Write the failing queue test**

```csharp
// diagnostics/gateway/Gateway.Tests/LocalQueueTests.cs
using Gateway.Ingest;
using Xunit;

public class LocalQueueTests
{
    [Fact]
    public async Task SurvivesProcessRestart()
    {
        // §5.1: the local queue is a SQLite file on a volume -- durable, inspectable,
        // and you can open it mid-outage to prove it is filling.
        var path = Path.Combine(Path.GetTempPath(), $"q-{Guid.NewGuid():N}.db");
        await using (var queue = await LocalQueue.OpenAsync(path))
        {
            await queue.EnqueueAsync(Sample("A-1"));
            await queue.EnqueueAsync(Sample("A-2"));
        }

        await using var reopened = await LocalQueue.OpenAsync(path);
        Assert.Equal(2, await reopened.DepthAsync());
    }

    [Fact]
    public async Task AckRemovesOnlyAckedRows()
    {
        var path = Path.Combine(Path.GetTempPath(), $"q-{Guid.NewGuid():N}.db");
        await using var queue = await LocalQueue.OpenAsync(path);
        await queue.EnqueueAsync(Sample("A-1"));
        await queue.EnqueueAsync(Sample("A-2"));

        var batch = await queue.DequeueBatchAsync(1);
        await queue.AckAsync(batch.Select(b => b.Id));

        Assert.Equal(1, await queue.DepthAsync());
    }

    [Fact]
    public async Task PreservesImageBytesExactly()
    {
        var image = new byte[200_000];
        Random.Shared.NextBytes(image);
        var path = Path.Combine(Path.GetTempPath(), $"q-{Guid.NewGuid():N}.db");
        await using var queue = await LocalQueue.OpenAsync(path);
        await queue.EnqueueAsync(Sample("A-1") with { ImageBytes = image });

        var batch = await queue.DequeueBatchAsync(1);
        Assert.Equal(image, batch[0].Record.ImageBytes);
    }

    private static IngestRecord Sample(string serial) => new(
        Kind: "event", NodeId: "ns=2;i=5",
        SourceTs: new DateTime(2026, 9, 12, 2, 14, 0, DateTimeKind.Utc),
        ServerTs: DateTime.UtcNow, StatusCode: 0,
        PayloadJson: $"{{\"AssemblySerial\":\"{serial}\"}}", ImageBytes: null);
}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd diagnostics/gateway && dotnet test --locked-mode --filter LocalQueueTests`
Expected: FAIL — `LocalQueue` does not exist.

- [ ] **Step 3: Write the queue**

`LocalQueue` opens a SQLite file with `journal_mode=WAL`, one table:

```sql
CREATE TABLE IF NOT EXISTS queue (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  kind         TEXT    NOT NULL,
  node_id      TEXT    NOT NULL,
  source_ts    TEXT    NOT NULL,   -- ISO-8601 UTC, simulated time
  server_ts    TEXT    NOT NULL,   -- ISO-8601 UTC, wall clock
  status_code  INTEGER NOT NULL,
  payload_json TEXT    NOT NULL,
  image_bytes  BLOB
);
```

`EnqueueAsync` inserts; `DequeueBatchAsync(n)` selects `ORDER BY id LIMIT n`; `AckAsync` deletes by id. Ordering is by insertion for drain purposes only — **nothing downstream may assume arrival order** (§4.4), which is why `source_ts` is carried explicitly and Task 9 orders by it.

- [ ] **Step 4: Write the subscription**

```csharp
// diagnostics/gateway/Opc/Subscriptions.cs
// Two variables and one event stream, per §4.2's three kinds of traffic.
var subscription = new Subscription(session.DefaultSubscription)
{
    DisplayName = "machine-agent S3",
    PublishingEnabled = true,
    PublishingInterval = options.PublishingIntervalMs,   // 250 default
    KeepAliveCount = 10,
    LifetimeCount = 100,
    MaxNotificationsPerPublish = 0,                      // server default
};
session.AddSubscription(subscription);
await subscription.CreateAsync(ct);

// TaktTime: a noisy float, deadband is meaningful.
var takt = new MonitoredItem(subscription.DefaultItem)
{
    StartNodeId = space.TaktNodeId,
    AttributeId = Attributes.Value,
    DisplayName = "S3.TaktTime",
    SamplingInterval = options.SamplingIntervalMs,
    QueueSize = options.QueueSize,                       // 100 default
    DiscardOldest = true,
    Filter = new DataChangeFilter
    {
        Trigger = DataChangeTrigger.StatusValue,
        DeadbandType = (uint)DeadbandType.Absolute,
        DeadbandValue = options.TaktDeadband,            // 0.05 s default
    },
};

// PartCount: monotonic. A deadband here would silently lose parts, so there is
// none. §5.1 makes deadbands the gateway's job but never says they are per-signal;
// this pair settles that they must be.
var partCount = new MonitoredItem(subscription.DefaultItem)
{
    StartNodeId = space.PartCountNodeId,
    AttributeId = Attributes.Value,
    DisplayName = "S3.PartCount",
    SamplingInterval = options.SamplingIntervalMs,
    QueueSize = options.QueueSize,
    DiscardOldest = false,   // prefer failing loudly over dropping a count
    Filter = null,
};

// Inspection events, including the image ByteString.
var events = new MonitoredItem(subscription.DefaultItem)
{
    StartNodeId = space.S3NodeId,
    AttributeId = Attributes.EventNotifier,
    NodeClass = NodeClass.Object,
    DisplayName = "S3.InspectionResult",
    SamplingInterval = 0,
    QueueSize = options.EventQueueSize,                  // 200 default
    Filter = BuildInspectionFilter(),
};
```

`BuildInspectionFilter()` returns an `EventFilter` whose `SelectClauses` is a `SimpleAttributeOperandCollection` carrying one `SimpleAttributeOperand` per field of `InspectionResultEventType` — `Time`, `AssemblySerial`, `Disposition`, `DefectClass`, `Confidence`, `ModelVersion`, `Image` — each with `AttributeId = Attributes.Value`, `TypeDefinitionId = ObjectTypeIds.BaseEventType`, and `BrowsePath = new QualifiedNameCollection([name])`. **Keep the select-clause order in a named array**: the notification arrives as a positional `EventFieldList`, so the order is the decoding contract and must be shared with Task 10's history reader, which needs the identical filter.

The notification handlers read `e.NotificationValue as MonitoredItemNotification` (`.Value` is a `DataValue`) and `as EventFieldList` (`.EventFields[i]` is a `Variant`), convert to `IngestRecord`, and enqueue. Overflow detection:

```csharp
// §4.4: the server sets the overflow bit when it dropped notifications.
// DiscardOldest=false replaces the NEWEST value -- it is not a lossless setting.
// Losslessness comes from an adequate queue plus a fast publishing interval,
// with this bit as the honest detector when that fails.
if (notification.Value.StatusCode.Overflow)
{
    _overflowCount++;
    await _gaps.MarkAsync(lastGoodSourceTs, notification.Value.SourceTimestamp, "subscription_overflow");
}
```

- [ ] **Step 5: Run the queue tests to verify they pass**

Run: `cd diagnostics/gateway && dotnet test --locked-mode --filter LocalQueueTests`
Expected: PASS, 3 tests.

- [ ] **Step 6: Commit**

```bash
git add diagnostics/gateway/Opc/Subscriptions.cs diagnostics/gateway/Ingest diagnostics/gateway/Gateway.Tests
git commit -m "feat(gateway): subscription ingest with per-signal deadbands into a durable SQLite queue"
```

---

## Task 9: Postgres — raw landing and normalised model in one transaction

**Files:**
- Create: `diagnostics/gateway/Migrations/001_m1.sql`, `Ingest/PostgresWriter.cs`
- Create: `measurements/run_r4.py`
- Test: `diagnostics/gateway/Gateway.Tests/PostgresWriterTests.cs`

**Interfaces:**
- Consumes: `IngestRecord`, `LocalQueue` (Task 8).
- Produces: `PostgresWriter.WriteBatchAsync(IReadOnlyList<IngestRecord>) -> Task<int>`, returning rows affected; the M1 schema.

- [ ] **Step 1: Write the schema**

```sql
-- diagnostics/gateway/Migrations/001_m1.sql
-- M1 subset of §5.2. Plain PostgreSQL with BRIN indexes on time columns;
-- TimescaleDB is not justified at this volume (§5.2).

CREATE TABLE IF NOT EXISTS raw_events (
  id           BIGSERIAL PRIMARY KEY,
  received_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  source_ts    TIMESTAMPTZ NOT NULL,
  server_ts    TIMESTAMPTZ NOT NULL,
  kind         TEXT        NOT NULL,
  node_id      TEXT        NOT NULL,
  payload      JSONB       NOT NULL,
  status_code  BIGINT      NOT NULL
);
CREATE INDEX IF NOT EXISTS raw_events_source_ts_brin ON raw_events USING brin (source_ts);

CREATE TABLE IF NOT EXISTS stations (
  id               SMALLSERIAL PRIMARY KEY,
  code             TEXT NOT NULL UNIQUE,   -- discovered by browsing (§4.1)
  name             TEXT NOT NULL,
  function         TEXT,
  position_in_line SMALLINT
);

CREATE TABLE IF NOT EXISTS signals (
  station_id SMALLINT    NOT NULL REFERENCES stations(id),
  signal     TEXT        NOT NULL,
  source_ts  TIMESTAMPTZ NOT NULL,
  value      DOUBLE PRECISION NOT NULL,
  -- §4.4: backfill overlapping live data produces duplicates. So does paging:
  -- the historian's continuation point is the first row of the next page and the
  -- next query re-includes it, so this key fires on every page boundary.
  PRIMARY KEY (station_id, signal, source_ts)
);
CREATE INDEX IF NOT EXISTS signals_source_ts_brin ON signals USING brin (source_ts);

CREATE TABLE IF NOT EXISTS inspection_results (
  assembly_serial TEXT        PRIMARY KEY,
  source_ts       TIMESTAMPTZ NOT NULL,
  station_id      SMALLINT    NOT NULL REFERENCES stations(id),
  result          TEXT        NOT NULL,      -- good | reject
  defect_class    TEXT,
  confidence      DOUBLE PRECISION,
  model_version   TEXT        NOT NULL,
  image_ref       TEXT
);
CREATE INDEX IF NOT EXISTS inspection_results_source_ts_brin
  ON inspection_results USING brin (source_ts);

CREATE TABLE IF NOT EXISTS inspection_images (
  assembly_serial TEXT PRIMARY KEY REFERENCES inspection_results(assembly_serial),
  bytes           BYTEA NOT NULL     -- rejects only (§3.4)
);

CREATE TABLE IF NOT EXISTS ingest_gaps (
  id      BIGSERIAL   PRIMARY KEY,
  from_ts TIMESTAMPTZ NOT NULL,
  to_ts   TIMESTAMPTZ NOT NULL,
  reason  TEXT        NOT NULL
);

-- R1's reconciliation ledger: what the gateway believes it pulled per window.
CREATE TABLE IF NOT EXISTS backfill_windows (
  id            BIGSERIAL   PRIMARY KEY,
  from_ts       TIMESTAMPTZ NOT NULL,
  to_ts         TIMESTAMPTZ NOT NULL,
  stream        TEXT        NOT NULL,
  rows_returned INTEGER     NOT NULL,
  rows_written  INTEGER     NOT NULL,
  pages         INTEGER     NOT NULL,
  duration_ms   INTEGER     NOT NULL,
  UNIQUE (from_ts, to_ts, stream)
);
```

- [ ] **Step 2: Write the failing writer test**

Use `Testcontainers.PostgreSql` 4.15.0 so the test runs against real Postgres.

```csharp
// diagnostics/gateway/Gateway.Tests/PostgresWriterTests.cs
[Fact]
public async Task RawAndNormalisedAreWrittenInOneTransaction()
{
    // §5.1: one write path, derivation in one place -- both or neither.
    var writer = new PostgresWriter(_connectionString, failNormalisedForTesting: true);
    await Assert.ThrowsAsync<InvalidOperationException>(
        () => writer.WriteBatchAsync([SampleEvent("A-1")]));

    Assert.Equal(0, await CountAsync("raw_events"));      // the raw row rolled back too
    Assert.Equal(0, await CountAsync("inspection_results"));
}

[Fact]
public async Task DuplicateSignalValuesCollapseToOneRow()
{
    // Not an edge case: this fires on every page boundary of every backfill.
    var writer = new PostgresWriter(_connectionString);
    var sample = SampleDataChange("TaktTime", ts: Instant, value: 6.02);
    await writer.WriteBatchAsync([sample]);
    await writer.WriteBatchAsync([sample]);

    Assert.Equal(1, await CountAsync("signals"));
}

[Fact]
public async Task OnlyRejectsCarryAnImage()
{
    var writer = new PostgresWriter(_connectionString);
    await writer.WriteBatchAsync([SampleEvent("A-1", reject: true, image: new byte[1024])]);
    await writer.WriteBatchAsync([SampleEvent("A-2", reject: false, image: null)]);

    Assert.Equal(1, await CountAsync("inspection_images"));
}

[Fact]
public async Task OutOfOrderArrivalsAreOrderedBySourceTimestampOnRead()
{
    // §4.4: ordering by SourceTimestamp on read, never by arrival.
    var writer = new PostgresWriter(_connectionString);
    await writer.WriteBatchAsync([SampleDataChange("TaktTime", Instant.AddSeconds(12), 6.1)]);
    await writer.WriteBatchAsync([SampleDataChange("TaktTime", Instant, 6.0)]);

    var ordered = await ReadSignalsOrderedAsync("TaktTime");
    Assert.Equal([6.0, 6.1], ordered);
}
```

- [ ] **Step 3: Run it to verify it fails, then write the writer**

`WriteBatchAsync` opens one `NpgsqlTransaction`, inserts every record verbatim into `raw_events`, derives the normalised rows in the same transaction, and commits once:

```csharp
await using var tx = await conn.BeginTransactionAsync(ct);
foreach (var record in batch)
{
    await InsertRawAsync(conn, tx, record, ct);          // verbatim, append-only
    switch (record.Kind)
    {
        case "datachange":
            // ON CONFLICT (station_id, signal, source_ts) DO NOTHING
            await UpsertSignalAsync(conn, tx, record, ct);
            break;
        case "event":
            // ON CONFLICT (assembly_serial) DO NOTHING
            await UpsertInspectionAsync(conn, tx, record, ct);
            if (record.ImageBytes is { Length: > 0 })
            {
                await UpsertImageAsync(conn, tx, record, ct);
            }
            break;
    }
}
await tx.CommitAsync(ct);
```

The cost of one transaction is that replaying raw after a normalisation bug means re-running the gateway's own logic, which is why §5.1 requires a `--replay-from <timestamp>` mode; add it reading `raw_events` in `source_ts` order and re-deriving.

- [ ] **Step 4: Run the writer tests to verify they pass**

Run: `cd diagnostics/gateway && dotnet test --locked-mode --filter PostgresWriterTests`
Expected: PASS, 4 tests.

- [ ] **Step 5: Measure R4 — the image ceiling**

The live path is now complete, so R4 is measurable end to end.

```python
# measurements/run_r4.py  (sketch of the procedure; see the risk table for thresholds)
# 1. read img_p50 / img_p99 from measurements/r4-image-sizes.txt (Task 5)
# 2. drive the simulator to emit one reject at each size in a doubling ladder
#    starting at img_p99: 1x, 2x, 4x, 8x ... until delivery fails
# 3. record the failing size, the exact StatusCode, and which limit produced it
#    (MaxByteStringLength, MaxMessageSize, or asyncua's TransportLimits)
# 4. burst test: emit B=20 rejects inside one publishing interval; assert
#    inspection_images gains exactly 20 rows and overflow_count did not rise
# 5. if overflow DID rise, assert an ingest_gaps row covers exactly that interval
```

Pass: `ceiling >= 4 * img_p99` and `burst_loss == 0` at B = 20. Write `measurements/r4-results.json`.

If the ceiling crowds `img_p99`, raise `MaxByteStringLength` and `MaxMessageSize` in `GatewayOptions` **and** asyncua's `TransportLimits` together, re-measure, and record the chosen values as configuration (§10.3). `burst_loss > 0` with no overflow bit is a blocker: undetectable loss breaks §4.4's premise that gap markers make missing data distinguishable from a quiet machine.

- [ ] **Step 6: Commit**

```bash
git add diagnostics/gateway/Migrations diagnostics/gateway/Ingest/PostgresWriter.cs diagnostics/gateway/Gateway.Tests measurements/run_r4.py measurements/r4-results.json
git commit -m "feat(gateway): raw landing and normalised model in one transaction, with R4 measured"
```

---

## Task 10: `HistoryRead` backfill, windowed and reconciled

This task carries R1 and R2. Read the Pre-flight findings again before starting — F1 and F2 dictate the design.

**Files:**
- Create: `diagnostics/gateway/Opc/HistoryBackfill.cs`, `Ingest/Reconciler.cs`
- Create: `measurements/run_r1_r2.py`, `measurements/probe_history.py`
- Test: `diagnostics/gateway/Gateway.Tests/BackfillTests.cs`

**Interfaces:**
- Consumes: `UaConnection`, `LocalQueue`, `PostgresWriter`.
- Produces: `HistoryBackfill.RunAsync(from, to, ct) -> Task<BackfillReport>` where `BackfillReport` carries `RowsReturned`, `RowsWritten`, `Pages`, `Duration`, `Windows`; `Reconciler.CheckAsync(from, to) -> Task<ReconciliationResult>`.

- [ ] **Step 1: Reproduce the pre-flight findings on the pinned interpreter**

The numbers in Pre-flight were taken on Python 3.14. Confirm them on the pinned 3.13 before designing around them.

```python
# measurements/probe_history.py
"""Reproduces F1 (the 10,000 ceiling) and F2 (page-boundary duplicates)."""

import asyncio, sys, tempfile, os
from datetime import datetime, timedelta, timezone
from asyncua import ua, Server, Client
from asyncua.server.history_sql import HistorySQLite


async def probe(n: int, page: int, port: int) -> tuple[int, int]:
    tmp = tempfile.mkdtemp()
    server = Server()
    await server.init()
    server.set_endpoint(f"opc.tcp://127.0.0.1:{port}/probe")
    idx = await server.register_namespace("probe")
    obj = await server.nodes.objects.add_object(idx, "S3")
    var = await obj.add_variable(idx, "TaktTime", 6.0)
    st = HistorySQLite(os.path.join(tmp, "h.db"), max_history_data_response_size=page)
    await st.init()
    server.iserver.history_manager.set_storage(st)
    base = datetime.now(timezone.utc) - timedelta(hours=18)
    async with server:
        await server.historize_node_data_change(var, period=None, count=0)
        for i in range(n):
            await var.write_value(
                ua.DataValue(
                    ua.Variant(6.0 + i * 0.001, ua.VariantType.Double),
                    SourceTimestamp=base + timedelta(seconds=6 * i),
                )
            )
        await asyncio.sleep(2)
        async with Client(f"opc.tcp://127.0.0.1:{port}/probe") as cl:
            rows = await cl.get_node(var.nodeid).read_raw_history(
                base - timedelta(minutes=1),
                datetime.now(timezone.utc) + timedelta(days=1),
                0,
            )
    await st.stop()
    return n, len(rows)


async def main() -> None:
    for n, page, port in (
        (3000, 500, 48420),
        (10800, 1000, 48421),
        (12000, 1000, 48422),
    ):
        wrote, read = await probe(n, page, port)
        print(f"wrote={wrote:6d} page={page:5d} read={read:6d} delta={read - wrote:+d}")


asyncio.run(main())  # run with python -u: buffered output is lost if it is killed
```

Expected, from the pre-flight run: `3000/500 -> 3007` (duplicates), `10800/1000 -> 10000` and `12000/1000 -> 10000` (a hard ceiling at 10,000 independent of page size). Record what you actually get in `measurements/r1-probe.txt`.

- [ ] **Step 2: Write the failing backfill test**

```csharp
// diagnostics/gateway/Gateway.Tests/BackfillTests.cs
[Fact]
public async Task BackfillOfAKnownWindowLandsExactlyTheExpectedRows()
{
    // R1's pass condition. The historian over-returns at page boundaries and
    // silently truncates past 10,000 per traversal, so neither the raw returned
    // count nor a single unbounded read can be trusted -- only this reconciliation.
    var expected = 26 * 60 * 60 / 6;              // depth / takt
    var report = await _backfill.RunAsync(_historyStart, _historyEnd, default);
    var check = await _reconciler.CheckAsync(_historyStart, _historyEnd);

    Assert.Equal(expected, check.PlantRows);
    Assert.Equal(expected, check.PgRows);
    Assert.Empty(check.Gaps);
    Assert.True(report.RowsReturned >= expected, "duplicates are expected, losses are not");
}

[Fact]
public async Task EveryWindowStaysUnderTheHistorianCeiling()
{
    // A window that can return more than 10,000 values will be silently truncated.
    var report = await _backfill.RunAsync(_historyStart, _historyEnd, default);
    Assert.All(report.Windows, w => Assert.True(w.RowsReturned < 10_000,
        $"window {w.From:o}..{w.To:o} returned {w.RowsReturned}; shrink BackfillWindow"));
}

[Fact]
public async Task ContinuationPointsAreReleasedOnAbort()
{
    // R2 mechanic M3. Leaked continuation points exhaust the server's pool.
    using var cts = new CancellationTokenSource();
    cts.CancelAfter(TimeSpan.FromMilliseconds(50));
    await Assert.ThrowsAnyAsync<OperationCanceledException>(
        () => _backfill.RunAsync(_historyStart, _historyEnd, cts.Token));

    Assert.Equal(0, await _backfill.OutstandingContinuationPointsAsync());
}

[Fact]
public async Task BothTimestampsSurviveAndDiverge()
{
    // R2 mechanic M4, and §4.2: SourceTimestamp is simulated, ServerTimestamp wall.
    await _backfill.RunAsync(_historyStart, _historyEnd, default);
    var (source, server) = await ReadOldestTimestampsAsync();
    Assert.True(server - source > TimeSpan.FromHours(20),
        "during catch-up the two diverge sharply -- that is what a real backfill looks like");
}

[Fact]
public async Task EventBackfillCarriesImageBytes()
{
    // R2 mechanic M2: ReadEventDetails with a filter matching the custom type.
    await _backfill.RunAsync(_historyStart, _historyEnd, default);
    Assert.True(await CountAsync("inspection_images") > 0);
}
```

- [ ] **Step 3: Write the backfill**

The shape that F1 and F2 force:

```csharp
// diagnostics/gateway/Opc/HistoryBackfill.cs
//
// Never issue an unbounded traversal. asyncua silently caps a full read at 10,000
// values with no exception and no bad StatusCode, and 18 h at 6 s takt is 10,800 --
// just over it. So: fixed time windows sized well under the ceiling, one
// reconciliation per window, and the run fails loudly if a window comes back short.
//
// The SDK gives us the raw service and nothing above it. 1.5.378.176 ships no
// HistoryRead sample; HistoryClient belongs to the unreleased 2.0 line. Paging,
// decoding and continuation-point release are all ours.

public async Task<BackfillReport> RunAsync(DateTime from, DateTime to, CancellationToken ct)
{
    var windows = new List<WindowReport>();
    for (var start = from; start < to; start = start.Add(_options.BackfillWindow))
    {
        var end = Min(start.Add(_options.BackfillWindow), to);
        windows.Add(await ReadVariableWindowAsync(_space.TaktNodeId, "TaktTime", start, end, ct));
        windows.Add(await ReadVariableWindowAsync(_space.PartCountNodeId, "PartCount", start, end, ct));
        windows.Add(await ReadEventWindowAsync(_space.S3NodeId, start, end, ct));
        _progress = (start - from).TotalSeconds / (to - from).TotalSeconds;
    }
    return new BackfillReport(windows);
}

private async Task<WindowReport> ReadVariableWindowAsync(
    NodeId node, string signal, DateTime from, DateTime to, CancellationToken ct)
{
    var details = new ReadRawModifiedDetails
    {
        IsReadModified = false,
        StartTime = from,
        EndTime = to,
        // Push the limit into the request. Left at 0 the server materialises the
        // whole remaining range per page and slices in memory.
        NumValuesPerNode = (uint)_options.HistoryPageSize,
        ReturnBounds = false,
    };

    byte[]? continuationPoint = null;
    var returned = 0;
    var pages = 0;
    try
    {
        do
        {
            var nodesToRead = new HistoryReadValueIdCollection
            {
                new HistoryReadValueId { NodeId = node, ContinuationPoint = continuationPoint },
            };

            var response = await _session.HistoryReadAsync(
                requestHeader: null,
                historyReadDetails: new ExtensionObject(details),
                timestampsToReturn: TimestampsToReturn.Both,   // §4.2 needs both
                releaseContinuationPoints: false,
                nodesToRead: nodesToRead,
                ct).ConfigureAwait(false);

            var result = response.Results[0];
            ServiceResultException.ThrowIfBad(result.StatusCode);

            var data = (HistoryData)ExtensionObject.ToEncodeable(result.HistoryData);
            foreach (var dv in data.DataValues)
            {
                await _queue.EnqueueAsync(ToRecord(signal, node, dv), ct).ConfigureAwait(false);
                returned++;
            }
            continuationPoint = result.ContinuationPoint;
            pages++;
        }
        while (continuationPoint is { Length: > 0 } && !ct.IsCancellationRequested);
    }
    finally
    {
        // Release on every exit path, cancellation included, or the server's
        // continuation-point pool leaks until it refuses further reads.
        if (continuationPoint is { Length: > 0 })
        {
            await ReleaseAsync(node, continuationPoint).ConfigureAwait(false);
        }
    }
    ct.ThrowIfCancellationRequested();
    return new WindowReport(from, to, signal, returned, pages);
}
```

`ReadEventWindowAsync` is the same shape with `ReadEventDetails { StartTime, EndTime, NumValuesPerNode, Filter = BuildInspectionFilter() }`, decoding `(HistoryEvent)ExtensionObject.ToEncodeable(result.HistoryData)` and reading `.Events[i].EventFields` positionally **against the same named select-clause order Task 8 defined**. A filter that differs from the subscription's produces a different field order and silently mis-assigns every column.

`Reconciler.CheckAsync` compares three counts for a window: the plant's ledger (exposed by `simulator.status`), `backfill_windows.rows_returned`, and the actual `signals` / `inspection_results` counts in Postgres. Equality of the first and third is the pass; the second is expected to exceed both by roughly `pages - 1` per window.

- [ ] **Step 4: Run the backfill tests to verify they pass**

Run: `cd diagnostics/gateway && dotnet test --locked-mode --filter BackfillTests`
Expected: PASS, 5 tests. All four R2 mechanics are green at this point.

- [ ] **Step 5: Measure R1 and R2**

```bash
uv run --frozen --package analysis python measurements/run_r1_r2.py | tee measurements/r1-r2-results.json
```

The runner brings both stacks up at depths of 1 h, 4 h and 26 h, and records `catchup_wall`, `backfill_wall`, `page_p50`, `page_p99`, `duplicate_rate`, `linearity`, and the three reconciled counts. It also records `history_loc` (`cloc` over `Opc/HistoryBackfill.cs`) and prompts for the written list of *what the SDK does not do for you*, which is R2's actual deliverable.

Thresholds and the decision tree are in the risk table. The one that must not be waived: `pg_rows == plant_rows` exactly.

- [ ] **Step 6: Commit**

```bash
git add diagnostics/gateway/Opc/HistoryBackfill.cs diagnostics/gateway/Ingest/Reconciler.cs diagnostics/gateway/Gateway.Tests/BackfillTests.cs measurements
git commit -m "feat(gateway): windowed HistoryRead backfill with per-window reconciliation

Windows are sized under asyncua's silent 10,000-value ceiling; the upsert absorbs
the duplicate the historian returns at every page boundary. Closes R1 and R2."
```

---

## Task 11: Topology discovery

**Files:**
- Create: `diagnostics/gateway/Opc/TopologyDiscovery.cs`
- Test: `diagnostics/gateway/Gateway.Tests/TopologyTests.cs`

**Interfaces:**
- Consumes: `UaConnection.Session`.
- Produces: `TopologyDiscovery.DiscoverAsync(session, ct) -> Task<IReadOnlyList<DiscoveredStation>>` with `DiscoveredStation(Code, Name, NodeId, TaktNodeId, PartCountNodeId)`; rows written to `stations`.

- [ ] **Step 1: Write the failing test**

```csharp
[Fact]
public async Task StationsAreBrowsedNotConfigured()
{
    // §4.1: the line's topology is discovered, not configured. Nothing downstream
    // hardcodes which stations exist -- M2 adds three more with no gateway change.
    var stations = await TopologyDiscovery.DiscoverAsync(_session, default);

    Assert.Single(stations);
    Assert.Equal("S3_Inspection", stations[0].Code);
    Assert.NotNull(stations[0].TaktNodeId);
}

[Fact]
public async Task DiscoveryIsIdempotentAcrossReconnects()
{
    await TopologyDiscovery.DiscoverAsync(_session, default);
    await TopologyDiscovery.DiscoverAsync(_session, default);
    Assert.Equal(1, await CountAsync("stations"));
}
```

- [ ] **Step 2: Run it to verify it fails, then implement**

`DiscoverAsync` browses `Objects` → `Line` → `Stations`, takes each child as a station, browses its children for `TaktTime` and `PartCount`, and upserts into `stations` with `ON CONFLICT (code) DO UPDATE`. `position_in_line` is left null in M1 — it is derivable from buffer references, and buffers arrive in M2.

- [ ] **Step 3: Run the tests to verify they pass**

Run: `cd diagnostics/gateway && dotnet test --locked-mode --filter TopologyTests`
Expected: PASS, 2 tests.

- [ ] **Step 4: Commit**

```bash
git add diagnostics/gateway/Opc/TopologyDiscovery.cs diagnostics/gateway/Gateway.Tests/TopologyTests.cs
git commit -m "feat(gateway): discover station topology by browsing the address space"
```

---

## Task 12: The analysis contract and its two endpoints

§13 says "one analysis endpoint · one tool". M1 ships one *tool* and a second endpoint that exists only to resolve citations, because §6.5 verifies every cited id against the database and §7.2 requires that clicking a citation opens the underlying data. A citation you cannot open is, in the spec's own words, barely a citation.

**Files:**
- Create: `contracts/analysis.openapi.yaml`, `diagnostics/analysis/src/analysis/{app,db,routes_inspection,routes_parts}.py`
- Test: `diagnostics/analysis/tests/test_contract.py`, `test_endpoints.py`

**Interfaces:**
- Consumes: the Postgres schema (Task 9).
- Produces:
  - `GET /inspection/stats?from&to&group_by=defect_class` → `{window:{from,to}, total, rejects, by_defect_class:[{defect_class,count}], sample_serials:[...], coverage:{gaps:[{from_ts,to_ts,reason}]}}`
  - `GET /parts/{serial}` → `{assembly_serial, source_ts, station, result, defect_class, confidence, model_version, image_url}`
  - `GET /parts/{serial}/image` → `image/png`
  - `contracts/analysis.openapi.yaml` as the single source of truth, from which `diagnostics/ui/src/generated/analysis.ts` is generated

- [ ] **Step 1: Write the failing contract test**

```python
# diagnostics/analysis/tests/test_contract.py
import yaml
from fastapi.testclient import TestClient

from analysis.app import app

CONTRACT = "../../contracts/analysis.openapi.yaml"


def test_the_served_schema_matches_the_committed_contract() -> None:
    """§10.1: contracts/ is the single source of truth. §7.3: TypeScript types are
    generated from it, so the frontend cannot drift from the API."""
    committed = yaml.safe_load(open(CONTRACT))
    served = TestClient(app).get("/openapi.json").json()
    assert set(committed["paths"]) == set(served["paths"])
    for path, spec in committed["paths"].items():
        assert set(spec) == set(served["paths"][path])


def test_coverage_is_part_of_every_stats_response() -> None:
    """§5.3: /coverage exists so the agent can ask whether it has data before
    answering. In M1 it is folded into the one tool rather than split out, so the
    coverage check of §6.1 step 3 cannot be skipped by forgetting to call it."""
    schema = yaml.safe_load(open(CONTRACT))
    stats = schema["paths"]["/inspection/stats"]["get"]["responses"]["200"]
    props = stats["content"]["application/json"]["schema"]["properties"]
    assert "coverage" in props
```

- [ ] **Step 2: Write the failing endpoint test**

```python
# diagnostics/analysis/tests/test_endpoints.py
def test_stats_window_is_closed_and_counts_are_exact(client, seeded_db) -> None:
    r = client.get(
        "/inspection/stats",
        params={"from": "2026-09-12T01:00:00Z", "to": "2026-09-12T02:00:00Z"},
    )
    body = r.json()
    assert body["total"] == 600  # one hour at 6 s takt
    assert body["rejects"] == 30  # seeded at 5 %
    assert sum(d["count"] for d in body["by_defect_class"]) == 30


def test_stats_reports_gaps_rather_than_hiding_them(client, seeded_db_with_gap) -> None:
    """§4.4: without gap markers, missing data is indistinguishable from a quiet
    machine, and the agent will confidently describe a stop that was a blackout."""
    r = client.get(
        "/inspection/stats",
        params={"from": "2026-09-12T01:00:00Z", "to": "2026-09-12T02:00:00Z"},
    )
    assert r.json()["coverage"]["gaps"]


def test_part_lookup_reads_the_per_part_record_directly(client, seeded_db) -> None:
    """§3.4a: the per-part record is authoritative. It is never reconstructed by
    joining the time series on 'when was this part at S3'."""
    r = client.get("/parts/A-00000007")
    assert r.status_code == 200
    assert r.json()["assembly_serial"] == "A-00000007"


def test_unknown_serial_is_404_not_an_empty_object(client, seeded_db) -> None:
    """Citation verification (§6.5) depends on this distinction."""
    assert client.get("/parts/A-99999999").status_code == 404


def test_only_rejects_expose_an_image_url(client, seeded_db) -> None:
    assert client.get("/parts/A-00000007").json()["image_url"] is not None
    assert client.get("/parts/A-00000006").json()["image_url"] is None
```

- [ ] **Step 3: Run both to verify they fail, then implement**

`routes_inspection.py` issues one query grouped by `defect_class` over `inspection_results` filtered on `source_ts`, plus one over `ingest_gaps` overlapping the window, and returns up to five reject serials as `sample_serials` so the agent has something concrete to cite. `routes_parts.py` selects a single row by primary key and joins `inspection_images` for the image URL — **no time-range join anywhere**, which is what Task 12's third test guards.

Closed-window caching (§5.3) is **not** implemented in M1; it is an analysis concern that pays off under concurrency, and M1 has one user. Recorded in *Spec ambiguities*.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd diagnostics && uv run --frozen --package analysis pytest analysis/tests -v`
Expected: PASS, 7 tests.

- [ ] **Step 5: Generate the TypeScript types**

```bash
cd diagnostics/ui && pnpm dlx openapi-typescript ../../contracts/analysis.openapi.yaml -o src/generated/analysis.ts
```

Add a `make contract-check` target that regenerates into a temp file and diffs, so a contract change that was not propagated fails CI rather than drifting.

- [ ] **Step 6: Commit**

```bash
git add contracts diagnostics/analysis diagnostics/ui/src/generated
git commit -m "feat(analysis): OpenAPI contract, inspection stats and the citation resolver"
```

---

## Task 13: The agent — one tool, a structured answer, verified citations

**Files:**
- Create: `diagnostics/agent/src/agent/{app,pipeline,tools,citations,provider,answer}.py`
- Create: `contracts/answer.schema.json`
- Test: `diagnostics/agent/tests/test_pipeline.py`, `test_citations.py`

**Interfaces:**
- Consumes: the analysis endpoints (Task 12).
- Produces:
  - `Answer` (Pydantic) mirroring §6.3: `findings[]` each with `statement`, `basis`, `citations[]`, optional `evidence_strength`; `answer_markdown`; `method{sops_used, tools_called, budget_used}`; `caveats[]`; optional `contradiction`
  - `Citation` tagged union; M1 emits only `{kind:"part", id:<serial>}`
  - `async run(question, session_id) -> Answer`
  - `POST /ask` streaming progress events then the answer object

- [ ] **Step 1: Write the failing citation test**

```python
# diagnostics/agent/tests/test_citations.py
import pytest

from agent.answer import Answer, Citation, Finding
from agent.citations import VerificationResult, verify


@pytest.mark.asyncio
async def test_a_citation_that_does_not_resolve_is_rejected(fake_analysis) -> None:
    """§6.5: every cited id is resolved against the database before the answer ships."""
    answer = Answer(
        findings=[
            Finding(
                statement="Part A-99999999 was rejected.",
                basis="measured",
                citations=[Citation(kind="part", id="A-99999999")],
            )
        ],
        answer_markdown="...",
        method={"sops_used": [], "tools_called": [], "budget_used": 1},
        caveats=[],
    )
    result = await verify(answer, fake_analysis)
    assert result.failed_ids == ["A-99999999"]


@pytest.mark.asyncio
async def test_unresolvable_claims_are_removed_and_the_answer_says_so(
    fake_analysis,
) -> None:
    """§6.5: after one failed retry the offending claims are removed and the answer
    ships with a visible note. The failure is logged so its frequency is measurable."""
    answer = Answer(
        findings=[
            Finding(
                statement="A-00000007 was rejected for a gap.",
                basis="measured",
                citations=[Citation(kind="part", id="A-00000007")],
            ),
            Finding(
                statement="A-99999999 was rejected too.",
                basis="measured",
                citations=[Citation(kind="part", id="A-99999999")],
            ),
        ],
        answer_markdown="...",
        method={"sops_used": [], "tools_called": [], "budget_used": 1},
        caveats=[],
    )
    stripped = await VerificationResult.strip(answer, fake_analysis)
    assert len(stripped.findings) == 1
    assert any("could not be verified" in c for c in stripped.caveats)


def test_m1_never_claims_a_hypothesis() -> None:
    """§6.3: basis 'hypothesis' means it required interpretation from the knowledge
    base. M1 has no knowledge base, so a hypothesis would be an invention.
    §6.3 also requires evidence_strength whenever basis is hypothesis."""
    with pytest.raises(ValueError, match="knowledge base"):
        Finding(
            statement="Carrier 7 is worn.",
            basis="hypothesis",
            citations=[],
            evidence_strength="92 %, n=214",
        )


def test_evidence_strength_is_required_for_a_hypothesis() -> None:
    from agent.answer import validate_basis

    with pytest.raises(ValueError, match="evidence_strength"):
        validate_basis(
            basis="hypothesis", evidence_strength=None, allow_hypothesis=True
        )
```

- [ ] **Step 2: Write the failing pipeline test**

```python
# diagnostics/agent/tests/test_pipeline.py
@pytest.mark.asyncio
async def test_time_windows_are_computed_in_code_not_by_the_model(monkeypatch) -> None:
    """§6.1 step 2: the model reads 'last hour' out of the sentence; code does the
    calendar maths. Date arithmetic is what models are unreliable at."""
    from agent.pipeline import resolve_window

    window = resolve_window(
        "last hour", now=datetime(2026, 9, 12, 14, 30, tzinfo=timezone.utc)
    )
    assert window.start == datetime(2026, 9, 12, 13, 30, tzinfo=timezone.utc)
    assert window.end == datetime(2026, 9, 12, 14, 30, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_a_window_with_gaps_forces_a_caveat(
    fake_analysis_with_gap, fake_model
) -> None:
    """§6.1 step 3 is a guard, not a choice: if the window has gaps, that fact
    enters the context and the answer must mention it."""
    answer = await run("how many rejects in the last hour?", session_id="s1")
    assert any("incomplete" in c.lower() or "gap" in c.lower() for c in answer.caveats)


@pytest.mark.asyncio
async def test_an_empty_window_says_so_instead_of_inventing(
    fake_analysis_empty, fake_model
) -> None:
    """§1's agent authenticity proof: it says 'I have no data for that window'
    instead of inventing an answer."""
    answer = await run("how many rejects last hour?", session_id="s1")
    assert answer.findings == [] or all(f.basis == "measured" for f in answer.findings)
    assert any("no data" in c.lower() for c in answer.caveats)


@pytest.mark.asyncio
async def test_out_of_scope_requests_are_declined(fake_model) -> None:
    """§6.1: anything asking the system to act on the plant. It is read-only (§4.5)."""
    answer = await run("increase the joining force at S2", session_id="s1")
    assert "read-only" in answer.answer_markdown.lower()
    assert answer.findings == []
```

- [ ] **Step 3: Run both to verify they fail, then implement the provider layer**

```python
# diagnostics/agent/src/agent/provider.py
"""The canonical surface of §6.9, narrowed to what M1 uses. Two adapters are the
M4 shape; M1 ships the Anthropic one behind the same interface so M4 adds a
sibling rather than a rewrite."""

from __future__ import annotations

import anthropic

MODEL = "claude-sonnet-5"  # §6.9's default


class AnthropicProvider:
    def __init__(self) -> None:
        self._client = anthropic.AsyncAnthropic()

    async def call(self, system: str, messages: list[dict], tools: list[dict]):
        return await self._client.messages.create(
            model=MODEL,
            max_tokens=16000,
            system=system,
            messages=messages,
            tools=tools,
            # Sonnet 5: adaptive is the only on-mode. budget_tokens, temperature,
            # top_p, top_k and assistant prefill all return 400 on this model.
            thinking={"type": "adaptive"},
            output_config={"effort": "medium"},
        )

    async def parse(self, system: str, messages: list[dict], schema: type):
        """Structured final output. §6.3: nothing is parsed out of prose."""
        return await self._client.messages.parse(
            model=MODEL,
            max_tokens=16000,
            system=system,
            messages=messages,
            thinking={"type": "adaptive"},
            output_format=schema,
        )
```

The pipeline runs §6.1's stages, narrowed: classify (a fixed set, defaulting to `statistics`), extract the time phrase and resolve it **in code**, coverage check, one tool call, citation verification with exactly one retry, compose, deliver. `method.sops_used` is `[]` and stays `[]` until M4. Every stage emits a progress event over SSE, which is §6.10's streaming progress and the best demo asset in the project.

Budget: `max_tool_calls = 4`. Exhaustion produces a partial answer stating what it could not finish, never a silently truncated one (§6.8).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd diagnostics && uv run --frozen --package agent pytest agent/tests -v`
Expected: PASS, 8 tests. The model is faked in all of them — no test in this suite spends money.

- [ ] **Step 5: Commit**

```bash
git add diagnostics/agent contracts/answer.schema.json
git commit -m "feat(agent): staged pipeline with one tool, structured findings and verified citations"
```

---

## Task 14: The chat box and the citation that opens

**Files:**
- Create: `diagnostics/ui/` (Vite + React + TypeScript)
- Test: `diagnostics/ui/src/__tests__/citation.test.tsx`

**Interfaces:**
- Consumes: `POST /ask` (SSE) from Task 13; `GET /parts/{serial}` from Task 12; generated types.
- Produces: a single page with a question box, streamed progress lines, the composed answer, citation chips, and an evidence panel.

- [ ] **Step 1: Scaffold with pinned tooling**

```bash
cd diagnostics/ui
corepack enable && corepack prepare pnpm@11.1.3 --activate
pnpm create vite . --template react-ts
pnpm install --frozen-lockfile
```

Add `"engines": {"node": "22"}` to `package.json`; `.nvmrc` is at the repository root.

- [ ] **Step 2: Write the failing citation test**

```tsx
// diagnostics/ui/src/__tests__/citation.test.tsx
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { CitationChip } from "../CitationChip";

test("clicking a citation opens the underlying row", async () => {
  // §7.2: a citation you cannot open is barely a citation.
  render(<CitationChip citation={{ kind: "part", id: "A-00000007" }} />);
  fireEvent.click(screen.getByText(/A-00000007/));
  await waitFor(() => expect(screen.getByTestId("evidence-panel")).toBeInTheDocument());
  expect(screen.getByText(/gap/)).toBeInTheDocument();
  expect(screen.getByRole("img")).toBeInTheDocument();
});

test("each citation kind has its own renderer", () => {
  // §7.3: adding a citation type means adding a renderer, nothing more.
  const { RENDERERS } = require("../CitationChip");
  expect(Object.keys(RENDERERS)).toContain("part");
});
```

- [ ] **Step 3: Run it to verify it fails, then implement**

`Chat.tsx` posts the question, renders each SSE progress line as it arrives (*reading inspection results… checking coverage…*), then the composed `answer_markdown` with citation chips inlined. `CitationChip.tsx` holds a `RENDERERS` map keyed by citation `kind`; M1 registers `part`. `EvidencePanel.tsx` fetches `/parts/{serial}` and renders the row plus the image.

The reasoning trace (§7.2) is rendered collapsed under every answer from `method`: tools called with arguments and timings, budget consumed, `sops_used` (empty in M1). It is stored for the audit trail anyway, so surfacing it is nearly free.

Visual design is deliberately minimal. §15 defers the frontend design language until after M4 on the grounds that a layout cannot be designed for content whose shape has not been seen — M1 is where that shape first becomes visible, and it should be recorded, not decorated.

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd diagnostics/ui && pnpm test`
Expected: PASS, 2 tests.

- [ ] **Step 5: Commit**

```bash
git add diagnostics/ui
git commit -m "feat(ui): chat box with streamed progress and a citation that opens its evidence"
```

---

## Task 15: The authenticity proofs, as executable tests

§1 sets the standard: *every link holds what it claims, and each has a test that would fail if the link were a facade.* Four of the nine are reachable in M1. The other five need later milestones and are named here so the gap is explicit.

**Files:**
- Create: `diagnostics/gateway/Gateway.Tests/AuthenticityTests.cs`, `measurements/authenticity/`
- Test: the file is the test.

- [ ] **Step 1: Write the four proofs**

```csharp
[Fact]
[Trait("Category", "Authenticity")]
public async Task ForeignClientCanBrowseTheAddressSpace()
{
    // §1 row 1. The gateway is itself a foreign client to asyncua, but a second,
    // independent UA-.NETStandard session proves it is not gateway-specific glue.
    await using var stranger = new UaConnection(GatewayOptions.Default(), _telemetry);
    var session = await stranger.ConnectAsync(default);
    var children = await BrowseAsync(session, "Objects/Line/Stations/S3_Inspection");
    Assert.Contains("TaktTime", children);
}

[Fact]
[Trait("Category", "Authenticity")]
public async Task GatewayKeepsBufferingWhilePostgresIsDownAndLosesNothing()
{
    // §1 row 2: stop Postgres -> the gateway keeps buffering -> restart -> no gaps.
    var before = await _reconciler.CheckAsync(_windowStart, _windowEnd);
    await _postgres.StopAsync();
    await Task.Delay(TimeSpan.FromSeconds(60));         // ten takts
    Assert.True(await _queue.DepthAsync() > 0, "the queue is not filling");
    await _postgres.StartAsync();
    await WaitForQueueDrainAsync(TimeSpan.FromMinutes(2));

    var after = await _reconciler.CheckAsync(_windowStart, _windowEnd);
    Assert.Equal(after.PlantRows, after.PgRows);
    Assert.Empty(after.Gaps);
}

[Fact]
[Trait("Category", "Authenticity")]
public async Task HistoryReadClosesAnUpstreamOutage()
{
    // §1 row 3: stop the plant stack -> reconnect -> HistoryRead closes the outage.
    await _plant.StopAsync();
    await Task.Delay(TimeSpan.FromSeconds(90));
    await _plant.StartAsync();
    await WaitForStateAsync("live", TimeSpan.FromMinutes(5));

    var check = await _reconciler.CheckAsync(_outageStart, _outageEnd);
    Assert.Equal(check.PlantRows, check.PgRows);
}

[Fact]
[Trait("Category", "Authenticity")]
public async Task DiagnosticsAnswersWithThePlantStackShutDown()
{
    // §1 row 4, and §2.1's hard requirement. This is the proof that decides whether
    // the two-stack split is real or decorative.
    await _plant.StopAsync();

    var answer = await AskAsync("how many parts were rejected in the last hour?");
    Assert.NotEmpty(answer.Findings);
    Assert.All(answer.Findings, f => Assert.Equal("measured", f.Basis));
}

[Fact]
[Trait("Category", "Authenticity")]
public async Task ExactlyTwoContainersJoinFieldNet()
{
    // §2.1: a fourth container on field-net means the architecture has been
    // violated, and docker network inspect shows it.
    var members = await DockerNetworkMembersAsync("field-net");
    Assert.Equal(new[] { "edge-gateway", "line-simulator" }, members.Order());
}
```

- [ ] **Step 2: Run them**

Run: `cd diagnostics/gateway && dotnet test --locked-mode --filter Category=Authenticity`
Expected: PASS, 5 tests. These are slow — they stop and start containers. Keep them out of the default `make test` and give them `make test-authenticity`.

- [ ] **Step 3: Record the five proofs M1 cannot yet make**

Write `measurements/authenticity/README.md` naming them and the milestone each waits on: analysis against ground truth (M2 + M3), the agent refusing to invent (M4 — M1 tests the empty-window case only, not the full behaviour), an external MCP client reaching the same tools (M4), unauthenticated requests returning 401 (M5), and containment scored against ground truth (M7).

- [ ] **Step 4: Commit**

```bash
git add diagnostics/gateway/Gateway.Tests/AuthenticityTests.cs measurements/authenticity
git commit -m "test: four of §1's authenticity proofs as executable tests"
```

---

## Task 16: The demo, and the README that admits what this is

**Files:**
- Modify: `Makefile`
- Create: `README.md`, `docs/superpowers/measurements/.gitkeep`

- [ ] **Step 1: Write the demo target**

```makefile
.PHONY: m1-demo browse verify-no-gaps m1-report test-authenticity

m1-demo: preflight
	@echo "== 1. plant: boot, build history, go live"
	docker compose -f plant/compose.yml up -d --build
	@docker compose -f plant/compose.yml logs -f line-simulator | sed -n '/phase=live/q;p'
	@echo "== 2. a foreign client browses the address space"
	$(MAKE) browse
	@echo "== 3. diagnostics: connect, backfill, go live"
	docker compose -f diagnostics/compose.yml up -d --build
	@until curl -sf localhost:8080/status | grep -q '"State":"live"'; do \
	    curl -s localhost:8080/status | jq -c '{State,BackfillProgress,QueueDepth,RowsWritten}'; sleep 2; done
	@echo "== 4. ask a question at http://localhost:5173"
	@echo "   try: how many parts were rejected in the last hour, and what were the defects?"
	@echo "== 5. downstream outage"; $(MAKE) demo-db-outage
	@echo "== 6. upstream outage"; $(MAKE) demo-plant-outage
	@echo "== 7. the numbers"; $(MAKE) m1-report

verify-no-gaps:
	@curl -sf localhost:8080/reconcile | jq -e '.PlantRows == .PgRows and (.Gaps | length) == 0' \
	  && echo "reconciled: plant == postgres, no gaps" \
	  || { echo "RECONCILIATION FAILED"; exit 1; }
```

`demo-plant-outage` stops the plant stack, **asks the question again while it is down** and prints the answer, then restarts and waits for `live`. That pause is the point of the whole architecture, so the demo stops and shows it rather than logging past it.

- [ ] **Step 2: Write the README**

It covers architecture, the decisions with their rejected alternatives, the known limits, the security posture in plain words, and an open account of the AI-assisted development process (§14). It states, in these words, that the system is **not production-ready**. It lists the host `/etc/hosts` prerequisite, and it carries the R1–R4 results table.

- [ ] **Step 3: Run the demo end to end from a clean checkout**

```bash
git clone <repo> /tmp/m1-check && cd /tmp/m1-check && make m1-demo
```

Expected: every step completes; step 6 answers with the plant down.

- [ ] **Step 4: Commit**

```bash
git add Makefile README.md
git commit -m "docs: M1 demo sequence and a README that says what this is not"
```

---

## Task 17: The measurement report, and the spec numbers M1 was supposed to produce

§15 states that the catch-up speed, history depth and takt are *"set from the M1 measurement, not guessed"*. This task is where that happens. M1 is not finished until the spec carries the real numbers.

**Files:**
- Create: `docs/superpowers/measurements/2026-09-12-m1-boundary-risks.md`, `measurements/report.py`
- Modify: `docs/superpowers/specs/2026-09-12-machine-diagnostics-assistant-design.md` (§3.2, §12, §15)

- [ ] **Step 1: Aggregate every measurement into one report**

`measurements/report.py` reads `r1-r2-results.json`, `r3-matrix.json`, `r4-results.json`, `r1-probe.txt` and `r4-image-sizes.txt` and renders a table per risk: measured value, threshold, pass or fail, and for every deviation the decision taken and why. It fails with a non-zero exit if any risk has neither a pass nor a recorded, justified deviation — so "we never got round to it" cannot pass silently.

- [ ] **Step 2: Write the report**

Structure: one section per risk, each carrying what was measured, the numbers, the verdict, and what changed as a result. Then a section for what M1 learned that the spec did not anticipate, which is where F1–F5 and anything new belongs.

- [ ] **Step 3: Update §3.2 with the measured clock numbers**

Replace the guessed figures with the measured ones. At minimum:
- **History depth** — 18 h is wrong for its stated purpose. Replace with the value Task 3's year sweep produced (26 h at the time of writing) and replace the justification sentence, which currently claims 18 h puts the previous night shift fully inside history. It does so only for a boot between roughly 06:00 and 16:00.
- **Catch-up speed** — keep 600× if `catchup_wall` met its threshold; otherwise the achieved value. Note that catch-up duration is `depth / (speed − 1)`, so changing the depth changes the boot time.
- **Takt** — keep 6 s if R1 passed at that volume; if the historian ceiling forced a different row count per window, record what changed instead.

- [ ] **Step 4: Update §12 with the measured risk outcomes**

Each of the four rows gains its result. Add a fifth row for whatever M1 found that §12 did not predict — on the pre-flight evidence, the historian's silent 10,000-value ceiling is the leading candidate, and it belongs in the risk table because M2 multiplies the signal count by roughly ten.

- [ ] **Step 5: Close the §15 open decisions M1 owns**

Strike *"Exact catch-up speed, history depth and takt — set from the M1 measurement, not guessed"* and replace it with the values and a pointer to the report. Leave the chart library, the significance test, scenario 6 and the MCP revision open — they belong to later milestones.

- [ ] **Step 6: Commit the spec update**

```bash
git add docs/superpowers/measurements docs/superpowers/specs measurements/report.py
git commit -m "docs(spec): set the clock numbers from M1's measurement

§3.2's history depth, catch-up speed and takt were placeholders pending M1.
§15 asked for them to be measured rather than guessed; this is the measurement.
Also records the boundary-risk outcomes in §12."
```

- [ ] **Step 7: Confirm M1 is done**

```bash
make test && make test-authenticity && make m1-report && make m1-demo
git log --oneline -1 docs/superpowers/specs/
```

All green, the report shows a verdict for every risk, and the spec commit exists.

---

## Spec ambiguities — a bug report

Sixteen items the spec leaves open, ranked by how much damage guessing would do. Each names where the plan guessed and what the guess was, so a decision can override it cheaply. Nothing here is a criticism of the spec's direction; these are the seams that only show up when you try to build the thing.

### Would change the design if answered differently

**A1 — §3.2's 18 h does not do what §3.2 says it does.** *"The 18 h default exists so the previous night shift lies fully inside history at startup."* Night is 22:00–06:00 Europe/Berlin, and the most recent **completed** night shift at 21:59 started 23 h 59 min earlier — nearly 25 h across the autumn DST night, which runs nine hours. 18 h therefore holds only for a boot between roughly 06:00 and 16:00. Outside that window the flagship question has partial data, which is worse than none because nothing says so. *Plan assumes:* 26 h, derived by a year-long sweep in Task 3, with the real number written back into §3.2 in Task 17. *Alternative worth considering:* compute the depth from the boot time rather than fixing it, which is cheaper at boot but makes runs non-comparable.

**A2 — the citation vocabulary has no kind for an aggregate.** §7.3 fixes the citation kinds. None of them fits *"600 parts, 30 rejected, in this window"*. `{kind:"pattern"}` is the nearest and carries significance semantics — observed share, expected share, effect size — that a plain count does not have and that §5.5 is careful to reserve for real statistical claims. Yet §6.1 lists `statistics` as a question type and §8.1 expects statistics answers to be evidenced. *Plan assumes:* M1 cites `{kind:"part", id}` for a named example reject and does not cite the aggregate at all, which is honest but leaves the headline number uncited. *Needs:* either a new `{kind:"stats", window}` or an explicit ruling that aggregates are evidenced by the trace rather than by a citation.

**A3 — "one analysis endpoint" cannot survive citation verification.** §13's M1 line says one endpoint and one tool. But §6.5 resolves every cited id against the database and §7.2 requires clicking a citation to open the underlying data — so a citation resolver is needed alongside the tool. *Plan assumes:* one tool, two endpoints, and reads §13 as constraining tools. *Alternative:* drop citations from M1 entirely, which would remove the one thing that makes M1's answer trustworthy.

**A4 — the historian's retention clock and the plant's history clock are different clocks.** §3.2 has the simulator writing history with simulated `SourceTimestamp`s up to the history depth in the past. asyncua's historian deletes rows where `SourceTimestamp < now() − period` using the **real** clock. Any retention period shorter than the history depth silently erases history as it is written. The spec never says which clock retention runs on, because from outside they look like the same thing. *Plan assumes:* `period=None, count=0`, with the reasoning in a comment. *Needs:* a ruling once M2 adds ten times the signals and unbounded retention stops being free.

### Would change a milestone boundary

**A5 — §13's M1 line does not mention the inspection service, but requires its output.** "One event with an image" plus §3.4's insistence that the vision system is a separate box cannot both be satisfied by a simulator that classifies its own images. *Plan assumes:* a minimal inspection service ships in M1, with `SimulatedClassifier` and the truth side channel but no false-accept/false-reject rates.

**A6 — false-accept and false-reject rates are assigned to two places.** §3.4 makes them a property of the classifier ("configurable false-accept and false-reject rates, so the confidence field carries information"); §3.5 makes them part of the noise floor, which is M2. *Plan assumes:* neither in M1; the classifier reports truth faithfully. *Needs:* a ruling on whether the classifier owns them or the noise model does, because that decides whether M1's confidence field carries information or merely exists.

**A7 — topology discovery is unplaced.** §4.1 makes it a gateway connect-path behaviour ("the line's topology is discovered, not configured"); §13 never assigns it to a milestone. *Plan assumes:* M1, minimally, because the alternative is hardcoding `S3` and deleting it in M2.

**A8 — closed-window caching is unplaced.** §5.3 describes it as what makes concurrent users nearly free. §13 never assigns it. *Plan assumes:* M3, on the grounds that M1 has one user.

**A9 — PackML's `State` and `StateReason` in M1.** §4.1's address space gives every station both; §13 puts PackML in M2. A station exposing a `State` node that never leaves `Execute` is exactly the kind of thing §13's "nothing in it is faked" rules out. *Plan assumes:* absent in M1, added with the state machine in M2. *Cost:* the gateway's normalised `state_changes` table stays empty until M2, and M2 must add the subscription rather than just the producer.

### Would change an interface

**A10 — deadbands are never said to be per-signal.** §5.1 gives the gateway deadbands; §10.3 makes them configuration. Neither says the configuration is per-signal. Applying one absolute deadband across S3's two signals would put a deadband on `PartCount`, which silently loses parts — the failure looks exactly like a slow line. *Plan assumes:* per-signal, with `PartCount` at no deadband and `DiscardOldest=false`.

**A11 — the event analogue of `SourceTimestamp` is unnamed.** §4.2 is precise for variables: `SourceTimestamp` is simulated, `ServerTimestamp` is wall clock, analysis always uses the former. Events have no `SourceTimestamp`; they have a `Time` field. The spec never states the mapping, though §5.2's `inspection_results.source_ts` clearly expects one. *Plan assumes:* the event's `Time` field is simulated time and lands in `source_ts`.

**A12 — §12 says "the first four all live at the boundary" and there are five.** The certificate-SAN row is a boundary risk, is explicitly assigned to M1, and interacts with the endpoint-URL row so tightly that §4.6 says they are one puzzle. Counting it as a fifth boundary risk would match the milestone plan. *Plan assumes:* R1–R4 are the four measured risks and the SAN problem is folded into R3's matrix, which is where it actually shows up.

### Housekeeping

**A13 — §14's "no manual steps" versus the host `/etc/hosts` entry.** §1's first authenticity proof needs UaExpert, which runs on the host. The host is not on Docker's DNS, so reaching the server by the one name the certificate carries needs a hosts entry, which `docker compose` cannot create. *Plan assumes:* it is a workstation prerequisite like installing Docker, checked by `make preflight` and documented in the README. Task 6 measures whether `opc.tcp://localhost:4840/plant` works anyway, which would retire the step.

**A14 — §10.1 has no home for measurement artifacts.** `harness/` is the referee for model evaluation (§8), which is a different thing from boundary-risk measurement. *Plan assumes:* a top-level `measurements/` for the runners and `docs/superpowers/measurements/` for the reports.

**A15 — §10.1 says `docs/specs/`; the repository has `docs/superpowers/specs/`.** Trivial, but §10.1 is presented as the layout and it is already wrong on disk.

**A16 — M1's reject rate is unspecified.** §3.5's 1.5 % is the noise floor, which is M2. At 1.5 % an 18 h M1 run yields roughly 160 images, which is thin for R4's ceiling search and burst test. *Plan assumes:* 5 %, declared as a measurement knob rather than a physical claim, reverting to the noise floor in M2.

### Resolved, not open

**§10.7 asked M1 to confirm `asyncua` supports Python 3.13 rather than assume it.** Confirmed twice: `asyncua` 2.0.1 declares `requires-python >=3.10`, and a uv workspace pinned to 3.13 resolves and imports it (`asyncua 2.0.1 on 3.13.13`). Task 1 keeps this as a standing test rather than a one-off check.

---

## Self-review

Checked against the spec with fresh eyes.

**Spec coverage for M1.** §13's M1 line decomposes to: one station (Task 4), two signals (Tasks 4, 8), one event with an image (Tasks 4, 5), OPC UA across two stacks (Tasks 6, 7), gateway with queue (Task 8) and backfill (Task 10), Postgres raw + clean (Task 9), one analysis endpoint (Task 12), one tool (Task 13), a chat box answering one question with one real citation (Tasks 13, 14). §4.6's transport signing is Tasks 2, 6 and 7. §12's four risks are measured in Tasks 6, 7, 9 and 10 and reported in Task 17. §15's number-setting obligation is Task 17. Four of §1's nine authenticity proofs are Task 15, with the other five named and deferred.

**Deliberately not covered, and why.** Everything in §§3.1–3.6 beyond one station, all of §5.4–5.5, §6.2–6.8, §7.1, §7.4, §8, §9 and §10.5 belong to M2 and later. §5.3's caching and §6.9's second provider adapter are M1-shaped but not M1-necessary; both are recorded above.

**Type consistency.** `PartOutcome` (Task 4) is produced by `InspectionClient.produce` (Task 5) and consumed by `generate_history`/`run_live` (Task 4). `IngestRecord` (Task 8) is produced by both the subscription (Task 8) and the backfill (Task 10) and consumed by `PostgresWriter` (Task 9). `Citation{kind,id}` (Task 13) is emitted by the agent, verified against `GET /parts/{serial}` (Task 12) and rendered by `RENDERERS["part"]` (Task 14). The event select-clause order is defined once in Task 8 and reused verbatim in Task 10 — the plan flags that a divergence there silently mis-assigns every field, which is the single most likely way to get a green test suite and wrong data.

**Known weakness in this plan.** Tasks 12 through 14 are specified more thinly than Tasks 1 through 11. That is deliberate and matches §13's shape — M1 is thin everywhere except the boundary — but it means a reviewer should expect more design discussion during those three tasks than the step counts suggest.
