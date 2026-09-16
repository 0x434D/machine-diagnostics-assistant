# M5 — Security and identity

**Goal:** Every diagnostics endpoint refuses an unauthenticated request, including MCP. An `admin`-only action performed as `user` returns 403. The plant's one privileged action is gated separately, by a mechanism that shares nothing with the diagnostics stack. §1's row 1.8 closes.

**Architecture:** One shared validation module, deliberately small — the spec calls it *"roughly thirty lines"* — consumed by analysis, agent and MCP the way `knowledge` already is. The gateway gets the same rules in C#. Roles arrive as token claims; there is no permission matrix, no groups, no per-resource rules, and no `users` table: identity lives in the issuer and `sessions.subject` carries the `sub` it has been typed for since M4.

**Spec:** §10.5 in full, §14's two identity lines.

**Human ruling carried from 2026-09-15:** M5 is **slim** — a JWT validation module with statically configured roles, **no Zitadel container**.

---

## What "slim" means, precisely, and what it costs

§10.5's argument for brokering is about *adding providers later being an admin-UI task*. That argument survives untouched: the application is an OIDC client of exactly one issuer, knows one claim shape and one role source, and never learns that Google exists. What M5 does not build is the issuer itself.

So: the services validate a JWT against a **configured public key or JWKS URL**, check `aud`, and read the role claim. A development issuer — a script minting tokens from a local keypair — stands in for Zitadel. The validation module is the part that matters and the part §1.8 proves; the issuer is the part that is replaceable by configuration.

**What this costs, stated rather than hidden:** §14's *"adding Google or Microsoft is demonstrably an admin-UI task: no code change, no redeploy"* is **not demonstrable** without an issuer that brokers. M5 closes 1.8 and leaves that separate claim open. The README says so.

---

## Global Constraints

- **The plant and the diagnostics stack must never share an issuer** (§10.5). A shared token issuer or user store would be a second channel across the boundary — exactly what the two-stack split exists to prevent. The plant HMI's fault injection is gated by a shared token in the plant's own `.env`, checked by the simulator. Two identity mechanisms, disjoint by construction, and a test asserts they share no configuration.
- **Roles are `admin` and `user`.** No permission matrix, no groups, no per-resource rules.
- **There is no `users` table.** `sessions.subject` carries the OIDC `sub`.
- Every HTTP service binds to localhost; there is no TLS between containers. This is a local demonstration system and the README says so in those words.
- `make check` green before every commit. `mypy --strict` with `disallow_any_explicit`. No blanket suppressions.
- **Do not catch an exception you cannot specifically recover from.** A failed signature check is a 401, which is a deliberate branch on a known condition, not a swallowed error.
- §10.3: every number is configuration — clock skew tolerance, token lifetime, the audience, the issuer.

## The line this milestone must not cross

- **No Zitadel, no IdP container, no user management UI.** Ruled.
- **No browser OIDC flow with redirect URIs and silent renewal.** The UI attaches a token; the full `oidc-client-ts` dance needs an issuer and belongs with M6's login screen.
- **No per-resource permissions.** §10.5 is explicit that there are none.
- **No eval harness, no improvement loop.** M7, M8.

---

## Task 1: The validation module

**Files:** `diagnostics/auth/` (new workspace member), tests

- [ ] **Step 1: Failing tests.** A valid token passes and yields its subject and role. Each of these is refused, distinguishably: no token; a malformed token; a good signature with the **wrong audience**; an expired token; a token signed by a key the service does not trust; a token with no role claim; a token whose role is outside `{admin, user}`.
- [ ] **Step 2: Implement** `verify(token, settings) -> Principal | None` — subject, role, and nothing else. Keep it small; §10.5 calls it thirty lines and the smallness is the point.
- [ ] **Step 3: Clock skew is configuration.** A token one second past expiry at a skew of zero is refused; the same token inside the skew is accepted. State the default and why.
- [ ] **Step 4: A development issuer** — a script that mints a token for a given subject and role from a local keypair. It is not a service, it is not in compose, and its keypair is gitignored. Say in its docstring that it stands where Zitadel will.

## Task 2: Every diagnostics endpoint refuses an unauthenticated request

**Files:** `diagnostics/analysis`, `diagnostics/agent`, `diagnostics/mcp`, tests

- [ ] **Step 1: The test that must not be satisfiable by a list.** Enumerate the served routes **from each app itself** — FastAPI exposes them — and assert every one refuses an unauthenticated request. A hand-written list of paths passes while a new endpoint added tomorrow is open by default; a generated one fails.
- [ ] **Step 2:** Wire the dependency into all three services. 401 for missing or invalid, 403 for authenticated-but-not-permitted.
- [ ] **Step 3: MCP included.** §14 names it specifically. The tools are the analysis queries and they are read-only, but read-only is not public.
- [ ] **Step 4: Health endpoints, if any, are a deliberate exception** with a comment saying why — not an oversight that happens to be convenient.

## Task 3: The two admin-only actions

§10.5's matrix has four admin rows. Two exist to be gated now; two do not exist yet.

- [ ] **Step 1:** `admin` only — reloading the knowledge base, and the raw model exchange and system prompts. `user` gets 403, and the 403 is distinguishable from the 401.
- [ ] **Step 2:** Everything else — asking questions, viewing answers, evidence, traces, traceability and containment — is open to `user`. Assert it, so a later tightening is a deliberate act.
- [ ] **Step 3:** Approving improvement-loop proposals and managing users do not exist. Do not invent endpoints to gate.

## Task 4: The gateway, in C#

**Files:** `diagnostics/gateway`, tests

- [ ] **Step 1:** `/status` refuses an unauthenticated request with the same rules — same issuer, same audience, same role claim.
- [ ] **Step 2:** The gateway is not a Python service and cannot import the shared module. Two implementations of one rule is the cost of the language split; assert they agree on the same token fixtures, so the pair cannot drift silently.

## Task 5: The plant's one privileged action, and the boundary

**Files:** `plant/simulator`, `plant/hmi`, tests

- [ ] **Step 1:** Fault injection is gated by a shared token in the plant's `.env`, checked by the simulator. §10.5 calls this honest for a machine-local HMI.
- [ ] **Step 2: The invariant test.** The plant's mechanism and the diagnostics stack's share **no** configuration — not an issuer, not a key, not an audience, not a token. A test asserts it by reading both stacks' configuration, so a later convenience that unified them fails loudly. This is the fifth of CLAUDE.md's seven invariants to become executable.

## Task 6: The UI attaches a token

- [ ] **Step 1:** The chat box sends the token; a 401 is rendered as *"not signed in"* rather than an empty answer. A 403 says which action needed which role.
- [ ] **Step 2:** No redirect flow, no silent renewal — those need an issuer and are M6's.

## Task 7: What M5 proves

- [ ] **Step 1: Row 1.8.** An unauthenticated request to **every** diagnostics endpoint returns 401, MCP included, enumerated from the apps rather than listed. An admin-only action as `user` returns 403. `sessions.subject` carries the `sub`.
- [ ] **Step 2: Mark `authenticity`**, and add the packages to `make verify` the way M4 did.
- [ ] **Step 3: Update `measurements/authenticity/README.md`** — 1.8 closes, 1.9 still waits on M7. And record plainly that §14's *"adding Google is an admin-UI task"* is **not** demonstrated, because that needs the issuer this milestone deliberately did not build.
