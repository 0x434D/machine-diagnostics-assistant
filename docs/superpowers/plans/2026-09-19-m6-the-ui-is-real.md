# M6 — The UI is real

**Goal:** The system stops being demonstrable only by a green test. A person opens a browser,
logs in, asks a question, and can open every claim the answer makes — the stop it names, the
part it blames, the SOP it followed, the chart it drew. §7.2's application exists, §7.3's every
citation kind resolves, §7.4's charts are citations rather than decoration.

**Spec:** §7 in full, §10.5's login, §15's frontend-design paragraph and its banked colour idea.

**Human ruling of 2026-09-19:** the development issuer becomes a small HTTP service in
`diag-net` so the login is an actual login. Zitadel replaces it by configuration; it does not
replace it in this milestone.

---

## The thing this milestone is actually about

A user interface is where fabrication hides best. §7.4 says it plainly: *a wrong chart reads
far more authoritatively than a wrong sentence.* Every earlier milestone could be checked by
reading a test; this one produces pixels, and pixels are believed.

So M6's central claim is not "the screens exist". It is: **nothing on screen is drawn from
anything the model typed.** A chart references a tool call by id and renders that call's
verified result. A citation chip resolves to a real endpoint and shows what came back. A
number with no endpoint behind it does not get rendered — it gets reported as missing.

That claim is testable, and Task 9 is where it stops being a slogan.

---

## Two decisions this plan closes

**§15's open chart-library decision** — *"Recharts / visx / ECharts, deferred to M5"* — was
not taken at M5. It is taken here, and none of the three wins: the fixed vocabulary renders
through **Vega-Lite**, the same declarative form §7.4 already mandates for the free-form
fallback. One dependency instead of two, one rendering path to test instead of two, and the
fallback stops being the least-exercised code in the frontend by sharing the path the six
built-in types use every day.

**The contract gains `GET /alarms/{id}`.** §7.3 names it as the resolution target for
`{kind: "alarm", id}`; `contracts/analysis.openapi.yaml` has only the `/alarms` collection.
That is the contract being incomplete against the spec, not the contract being changed on a
whim — which is the distinction CLAUDE.md's "ask before touching contracts/" exists to protect.

---

## Global Constraints

- **Nothing on screen comes from model-typed values.** Chart specifications reference a
  `tool_call_id`; the renderer reads that call's stored result. A specification carrying inline
  data is rejected by the type, not by a reviewer's attention.
- **TypeScript types are generated from the OpenAPI contract** (§7.3). The frontend cannot
  drift from the API, and a citation kind added to `answer.schema.json` must fail to compile
  until it has a renderer — the registry in `CitationChip.tsx` already works this way. Keep it.
- **The two frontends share design tokens and nothing else** (§7). Tokens are copied into each
  stack, never imported across the boundary: a shared npm package spanning `plant/` and
  `diagnostics/` is a build-time coupling between two stacks whose whole point is that they
  have none.
- **Colour is never the only channel** (§15, ISA-101). Every state encoded by colour carries a
  second encoding — shape, label, pattern or position. This is an accessibility requirement and
  a legibility one, and it is checked in Task 9 rather than asserted here.
- **Every request carries a token** (§10.5, M5). Including the ones a chart makes.
- **Every number is configuration** (§10.3) — the dev issuer's URL, token lifetime, poll
  intervals, page sizes, the containment export's row cap.
- `make check` green before every commit. TypeScript lints with **`oxlint`**, per
  §10.8 and `docs/ENGINEERING.md` -- which rejects Biome by name and gives three
  reasons. An earlier draft of this plan said `biome`; it was wrong.
- **Do not catch an exception you cannot specifically recover from.** A failed fetch in a
  citation renderer is a rendered error state — a deliberate branch on a known condition — not
  a swallowed one that leaves an empty panel looking like "no data".

## The line this milestone must not cross

- **No Zitadel, no real OIDC redirect flow.** The dev issuer is a dev issuer and the login
  screen says so on screen, in words, where the user can read it.
- **No new analysis.** M6 renders what M3 computes. If a view needs a number that does not
  exist, that is a finding to report, not a new analysis module to write.
- **No eval harness, no rates, no improvement proposals.** M7 and M8.
- **No writes to the plant, no second channel.** The diagnostics UI never talks to `plant-net`.

---

## Task 1: The development issuer becomes a service, and the UI gets a login

`scripts/mint-token.py` becomes a small FastAPI service in `diag-net` exposing a token
endpoint and a JWKS document, with its users and roles read from configuration. The UI gets a
login screen that posts credentials, stores the token, shows who is signed in and their role,
and signs out. The screen states that this is a development issuer and that production
configuration points at a real one.

The existing `TokenField` is deleted, not left beside the new screen.

## Task 2: The stream names the session, and the trace and feedback become endpoints

`/ask` currently tells the client nothing it could later refer to, so §7.2's trace and feedback
have no address. The stream gains a first event carrying `session_id` and the message `seq`;
the agent gains `GET` for a message's trace — SOPs loaded, every tool call with its arguments
and timings, budget consumed, all already stored — and `POST` for §7.2's two feedback
questions, which `agent.feedback` has been shaped for since M4.

## Task 3: The visual language

§15 deferred this deliberately until the shape of an answer could be seen. It can be seen now.
Design tokens, type scale, density, light and dark, and the banked idea M6 was named as the
place for: **colour by category rather than by state** — producing, waiting-on-others,
held-by-own-fault, stopped, transitioning — so the station that *is* the problem reads
differently from the ones merely waiting. Every category carries its second channel.

## Task 4: A renderer per citation kind

§7.3's ten kinds, each resolving to its real endpoint and rendering what comes back. The
contract gains `/alarms/{id}`. A citation that cannot be resolved renders as a citation that
could not be resolved, with the reason — never as nothing.

## Task 5: Charts are citations

§7.4's six built-in types plus the free-form Vega-Lite fallback, all through one renderer. The
specification references a tool call by id and never carries values. The fallback gets its own
tests, because §7.4 says exactly why: it fires rarely, and rarely-fired code is where defects
live.

## Task 6: The stop timeline

Station states as a Gantt across the window, with M3's propagation chain drawn on it — the
consequence episodes and the cause they were derived from, on the same axis, so the derivation
is visible rather than asserted.

## Task 7: Part detail, genealogy, serial search, containment

The part view: its genealogy tree, its station-by-station timeline, the process values recorded
for *that* part, its inspection result and image. Serial search reaches it. The containment
view shows the affected set with an export, and the export is the set the query actually
returned, with its own count on it.

## Task 8: The banners and the reasoning trace

The plant status banner from the gateway — *connected · backfilling · plant offline, history to
03:14*. The contradiction banner when the agent disagrees with the computed propagation, which
M4 puts in the answer object and nothing has yet shown. The reasoning trace under every answer,
permanent and collapsible, and the two feedback questions beside it.

## Task 9: What M6 proves

The authenticity proofs for this milestone, against services that are running: that no rendered
number originates outside a verified tool result; that every citation kind resolves; that every
colour-coded state carries a second channel; that an unauthenticated UI gets a login screen and
not data. Plus the measurement record, and the README saying what M6 does not demonstrate.
