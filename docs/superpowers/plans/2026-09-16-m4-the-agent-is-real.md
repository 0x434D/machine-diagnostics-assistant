# M4 — The agent is real

**Goal:** A question in plain language becomes a staged pipeline that classifies it, resolves its time window in code, checks whether the data is even there, routes the right documents from their own front-matter, calls the analysis tools under a budget, verifies every cited id against the database, and composes findings into prose that traces back to them. Plus an MCP server, so an agent nobody here built reaches the same tools, reads the same SOPs, and arrives at the same conclusion.

**Architecture:** The analysis computes what follows necessarily from the data; the knowledge base supplies what requires experience to interpret; the agent applies the second to the first — and may contradict the first, with reasons. Routing is deterministic from the documents' own front-matter, so adding diagnostic competence means adding a file. The answer is an object, never parsed out of prose.

**Spec:** §6 in full, §7.3's citation contract, §6.11 for MCP.

---

## Global Constraints

**The model (§6.9, and a standing human ruling)**
- **There is no API key in this environment.** Everything M4 builds runs and is tested against `ScriptedProvider`, which already exists, is deterministic, reaches no network, and discloses in every answer that it is not a model. `AnthropicProvider` also already exists and is imported lazily so its absence blocks nothing. M4 adds no third provider.
- Default model stays `claude-sonnet-5`. The OpenAI-compatible adapter §6.9 describes is **not** built — one adapter with one implementation is the abstraction CLAUDE.md forbids until a second is real.

**Truthfulness (§6.3, §6.5, §6.6)**
- The model produces structured findings **through a tool call**. Nothing is parsed out of prose.
- `basis` is the claim's epistemic status: `measured` is read from data, `derived` follows necessarily from the propagation computation, `hypothesis` required interpretation from the knowledge base. **`evidence_strength` is required whenever `basis` is `hypothesis`** and states the support in figures.
- **Every cited id is resolved against the database before the answer ships.** Cited SOPs are additionally checked against the set routing actually loaded, so the model cannot invent a procedure it never read. One retry, then the offending claims are removed and the answer ships with a visible note, and the failure is logged so its frequency is measurable.
- The composer **arranges; it does not author.** A summary sentence it writes becomes a finding itself, inheriting the citations of what it summarises, and passes through verification like everything else.

**Knowledge (§6.2)**
- Routing is deterministic from front-matter. **There is no routing table in code.**
- **`always_load` is the critical flag.** The method never arrives through search — a failed retrieval must not silently become a failed method.
- A retrieval budget caps document count and total size; when routing selects too many, ranking is by specificity. Context dilution degrades these systems, so the budget is a correctness measure.
- Documents are hot-reloaded; reload swaps an immutable index rather than mutating one in place.

**The database**
- The agent's tables land in an `agent` schema owned by an `agent` role, migrated by Alembic. **Migration 005 left a warning that must be obeyed**: the database-wide `search_path` is `ingest, public`, so an unqualified `CREATE TABLE` from Alembic lands in the *gateway's* schema. The migration that creates the role sets `ALTER ROLE agent SET search_path = agent` in the same migration. A role-level setting overrides the database-level one; that is the mechanism, not a convention for Alembic to remember.

**Quality gates**
- `make check` green before every commit. `mypy --strict` with `disallow_any_explicit`. No blanket suppressions.
- **`make contract` after any change to the answer schema or the analysis surface**; `test_contract.py` is the enforced drift gate in both packages.
- **The agent's tool loop is one of CLAUDE.md's three sanctioned recovery sites** — tool errors return to the model as tool results rather than crashing the request. Everywhere else, let it propagate. After several consecutive failures the run aborts with an honest message.
- §10.3: every number is configuration — the tool budget, the retrieval budget, the retry count, the concurrency cap.

---

## The line this milestone must not cross

- **No identity, no login.** M5.
- **No UI beyond what already exists.** M6 builds the evidence panel, the charts and the timeline.
- **No eval harness.** M7 scores; M4 must not grade itself.
- **No improvement loop.** M8.
- **No second provider adapter** until something needs it.

---

## Task 1: The agent's own schema, and the trap 005 left

**Files:** `diagnostics/agent/alembic/`, `alembic.ini`, migration 001, `diagnostics/compose.yml`, tests

- [ ] **Step 1: Failing test.** The `agent` role can write its own tables and **cannot** read `ingest.*` or write `read.*`. Assert the specific `InsufficientPrivilege`, as `test_read_layer.py` does.
- [ ] **Step 2: The role, and the search_path.** Create the role and set `ALTER ROLE agent SET search_path = agent` **in the same migration**, before any `CREATE TABLE`. Add a test that creates a table through Alembic and asserts it landed in `agent` and not `ingest` — 005's warning says the symptom is agent tables owned by the wrong role and truncated by the gateway's fixtures, which is invisible until it is expensive.
- [ ] **Step 3: §5.2's four tables** — `sessions(id, subject, created_at)`, `messages(session_id, seq, role, content, created_at)`, `traces(session_id, message_seq, sops_loaded, tool_calls, budget, timings)`, `feedback(session_id, message_seq, useful, matched_reality, comment, created_at)`. Session ids are unguessable. There is no `users` table: identity lives in the provider and sessions carry the `sub` claim — which is M5's, so `subject` is nullable for now with a comment saying why.
- [ ] **Step 4:** Register the `authenticity` marker in `diagnostics/agent/pyproject.toml`; it has none, and `make verify` currently cannot pick up anything the agent proves.

## Task 2: The knowledge base becomes readable

30 documents exist in `knowledge/` with real front-matter. **Nothing reads them.**

**Files:** `diagnostics/agent/src/agent/knowledge.py`, tests

- [ ] **Step 1: Failing tests.** Every document parses; every `id` is unique; a file without an `id` is ignored (`knowledge/README.md` specifies this and is itself such a file); the two `core/` documents carry `always_load`.
- [ ] **Step 2: The loader.** Parse YAML front-matter into an immutable index: id, title, path, body, and the `applies_to` keys actually in use — `question_types`, `defect_classes`, `dimensions`, `stations`, `alarm_codes`.
- [ ] **Step 3: Hot reload** that swaps the index rather than mutating it, so a reload mid-question is safe. Test that a question in flight sees a consistent index.
- [ ] **Step 4:** `GET /knowledge/{id}` on the **analysis** service — §5.3 lists it there. It serves the document by id, which is what makes a `sop` citation openable.

## Task 3: Routing, and the flag that must not fail silently

**Files:** `diagnostics/agent/src/agent/routing.py`, tests

- [ ] **Step 1: Failing tests.** `always_load` documents are in every result regardless of the question — assert this for a question that matches nothing. A `stop_investigation` routes SOP-01. A defect-class question routes that defect's document. Over-selection is ranked by specificity and truncated to the budget. **Nothing in the module is a routing table** — a test that greps the module for document ids and fails if it finds any hardcoded one.
- [ ] **Step 2: Implement** selection from front-matter alone, then specificity ranking, then the budget.
- [ ] **Step 3: The failure that matters.** A test proving that when routing selects nothing, `always_load` still arrives — §6.2 calls a failed retrieval silently becoming a failed method "the single most common failure of runbook-driven agents".

## Task 4: The staged pipeline

Three of §6.1's eight stages exist. Build the rest.

**Files:** `diagnostics/agent/src/agent/pipeline.py`, `classify.py`, tests

- [ ] **Step 1: Stage 1 — classify** into §6.1's fixed set: `stop_investigation`, `quality_investigation`, `status`, `trend`, `statistics`, `traceability`, `knowledge`, `out_of_scope`. An unclassifiable question falls back to `knowledge` **with a caveat**, never a guess.
- [ ] **Step 2: Stage 2 — the model reads the time phrase, `/time/resolve` computes it.** Replace the `"last N hours"` regex, whose own docstring says the shift calendar is M3's and M3 built it.
- [ ] **Step 3: Stage 3 — coverage is a guard, not a choice.** Call `/coverage` as its own step; if the window has gaps that fact enters the context and the answer must mention it. Test that it cannot be skipped.
- [ ] **Step 4: Stage 5 — the tool loop over all 15 analysis operations**, budgeted, every call logged. Tool errors return to the model as tool results; after several consecutive failures the run aborts honestly. Budget exhaustion produces a **partial answer stating what it could not finish**, never a silently truncated one.
- [ ] **Step 5: Stage 6.5 — the composer arranges.** Replace `_compose()`, which only knows inspection stats. A summary sentence becomes a finding with inherited citations.
- [ ] **Step 6: §6.7's clarifying-question rule.** Ask back only when the plausible readings lead to materially different investigations **and** no reading is clearly more likely. Otherwise assume the most likely and say so in a caveat. Test both directions — the mirror case, where it looks ambiguous and is not, is a failure if it asks.

## Task 5: Citations widen, and `ALLOW_HYPOTHESIS` flips

**Files:** `answer.py`, `citations.py`, `contracts/answer.schema.json`, tests

- [ ] **Step 1:** Widen `Citation.kind` to §7.3's vocabulary, restricted to what M3 actually serves: `part`, `stop`, `alarm`, `signal`, `pattern`, `sop`, `serial`, `lot`, `containment`. **No `chart` kind** — charts are M6's.
- [ ] **Step 2:** Each kind resolves against a real endpoint. A kind that cannot be resolved is not a kind.
- [ ] **Step 3: Cited SOPs are checked against what routing loaded**, not merely against the knowledge base. §6.5 is explicit: the model must not invent a procedure it never read.
- [ ] **Step 4: Flip `ALLOW_HYPOTHESIS`.** It is `False` with a comment saying M1 routes no knowledge. M4 does. A `hypothesis` finding without `evidence_strength` must still raise.

## Task 6: Contradiction is a first-class field

**Files:** `pipeline.py`, `answer.py`, tests

- [ ] **Step 1:** When the agent disagrees with the computed propagation it populates `contradiction` with its own root and its reasoning. §6.5: this is only possible because the derivation is visible.
- [ ] **Step 2:** Test the case DP-11 was written for — an operator intervention mid-stop misleads the mechanical chain.
- [ ] **Step 3:** A contradiction without reasoning is invalid. Assert it.

## Task 7: MCP — the method transfers

**Files:** `diagnostics/mcp/` (new workspace member), `diagnostics/compose.yml`, tests

- [ ] **Step 1:** Streamable HTTP, bound to localhost, on `diag-net`, **never** on `field-net`. Pin the spec revision explicitly and say which.
- [ ] **Step 2: tools** — the analysis queries, read-only, every one. **resources** — the knowledge base by URI: `sop://SOP-01`, `defect://misalignment`, `pattern://DP-02`, `station://S2`, `alarm://A-207`. **`diagnose(question)`** — the full staged pipeline with verified citations.
- [ ] **Step 3: The test §6.11 demands** — the MCP tool list and the OpenAPI operation set derive from the **same definition**. Neither binding can do anything the other cannot. Generate both from one source and assert it.
- [ ] **Step 4:** Add the package to the workspace, to `make test-python`, and to the gate.

## Task 8: What M4 proves

`measurements/authenticity/README.md` has rows 1.6 and 1.7 open, both waiting on M4.

- [ ] **Step 1: 1.6 — the agent says "I have no data for that window" rather than inventing.** With the scripted provider this is structural rather than behavioural: assert that an empty window yields no `measured` finding and a caveat, that a `hypothesis` cannot ship without evidence strength, and that an unresolvable citation is stripped with a visible note. **Say plainly in the README that the behavioural claim needs a real model**, and that the scripted provider cannot be asked whether a model would resist inventing.
- [ ] **Step 2: 1.7 — an external MCP client reaches the same tools and gets the same results.** Drive `diagnose()` over MCP and the same question over REST, and assert the answers agree. This is the proof that the method transfers to an agent we did not build.
- [ ] **Step 3:** Update the README with what M4 closes and what remains: 1.8 waits on M5, 1.9 on M7.

---

## Judgement calls made here

- **No OpenAI-compatible adapter.** §6.9 describes two adapters; building the second with no consumer is the one-implementation abstraction CLAUDE.md forbids. The `Provider` protocol already exists, so adding it later is additive.
- **No `chart` citation kind.** §7.3 lists it and §7.4 describes the vocabulary, but a chart citation that nothing renders is a claim with no referent. M6.
- **`sessions.subject` is nullable.** §5.2 says it carries the OIDC `sub` claim; there is no identity provider until M5.
- **The scripted provider stays the default.** Every test in this milestone runs without a key. When a key arrives, the same pipeline runs against `AnthropicProvider` with no code change — that is what the provider protocol is for, and it is the only claim M4 makes about the real model.
