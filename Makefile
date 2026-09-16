SHELL := /bin/bash
.PHONY: preflight lock-check fmt lint test check verify ci ci-scheduled contract m1-report \
        m2a-r5 m2a-demo m2a-propagation backfill-counts authenticity m2c-demo \
        m1-demo browse ask verify-no-gaps \
        lint-python test-python check-python \
        lint-dotnet test-dotnet check-dotnet audit-dotnet \
        lint-frontend test-frontend check-frontend \
        lint-actions lint-commits images scan-images sbom secrets-scan

# The C# gateway arrives in M1 Task 7 and the frontends in Task 14, but the commit hook runs
# `make check` on every commit from now on. Each language block below is guarded on its stack
# existing; delete the guard when the directory does.
GATEWAY := diagnostics/gateway
UI := diagnostics/ui
HMI := plant/hmi

# Spec §10.7 says "restore with --locked-mode", and that reads like a contradiction here.
# --locked-mode is a `dotnet restore` switch; `dotnet build` and `dotnet test` forward it to
# MSBuild, which rejects it (MSB1001). This property is the same instruction in the form
# those two commands accept.
LOCKED := -p:RestoreLockedMode=true

# Handbook §2: TreatWarningsAsErrors covers the C# compiler only — not MSBuild, NuGet or
# custom tasks. Without this on the command line, those slip through silently.
WARNASERROR := -warnaserror

# --- The development issuer ----------------------------------------------------------------
# §14 made every diagnostics endpoint refuse an unauthenticated request — the gateway's
# /status and /reconcile, the agent's /ask, the analysis service's queries — and that includes
# the calls the demo targets below make. M5 was ruled slim and builds no IdP, so
# scripts/mint-token.py is the issuer: it keeps a gitignored keypair, generates it on first
# use, prints the public key the services verify against, and mints the tokens to present.
#
# Both of the two below are shell command substitutions embedded in recipes, NOT make
# variables that $(shell ...) would expand. A $(shell ...) here runs on every invocation of
# this file, so `make check` would mint a token it has no use for on every commit.
MINT := cd $(CURDIR)/diagnostics && uv run --frozen python $(CURDIR)/scripts/mint-token.py

# Configures the stack with the key the demo's own tokens are signed by, overriding anything
# in diagnostics/.env or the shell. That override is the point rather than a rudeness: this
# demo mints from the development issuer, so a stack pointed at any other key would refuse
# every request it makes.
DEV_PUBLIC_KEY = AUTH_PUBLIC_KEY="$$($(MINT) --public-key)"

# `user`, not `admin`. Every endpoint the demo calls is in §10.5's user column, and minting
# the more privileged token would leave the demo unable to tell "open to an operator" from
# "open to an administrator" — it should run as the person it is demonstrating for. An admin
# action added to a demo step later fails loudly here, which is the correct outcome.
#
# Minted per call rather than once into a variable: a token costs ~0.17 s and these demos run
# for minutes, so one held across the run is one that expires halfway through it.
BEARER = -H "Authorization: Bearer $$($(MINT) --role user)"

# --- CI tool pins -------------------------------------------------------------------------
# Every tool the pipeline runs is pinned and invoked from here rather than from a marketplace
# action, so that handbook §9's "CI contains no logic" holds literally: the pipeline is these
# targets, and `make ci` runs the whole of it on a laptop with no remote in existence.
# Image digests come from scripts/pin-images.sh, action SHAs from scripts/pin-actions.sh.
ACTIONLINT := rhysd/actionlint:1.7.12@sha256:b1934ee5f1c509618f2508e6eb47ee0d3520686341fec936f3b79331f9315667
TRIVY      := aquasec/trivy:0.74.0@sha256:62b1e65e8869bc4b4c6aa4fa2b21595256c7c2f6018a9d9ad61caf87187c1969
TRUFFLEHOG := trufflesecurity/trufflehog:3.97.4@sha256:562bc231afa9de3d04de44cfe624252b08207de1fc3cebc5e7ed92bed7f279e4
# The default `docker` buildx driver cannot export OCI with attestations attached, so the
# image targets run on their own docker-container builder. Naming and pinning it here is what
# lets `make images` behave the same on a laptop and on a runner, with no setup action.
BUILDER    := machine-agent
BUILDKIT   := moby/buildkit:v0.33.0@sha256:6c2fa84a6b61ccd72899dde4239f8d5717f05f9a8ca6f3cad185fb1a95a94de3
# BuildKit pulls its SBOM generator at build time. Left as the default `stable-1` tag it is an
# unpinned third-party image with read access to every layer it scans — the one hole the rest
# of this pinning would leave open.
SYFT       := docker/buildkit-syft-scanner:stable-1@sha256:ae4f3b554449e7e25548e7d8ccc029d17357348e30c6e3df01b92bc93654d6a9
# zizmor and committed are on PyPI, so `uvx <tool>@<version>` pins them just as hard with no
# daemon and no image to keep current.
ZIZMOR    := zizmor@1.30.1
COMMITTED := committed@1.1.11

# Handbook §4 tags images by git SHA — immutable, traceable, no version negotiation.
IMAGE_TAG ?= $(shell git rev-parse --short HEAD)
# Handbook §7: this one line stops the image digest changing when nothing but the clock did,
# which is what otherwise makes Compose recreate containers for no reason.
SOURCE_EPOCH := $(shell git log -1 --pretty=%ct)
# §7's `.source` label is what links a published image back to the repository. Read from the
# remote rather than hardcoded, so it is correct the moment a remote exists.
IMAGE_SOURCE := $(shell git remote get-url origin 2>/dev/null)
BUILD_DIR := build

# The range `committed` checks. CI passes the pull request's base..head; locally the default
# is whatever this branch adds on top of main.
COMMIT_RANGE ?= main..HEAD

# Strict: every package has tests now, so "collected nothing" is a package whose tests stopped
# being found, which must fail rather than read as a pass.
# $(3) is the uv package name when it differs from the directory, which happens exactly once:
# diagnostics/mcp/ holds the package `mcp-server`, because `mcp` is the SDK's own distribution
# and a workspace member of that name would shadow the thing it imports.
define pytest-package
	cd $(1) && uv run --frozen --package $(if $(3),$(3),$(2)) pytest $(2)/tests -q
endef

# The same, for a marker, and exit 5 is no longer forgiven. It was, and correctly: the plant
# then had no authenticity-marked test, so `make verify` died on an empty selection before it
# ever reached the diagnostics proofs that did exist. Both packages now carry four each, which
# makes "collected nothing" the failure rather than the cost of the fix — a rename, a dropped
# `pytestmark`, a marker that stops matching, and `verify`, `authenticity` and weekly.yml all
# go green having run zero proofs. That is precisely the breakage Task 12 found, re-enabled by
# the fix for it, in the one guard that stands over all the other guards.
#
# "At least one", not an expected count. The count lives in the test files; a copy of it here
# would have to be edited by whoever adds the fifth proof, and it catches nothing the empty
# selection does not already catch.
#
# $(4) is the uv package name when it differs from the directory, exactly as $(3) is in
# pytest-package above — and for the one case that needs it, diagnostics/mcp/.
define pytest-marked
	cd $(1) && uv run --frozen --package $(if $(4),$(4),$(2)) pytest $(2)/tests -q -m $(3)
endef

define in-gateway
	@if [ -d "$(GATEWAY)" ]; then cd "$(GATEWAY)" && $(1); \
	else echo "skip [$(GATEWAY) arrives in M1 Task 7]: $(1)"; fi
endef

# Two frontends now, in two different stacks: the diagnostics chat box and the plant HMI.
# Parameterised on the directory rather than duplicated per frontend, so a step added to
# one is added to both by construction -- which is the half of §10.8 a second hardcoded
# copy would quietly stop holding.
define in-frontend
	@if [ -d "$(1)" ]; then cd "$(1)" && $(2); \
	else echo "skip [no $(1) in this checkout]: $(2)"; fi
endef

# BuildKit attaches attestations only on an exporter that can carry them; the default docker
# exporter drops them silently, which is why this writes an OCI layout rather than loading into
# the daemon. `tar=false` makes that layout a directory: Trivy reads an OCI *directory* and a
# Docker-format tar, but not an OCI *tar*, which fails with a misleading "manifest.json not
# found". BUILDKIT_SBOM_SCAN_* widen the scan past the final stage — without them a multi-stage
# build's SBOM omits everything the builder installed (handbook §9).
#
# $(1) build context, $(2) Dockerfile, $(3) image name, $(4) extra build args. Every image in
# the repository goes through here: four of the seven did, and the three that did not were the
# gateway, the diagnostics UI and the plant HMI — so `scan-images` passed over the one
# container that sits on field-net. A second hardcoded copy per Dockerfile is how that
# happened, and one macro with a context and a file is what stops it happening again.
define build-image-at
	SOURCE_DATE_EPOCH=$(SOURCE_EPOCH) docker buildx build $(1) \
	  --file $(2) \
	  --builder $(BUILDER) \
	  $(4) \
	  --build-arg BUILDKIT_SBOM_SCAN_CONTEXT=true \
	  --build-arg BUILDKIT_SBOM_SCAN_STAGE=true \
	  --sbom=generator=$(SYFT) --provenance=true \
	  --label org.opencontainers.image.source="$(IMAGE_SOURCE)" \
	  --label org.opencontainers.image.revision="$$(git rev-parse HEAD)" \
	  --label org.opencontainers.image.created="$$(date -u -d @$(SOURCE_EPOCH) +%Y-%m-%dT%H:%M:%SZ)" \
	  --tag machine-agent/$(3):$(IMAGE_TAG) \
	  --output type=oci,tar=false,dest=$(BUILD_DIR)/images/$(3)
endef

# The four Python services: one Dockerfile per stack, parameterised on the package, built
# from the stack's own directory. $(1) stack, $(2) package.
define build-image
$(call build-image-at,$(1),$(1)/Dockerfile,$(2),--build-arg PACKAGE=$(2))
endef

# The /etc/hosts check this used to carry is gone. It existed because the host was
# assumed to need `127.0.0.1 line-simulator` to reach the plant; R3 measured that
# opc.tcp://localhost:4840/plant works — asyncua advertises back whatever netloc the
# client dialled, and the certificate covers localhost and 127.0.0.1 as independently as
# it covers line-simulator (measurements/r3-notes.txt §1). §14's "no manual steps" now
# holds with no qualification, which is worth more than the one matrix row that still
# prefers the entry.
preflight:
	@docker --version >/dev/null || { echo "docker missing"; exit 1; }
	@docker network inspect field-net >/dev/null 2>&1 || docker network create field-net
# pki/ is a host bind mount and pki-init writes private keys 0600, so the uid the plant's
# containers run as must be the uid that owns it. Left to the ${HOST_UID:-1000} default on
# a machine whose user is not 1000, the failure is EACCES on /pki from a container that
# exited seconds ago — self-diagnosing here is worth five lines.
	@uid=$$(id -u); \
	 want=$$(sed -n 's/^HOST_UID=//p' plant/.env 2>/dev/null | tail -1); \
	 want=$${want:-1000}; \
	 [ "$$uid" = "$$want" ] || { \
	   echo "HOST_UID mismatch: the plant stack would run as $$want, you are $$uid."; \
	   echo "pki-init would fail with EACCES on /pki. Fix with:"; \
	   echo "    printf 'HOST_UID=%s\nHOST_GID=%s\n' $$(id -u) $$(id -g) >> plant/.env"; \
	   exit 1; }
	@echo "preflight ok"

# The drift guard spec §10.7 asks for: fails if the lockfile would change, rather than
# letting it move silently.
lock-check:
	cd plant && uv lock --check
	cd diagnostics && uv lock --check

# --- the gate, split by language ----------------------------------------------------------
# `make check` is still lint plus test over everything, and is still what the commit hook
# runs. These are its parts, not a second definition of it: CI's path-filtered jobs call the
# parts, so there is exactly one description of what the gate is (handbook §9).

lint-python: lock-check
	cd plant && uv run --frozen ruff format --check . && uv run --frozen ruff check . \
	  && uv run --frozen mypy --strict --config-file $(CURDIR)/mypy.ini .
	cd diagnostics && uv run --frozen ruff format --check . && uv run --frozen ruff check .
# Per package, not over the workspace: each package has a tests/ with the same module names
# in it, and a single run sees one name defined twice and stops before checking anything.
	cd diagnostics && uv run --frozen mypy --strict --config-file $(CURDIR)/mypy.ini analysis
	cd diagnostics && uv run --frozen mypy --strict --config-file $(CURDIR)/mypy.ini auth
	cd diagnostics && uv run --frozen mypy --strict --config-file $(CURDIR)/mypy.ini agent
	cd diagnostics && uv run --frozen mypy --strict --config-file $(CURDIR)/mypy.ini knowledge
	cd diagnostics && uv run --frozen mypy --strict --config-file $(CURDIR)/mypy.ini mcp
# measurements/ is not a package and sits outside both workspaces, so neither line above
# reaches it -- while gate.yml's path filter does list measurements/**, which made CI run a
# check that never looked at the file that changed. Its runners execute inside the plant
# workspace, so they are checked with the plant's interpreter and dependencies.
	cd plant && uv run --frozen ruff format --check $(CURDIR)/measurements \
	  && uv run --frozen ruff check $(CURDIR)/measurements \
	  && uv run --frozen mypy --strict --config-file $(CURDIR)/mypy.ini $(CURDIR)/measurements
# scripts/ had the same hole measurements/ did: outside both workspaces, so no gate reached
# it, while it writes the contracts every other gate compares against. Checked with the
# diagnostics interpreter because that is the one it imports both services from.
	cd diagnostics && uv run --frozen ruff format --check $(CURDIR)/scripts \
	  && uv run --frozen ruff check $(CURDIR)/scripts \
	  && uv run --frozen mypy --strict --config-file $(CURDIR)/mypy.ini $(CURDIR)/scripts

# contracts/ is the single source of truth (§10.1). The served schema is compared against the
# committed file by analysis/tests/test_contract.py, so this target is for propagating an
# intended change, never for making a failing test pass.
# The M1 demo, end to end. Step 6 is the one the architecture exists for, so it asks the
# question again with the plant stopped rather than logging past the outage.
#
# Every published port is read from the environment with the compose file's own default, so a
# host that already has something on 8080 runs this with GATEWAY_PORT=18080 and nothing else
# changes. Hardcoding them here meant the demo could not run on the machine it was written on.
m1-demo: preflight
	@echo "== 1. plant: boot, build history, go live"
	docker compose -f plant/compose.yml up -d --build
	@until docker compose -f plant/compose.yml exec -T line-simulator \
	    python -c "import urllib.request" >/dev/null 2>&1; do sleep 2; done
	@echo "== 2. a foreign client browses the address space"
	$(MAKE) browse
	@echo "== 3. diagnostics: connect, wait for the plant's phase, backfill, go live"
	$(DEV_PUBLIC_KEY) docker compose -f diagnostics/compose.yml up -d --build
	@until curl -sf $(BEARER) localhost:$${GATEWAY_PORT:-8080}/status | grep -q '"state":"live"'; do \
	    curl -s $(BEARER) localhost:$${GATEWAY_PORT:-8080}/status; echo; sleep 5; done
	@echo "== 4. ask, and open the citation"
# `up -d --build` recreates the agent and the UI too, and step 3 waits only for the gateway --
# so the first run of this demo asked an agent that was still starting and died on an empty
# response. `make ask` retries; this waits for the page a human is being sent to.
	@until curl -sf -o /dev/null "localhost:$${UI_PORT:-5173}/"; do sleep 2; done
	@echo "   the chat box is at http://localhost:$${UI_PORT:-5173} -- try the question below,"
	@echo "   then click the citation chip under the answer to open the part it names."
	@$(MAKE) ask
	@echo "== 5. downstream outage: Postgres stops, the queue fills, nothing is lost"
	docker compose -f diagnostics/compose.yml stop postgres
	@sleep 30; curl -s $(BEARER) localhost:$${GATEWAY_PORT:-8080}/status; echo
	docker compose -f diagnostics/compose.yml start postgres
	@until [ "$$(curl -sf $(BEARER) localhost:$${GATEWAY_PORT:-8080}/status | sed -n 's/.*"queueDepth":\([0-9]*\).*/\1/p')" = "0" ]; do \
	    curl -s $(BEARER) localhost:$${GATEWAY_PORT:-8080}/status; echo; sleep 5; done
	@$(MAKE) verify-no-gaps
	@echo "== 6. upstream outage: the plant stops, and the question is asked again anyway"
	docker compose -f plant/compose.yml stop line-simulator
	@$(MAKE) ask
	@echo "   ^ answered from history, with the plant shut down. That is the whole point."
	docker compose -f plant/compose.yml start line-simulator
	@echo "   ...and the outage window closes by HistoryRead, not by being forgotten."
	@until curl -sf $(BEARER) localhost:$${GATEWAY_PORT:-8080}/status | grep -q '"state":"live"'; do \
	    curl -s $(BEARER) localhost:$${GATEWAY_PORT:-8080}/status; echo; sleep 5; done
	@$(MAKE) verify-no-gaps
	@echo "== 7. the numbers"
	$(MAKE) m1-report

# A client that is not our gateway, proving the boundary is a real OPC UA server rather than
# gateway-specific glue.
browse:
	docker run --rm --network field-net --user "$$(id -u):$$(id -g)" \
	  -v "$(CURDIR)/pki:/pki:ro" -v "$(CURDIR)/measurements:/measurements:ro" \
	  machine-agent-plant-line-simulator \
	  python /measurements/run_r3.py --probe opc.tcp://line-simulator:4840/plant \
	  --pki /pki --secure

# Asks the agent, not the analysis API: the whole chain is the claim, and the analysis API
# alone would demonstrate the half that was never in doubt. /ask streams, so this keeps the
# last data: frame -- the answer object -- and drops the progress lines.
ASK ?= How many parts were rejected in the last hour, and what were the defects?
# `export`, because the recipe reads it from the environment rather than interpolating it:
# the question contains characters that a shell would otherwise get an opinion about.
export ASK
ask:
	@port=$${AGENT_PORT:-8001}; \
	 body=$$(curl -sfN --retry 10 --retry-delay 3 --retry-connrefused --retry-all-errors \
	   -X POST "localhost:$$port/ask" $(BEARER) -H 'Content-Type: application/json' \
	   --data-binary "$$(python3 -c 'import json,os; print(json.dumps({"question": os.environ["ASK"]}))')") \
	 || { echo "the agent did not answer on localhost:$$port"; exit 1; }; \
	 printf '%s' "$$body" | sed -n 's/^data: //p' | tail -1 \
	   | python3 -c 'import json,sys; a = json.load(sys.stdin); print(a["answer_markdown"]); \
	       print("citations:", [c["id"] for f in a["findings"] for c in f["citations"]] or "none")'

# §1's second and third proofs both end here: is what is stored still everything the plant
# produced. Exits non-zero on a hole, so it can be a demo step rather than a thing to read.
verify-no-gaps:
	@curl -sf $(BEARER) "localhost:$${GATEWAY_PORT:-8080}/reconcile" \
	  | python3 -c 'import json,sys; r = json.load(sys.stdin); \
	      print(json.dumps(r, indent=2)); \
	      sys.exit(0 if r["reconciled"] else "RECONCILIATION FAILED")' \
	  && echo "reconciled: nothing was read and lost, and no window is recorded as missing"

# A PRINTOUT, NOT A PROOF, and the distinction is the whole reason this comment is long.
#
# It shows what the backfill read against what is stored, per stream, over the window the
# ledger actually covers rather than /reconcile's default one. That is worth showing: at 26
# streams "reconciled: true" says nothing about which stream holds what.
#
# What it does NOT do is assert `read == stored`, and it must not. Bounding the window changes
# what is printed, not what can be checked: `ReconciliationResult.Reconciled` is
# `Gaps.Count == 0 && all(Lost == 0)`, and `Lost` is `Math.Max(0, ...)`, so a surplus -- stored
# exceeding read -- cannot fail it. An earlier version of this target was documented as proving
# the equality while exiting on exactly the criterion `verify-no-gaps` uses, which is this
# project's signature defect written into the file that catalogues its proofs.
#
# Asserting the equality here would be worse than leaving it unasserted, because the surplus is
# legitimate. A reconnect runs BackfillFromStorageAsync a second time (Program.cs), so
# `min(from_ts) .. max(to_ts)` unions two passes and contains the live stretch between them --
# rows the subscription wrote that no window ever claimed. §1's own proofs cause exactly that,
# by stopping the plant. Measured both ways on two boots: 402,044 read / 402,044 stored on a
# clean single-pass boot, and 402,044 read / 427,571 stored on one that had reconnected. A
# target asserting equality would call the second gateway broken.
#
# `read_rows == pg_rows` is therefore not provable from inside the diagnostics stack at all.
# It needs the three-way comparison against the plant's own ledger, which lives outside both
# stacks -- see measurements/authenticity/README.md, which lists it as not yet provable and
# names what it waits on.
#
# The exit code is the same `reconciled` verify-no-gaps uses: gaps and losses, honestly, and
# nothing more. The bounds come from `backfill_windows` rather than from a date typed here,
# because the window is whatever this boot produced.
backfill-counts:
	@bounds=$$(docker compose -f diagnostics/compose.yml exec -T postgres \
	    psql -U postgres -d diagnostics -t -A \
	    -c "SELECT to_char(min(from_ts) AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS.USZ'), \
	        to_char(max(to_ts) AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS.USZ') \
	        FROM backfill_windows" | tr -d '\r'); \
	 from=$${bounds%%|*}; to=$${bounds##*|}; \
	 [ -n "$$from" ] && [ "$$from" != "$$to" ] \
	   || { echo "backfill_windows is empty: nothing has been backfilled yet"; exit 1; }; \
	 echo "   window $$from .. $$to"; \
	 curl -sf $(BEARER) "localhost:$${GATEWAY_PORT:-8080}/reconcile?from=$$from&to=$$to" \
	   | python3 -c 'import json,sys; r = json.load(sys.stdin); \
	       rows = sorted(r["streams"], key=lambda s: s["stream"]); \
	       [print("   {stream:24} read={rowsReturned:7} stored={rowsStored:7} lost={lost}".format(**s)) for s in rows]; \
	       print("   {} streams, {} read, {} stored, {} lost, {} gaps".format(len(rows), \
	         sum(s["rowsReturned"] for s in rows), sum(s["rowsStored"] for s in rows), \
	         sum(s["lost"] for s in rows), len(r["gaps"]))); \
	       sys.exit(0 if r["reconciled"] else "gaps or lost rows over the backfill window")'

# Aggregates every M1 measurement into one table with a verdict per risk, and exits non-zero
# if any risk has neither a pass nor a recorded, justified deviation — so an unmeasured risk
# cannot pass silently.
m1-report:
	cd plant && uv run --frozen --package simulator python $(CURDIR)/measurements/report.py

# R5: the one number M1's results cannot predict -- historian throughput at M2's stream
# count. Run before Task 2, not after the line is built, so a bad number changes the
# design rather than the excuses.
m2a-r5:
	cd plant && uv run --frozen --package simulator python $(CURDIR)/measurements/run_r5.py

# M2a's authenticity proof on its own, so the demo can show it without running the gate.
# In process and under a second: it drives the same four station classes `server.build_line`
# builds, with the address space replaced by recorders.
m2a-propagation:
	cd plant && uv run --frozen --package simulator pytest simulator/tests/test_propagation.py -v

# The M2a demo: the line, where M1 had one station. Same shape as m1-demo, same rule about
# ports -- every published port is read from the environment with the compose file's own
# default, because the M1 demo could not run on the machine it was written on.
#
# What this does NOT do, stated rather than left as a gap a viewer has to notice: it does not
# let you stop S2 by hand. The HMI is read-only in M2a. §3.5 requires every injection to write
# the ground-truth log, §3.6 puts that log in M2c, and M5 gates the injection panel -- so a
# button added here would inject faults nothing records, which is the one thing §13 forbids.
# Step 6 runs the proof instead, and the proof is the stronger artifact anyway: it asserts the
# delay rather than inviting you to watch for it.
# M2c, end to end: one of §3.5's eight scenarios runs, and its consequences are read out of
# §5.2's tables. The ground-truth log is shown from inside the plant, where it lives -- and
# step 6 is the demonstration that nothing on the other side of the boundary can reach it.
#
# PLANT_SCENARIO picks the row (1-8); 3 is the default because it is the one that ends in an
# alarm and a shutdown, so every §5.2 table M2c touches has something in it inside one run.
#
# Every published port is read from the environment with the compose file's own default, for
# the reason the M1 demo now is: it could not run on the machine it was written on, because
# 8080 was already taken. On a host like that, `GATEWAY_PORT=18080 ANALYSIS_PORT=18000 make
# m2c-demo` is the whole change.
m2c-demo: preflight
	@echo "== 1. plant: scenario $${PLANT_SCENARIO:-3} of §3.5's eight, from the first part"
	PLANT_SCENARIO=$${PLANT_SCENARIO:-3} docker compose -f plant/compose.yml up -d --build
	@until docker compose -f plant/compose.yml exec -T line-simulator \
	    python -c "import urllib.request" >/dev/null 2>&1; do sleep 2; done
	@echo "== 2. what the scenario says it will do, before anything has happened"
# The gate above says the container execs Python; it does not say the log exists.
# `server.main` opens the ground-truth log after the status file, the historian and the
# server context, so a cold start reliably lost this step to a missing file while the
# re-run seconds later got through. Wait for the artefact the next line points at, which
# is the rule 84b2dfc set for the M2a demo and step 2 was inserted ahead of.
	@until docker compose -f plant/compose.yml exec -T line-simulator \
	    test -s /gt/ground-truth.jsonl >/dev/null 2>&1; do sleep 2; done
	@docker compose -f plant/compose.yml exec -T line-simulator \
	    grep -v '"record": "part"' /gt/ground-truth.jsonl
	@echo "   ^ the run header and every injection, with the consequences §3.5's row claims."
	@echo "     Consequences, never a cause: what an analysis should conclude is M3's, and"
	@echo "     writing it here would be the answer key written by the reasoning M7 grades."
	@echo "== 3. the screen the line is built with, now with a fault panel on it (§3.7)"
	@until curl -sf -o /dev/null "localhost:$${PLANT_HMI_PORT:-5174}/"; do sleep 2; done
	@echo "   http://localhost:$${PLANT_HMI_PORT:-5174} -- alarms list, and a panel that"
	@echo "   injects. Every injection you make there is written to the ground-truth log too,"
	@echo "   with source=operator and no consequences: nobody wrote down what should follow"
	@echo "   from a fault chosen at a keyboard."
	@echo "== 4. diagnostics: topology, subscriptions, backfill, live"
	$(DEV_PUBLIC_KEY) docker compose -f diagnostics/compose.yml up -d --build
	@until curl -sf $(BEARER) localhost:$${GATEWAY_PORT:-8080}/status | grep -q '"state":"live"'; do \
	    curl -s $(BEARER) localhost:$${GATEWAY_PORT:-8080}/status; echo; sleep 5; done
	@echo "== 5. the consequences, out of §5.2's tables and nowhere else"
	@echo "-- alarms: what S2 raised, when, and when it was cleared"
	@docker compose -f diagnostics/compose.yml exec -T postgres psql -U postgres -d diagnostics \
	    -c "SELECT s.code, a.code, a.text, a.severity, a.raised_at, a.acked_at, a.cleared_at \
	        FROM alarms a JOIN stations s ON s.id = a.station_id \
	        ORDER BY a.raised_at LIMIT 10"
	@echo "-- state_changes: what the line did around that alarm, oldest first"
# No `reason IS NOT NULL` and no `DESC`, and both are the fix rather than an oversight.
# `Aborted` carries no reason by design (line.py says why: the alarm names the cause), so
# filtering on one excluded row 3's actual consequence outright; and the newest twelve rows
# are the ordinary buffer suspends of the live phase, while the scenario's consequences sit
# in history. Anchored on the first alarm, which is where they are. A run that raises none
# -- scenarios 1, 2 and 4-8 -- falls back to the first suspension of the run.
	@docker compose -f diagnostics/compose.yml exec -T postgres psql -U postgres -d diagnostics \
	    -c "SELECT s.code, c.source_ts, c.from_state, c.to_state, c.reason, b.code AS buffer \
	        FROM state_changes_settled c JOIN stations s ON s.id = c.station_id \
	        LEFT JOIN buffers b ON b.id = c.reason_buffer_id \
	        WHERE c.source_ts >= COALESCE( \
	            (SELECT min(raised_at) FROM alarms), \
	            (SELECT min(source_ts) FROM state_changes_settled WHERE reason IS NOT NULL) \
	          ) - interval '60 seconds' \
	        ORDER BY c.source_ts LIMIT 12"
	@echo "-- inspection_results: §3.4's six scores, per class, above the threshold"
# /inspection/stats requires `from` and `to` (§5.3's window is not optional), and the call
# here carried neither: it printed a 422 naming both as missing, under a heading promising
# six scores, and `curl -s` without `-f` exited 0 so the demo went on. The window comes out
# of the table rather than off a date typed here, because it is whatever this boot ingested;
# `to` is exclusive, hence the second past the last row.
	@bounds=$$(docker compose -f diagnostics/compose.yml exec -T postgres \
	    psql -U postgres -d diagnostics -t -A \
	    -c "SELECT to_char(min(source_ts) AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS.USZ'), \
	        to_char((max(source_ts) + interval '1 second') AT TIME ZONE 'UTC', \
	                'YYYY-MM-DD\"T\"HH24:MI:SS.USZ') \
	        FROM inspection_results" | tr -d '\r'); \
	 from=$${bounds%%|*}; to=$${bounds##*|}; \
	 [ -n "$$from" ] && [ "$$from" != "$$to" ] \
	   || { echo "   inspection_results is empty: nothing has been ingested yet"; exit 1; }; \
	 echo "   window $$from .. $$to"; \
	 body=$$(curl -sf $(BEARER) --get --data-urlencode "from=$$from" --data-urlencode "to=$$to" \
	   "localhost:$${ANALYSIS_PORT:-8000}/inspection/stats") \
	   || { echo "   /inspection/stats did not answer over that window"; exit 1; }; \
	 printf '%s' "$$body" | python3 -m json.tool
	@echo "   ^ read these as evidence and not as an answer. §3.3 is explicit that the first"
	@echo "     station to raise an alarm is NOT the root cause -- the simulator produced both"
	@echo "     from one injected fault -- and 004_m2c.sql says so above the table itself."
	@echo "     Scenarios 1 and 2 raise no alarm anywhere, which is the same warning from the"
	@echo "     other side."
	@echo "== 6. the boundary the evaluation rests on"
# The one check in this demo that is about the invariant the whole evaluation rests on, so
# it must not be able to pass for the wrong reason. `exec ... ls /gt` fails identically
# whether /gt is absent or the container is -- and with the diagnostics stack down it was
# printing "not mounted", which is the reassuring answer to a question nobody asked.
# So: prove there is a gateway to inspect first, and fail loudly when there is not.
	@docker compose -f diagnostics/compose.yml exec -T edge-gateway ls / >/dev/null 2>&1 \
	  || { echo "   NO GATEWAY TO INSPECT -- this check proves nothing about the boundary."; \
	       echo "   Bring the diagnostics stack up and run it again."; exit 1; }
	@if docker compose -f diagnostics/compose.yml exec -T edge-gateway \
	      ls /gt >/dev/null 2>&1; then \
	    echo "   REACHABLE from the gateway -- every evaluation number here is worthless"; \
	    exit 1; \
	 else \
	    echo "   /gt is not mounted in the gateway, which is the container that could"; \
	    echo "   reach the plant at all"; \
	 fi
	@echo "   ^ ground truth lives on plant-ground-truth, which exactly one container mounts."
	@echo "     This step inspected the gateway; that no OTHER diagnostics container mounts"
	@echo "     it is test_compose_invariants.py's claim, in the gate, over both files."
	@echo "== 7. the same run again, from the same seed"
	@docker compose -f plant/compose.yml exec -T line-simulator \
	    head -1 /gt/ground-truth.jsonl
	@echo "   ^ run_id is crc32(seed : scenario : history start : depth), derived and not"
	@echo "     drawn, so two runs of one scenario are the same run and can be compared byte"
	@echo "     for byte. test_a_run_is_reproducible_byte_for_byte_from_its_seed is the proof."
	@echo "== 8. what is NOT here"
	@echo "   Nothing above diagnosed anything. Whether the analysis reaches the right cause"
	@echo "   is M3, and scoring it against this log is M7."

m2a-demo: preflight
	@echo "== 1. plant: four stations, three buffers, 25 historised streams, 33 h of history"
	docker compose -f plant/compose.yml up -d --build
	@until docker compose -f plant/compose.yml exec -T line-simulator \
	    python -c "import urllib.request" >/dev/null 2>&1; do sleep 2; done
	@echo "== 2. the screen the line is built with (§15)"
	@until curl -sf -o /dev/null "localhost:$${PLANT_HMI_PORT:-5174}/"; do sleep 2; done
	@echo "   http://localhost:$${PLANT_HMI_PORT:-5174} -- and what to watch for:"
	@echo "     * four stations green while the line is producing;"
	@echo "     * B1_2 and B2_3 fill to capacity and stay there, B3_4 sits at 0-2."
	@echo "       That is S3 being the bottleneck at 6.00 s against S1's 5.70 and S2's 5.85;"
	@echo "     * S1 and S2 turn amber every couple of minutes on blocked:B1_2 / blocked:B2_3."
	@echo "       Amber is the consequence colour and no fault is injected anywhere -- the"
	@echo "       plant runs a fixed nominal takt in M2a, so every stop you see is the line"
	@echo "       waiting on itself."
	@echo "== 3. a foreign client browses the address space, buffers and all"
	$(MAKE) browse
	@echo "== 4. diagnostics: discover the topology, subscribe to 25 streams, backfill, go live"
	$(DEV_PUBLIC_KEY) docker compose -f diagnostics/compose.yml up -d --build
	@until curl -sf $(BEARER) localhost:$${GATEWAY_PORT:-8080}/status | grep -q '"state":"live"'; do \
	    curl -s $(BEARER) localhost:$${GATEWAY_PORT:-8080}/status; echo; sleep 5; done
	@echo "== 5a. the one thing this stack can check about its own storage"
	@$(MAKE) verify-no-gaps
	@echo "   ^ read this precisely. Reconciled means no recorded gap and no stream whose"
	@echo "     shortfall page boundaries do not explain. It is NOT 'nothing was missed':"
	@echo "     Lost clamps at zero, so a surplus -- which every running gateway has, because"
	@echo "     the live subscription writes rows no backfill window claimed -- carries no"
	@echo "     information either way."
	@echo "== 5b. the same comparison, per stream, over the window the backfill covers"
	@$(MAKE) backfill-counts
	@echo "   ^ a printout, and deliberately not a proof. Bounding the window changes what is"
	@echo "     printed, not what is checked, and asserting read == stored here would be wrong:"
	@echo "     a gateway that reconnected backfills a second time, so this window spans a live"
	@echo "     stretch and legitimately stores more than it read. §1's read_rows == pg_rows"
	@echo "     needs the plant's own ledger, which is on the far side of a boundary carrying"
	@echo "     OPC UA and nothing else (§4.5). measurements/authenticity/README.md lists it as"
	@echo "     not yet provable, and says what it waits on."
	@echo "== 6. propagation, measured -- the step M1 had no line to show"
	@echo "   Stop S2, and S3 starves once B2_3 drains. Not before, and not in sympathy."
	@$(MAKE) m2a-propagation
	@echo "   ^ the delay is derived from the level B2_3 held at the moment S2 stopped,"
	@echo "     never the 30 s §3.1 quotes: in steady state that level is 4 or 5, so the"
	@echo "     real delay is 24-32 s and a hardcoded 30 would be right about half the time."
	@echo "== 7. ask, with the whole line behind the answer"
	@until curl -sf -o /dev/null "localhost:$${UI_PORT:-5173}/"; do sleep 2; done
	@echo "   the chat box is at http://localhost:$${UI_PORT:-5173}"
	@$(MAKE) ask
	@echo "== 8. the numbers -- M1's, and only M1's"
	$(MAKE) m1-report
	@echo "   ^ this table is R1-R4 measured against an M1 gateway over three streams. M2a has"
	@echo "     not re-measured it: measurements/run_r1_r2.py does not run against this gateway"
	@echo "     and its marker says why. Nothing above this line depends on it."

contract:
	cd diagnostics && uv run --frozen --package analysis python $(CURDIR)/scripts/generate-contract.py
	$(call in-frontend,$(UI),pnpm install --frozen-lockfile && pnpm generate)

test-python: lock-check
	$(call pytest-package,plant,simulator)
	$(call pytest-package,plant,inspection)
	$(call pytest-package,diagnostics,auth)
	$(call pytest-package,diagnostics,knowledge)
	$(call pytest-package,diagnostics,analysis)
	$(call pytest-package,diagnostics,agent)
	$(call pytest-package,diagnostics,mcp,mcp-server)

check-python: lint-python test-python

# Handbook §2 argues `dotnet format --verify-no-changes` is redundant in CI, because
# EnforceCodeStyleInBuild already fails the build. It is kept anyway: spec §10.8's "identical
# to what CI runs" is the stronger invariant, and a CI target that is a subset of the local
# one is precisely the divergence §9 exists to prevent. The cost is seconds.
lint-dotnet:
	$(call in-gateway,dotnet format --verify-no-changes && dotnet build $(LOCKED) $(WARNASERROR))

test-dotnet:
	$(call in-gateway,dotnet test $(LOCKED) $(WARNASERROR))

check-dotnet: lint-dotnet test-dotnet

# `pnpm tsc --noEmit` would check nothing: tsconfig.json is a solution file whose two
# referenced projects hold all the sources, and a type gate that silently checks nothing is
# worse than none. --build walks the references and --force stops a stale .tsbuildinfo from
# reporting a pass it did not earn.
lint-frontend:
	$(call in-frontend,$(UI),pnpm install --frozen-lockfile && pnpm oxlint && pnpm prettier --check . && pnpm tsc --build --force)
	$(call in-frontend,$(HMI),pnpm install --frozen-lockfile && pnpm oxlint && pnpm prettier --check . && pnpm tsc --build --force)
# The generated types compile whether or not they still match contracts/ -- a stale one is
# valid TypeScript asserting the shape of an endpoint that has moved on. Regenerating and
# failing on a diff is the same guard lock-check is, in the one place the handbook left to
# "it compiles".
#
# $(UI) only, and $(HMI) gets no no-op stand-in for it: the HMI has no generated types --
# it reads the simulator's own snapshot, which is inside one stack and has no entry in
# contracts/ -- and a gate step that checks nothing is worse than no step. What holds that
# payload still is simulator/tests/test_hmi.py.
	$(call in-frontend,$(UI),pnpm generate && git diff --exit-code src/generated)

test-frontend:
	$(call in-frontend,$(UI),pnpm vitest run)
	$(call in-frontend,$(HMI),pnpm vitest run)

check-frontend: lint-frontend test-frontend

fmt:
	cd plant && uv run --frozen ruff format . && uv run --frozen ruff check --fix .
	cd diagnostics && uv run --frozen ruff format . && uv run --frozen ruff check --fix .
	$(call in-gateway,dotnet format)
	$(call in-frontend,$(UI),pnpm oxlint --fix && pnpm prettier --write .)
	$(call in-frontend,$(HMI),pnpm oxlint --fix && pnpm prettier --write .)

lint: lint-python lint-dotnet lint-frontend

test: test-python test-dotnet test-frontend

check: lint test

# Separate from `check` on purpose: the authenticity proofs stop and restart containers, and a
# gate slow enough to skip is not a gate. Task 15 registers the `authenticity` marker.
#
# Four packages, and the last two arrived a milestone after their marker did. M4 registered
# `authenticity` in agent/pyproject.toml and mcp/pyproject.toml, which reads from a distance
# exactly like a wired one — a marker that is declared, excluded from `addopts`, and run by
# nothing. §1.6 and §1.7 are the proofs those two lines now run, and they are the first
# proofs in this project that answer for the agent rather than for the pipe beneath it.
#
# Five packages now. M5 adds `auth`, and it is the one whose proof is not about a service:
# §1.8's claim is *every* diagnostics endpoint, which no single service can say — the
# analysis service cannot answer for the agent, neither can answer for the MCP server, and
# none of the three holds the `agent.sessions` row. It runs where the rule they share lives.
verify:
	$(call pytest-marked,plant,simulator,authenticity)
	$(call pytest-marked,diagnostics,auth,authenticity)
	$(call pytest-marked,diagnostics,analysis,authenticity)
	$(call pytest-marked,diagnostics,agent,authenticity)
	$(call pytest-marked,diagnostics,mcp,authenticity,mcp-server)

# `make verify` plus the plant it needs, brought up and taken down again.
#
# `verify`'s diagnostics half starts a gateway of its own against a plant that is *already*
# running -- it has no stack of its own to bring up -- so `verify` alone cannot be a scheduled
# job. This is the target a schedule can call, and it is why §1's four proofs could break for
# three tasks with nothing red: `verify` is in no workflow, and a human habit is what was
# supposed to run it.
#
# Not added to `check` or `ci`: it builds two stacks, generates 33 h of history and stops
# containers, which is minutes on a laptop and tens of them on a runner. `ci-scheduled` is
# where the checks that cost that much already live.
authenticity: preflight
	docker compose -f plant/compose.yml up -d --build
# The plant's own phase, read through the HMI's proxy rather than with an OPC UA client:
# `verify` reads history, and history read mid-catch-up is history still being written.
# Bounded, so a plant that never comes up fails here rather than hanging the schedule.
	@for i in $$(seq 1 120); do \
	   curl -sf "localhost:$${PLANT_HMI_PORT:-5174}/api/plant/snapshot" \
	     | grep -q '"phase":"live"' && break; \
	   sleep 10; \
	 done; \
	 curl -sf "localhost:$${PLANT_HMI_PORT:-5174}/api/plant/snapshot" \
	   | grep -q '"phase":"live"' \
	   || { echo "plant never reached live"; docker compose -f plant/compose.yml logs --tail 40 line-simulator; \
	        docker compose -f plant/compose.yml down; exit 1; }
# The teardown runs whether the proofs passed or failed, and the exit status is still theirs.
	@$(MAKE) verify; status=$$?; \
	 docker compose -f plant/compose.yml down; \
	 exit $$status

# --- the rest of the pipeline (handbook §9) -----------------------------------------------

# actionlint catches workflow syntax and the shell mistakes inside `run:`; zizmor catches the
# security ones — template injection, over-broad permissions, unpinned actions. The findings
# barely overlap, and both take seconds.
#
# zizmor's --offline is its default, stated explicitly so it reads as a decision rather than a
# warning on every run. Its online audits want a GitHub token, and handing a credential to a
# linter job is a worse trade than the handful of extra checks it buys.
lint-actions:
	docker run --rm -v "$(CURDIR)":/repo -w /repo $(ACTIONLINT) -color
	uvx $(ZIZMOR) --offline .github/workflows

# A commit message is the only part of a commit that cannot be corrected later without
# rewriting history, so it is checked before the merge rather than after.
lint-commits:
	uvx $(COMMITTED) $(COMMIT_RANGE)

# All seven containers the two stacks run, so that `scan-images` below covers the whole of
# what is deployed rather than the subset that happened to share a Dockerfile. Each context
# and file is the pair its Compose service already declares — the gateway and the UI build
# from the repository root because they need Directory.Build.props and contracts/
# respectively, which is why neither fitted the per-stack macro above.
images:
	@mkdir -p $(BUILD_DIR)/images
	@docker buildx inspect $(BUILDER) >/dev/null 2>&1 \
	  || docker buildx create --name $(BUILDER) --driver docker-container \
	       --driver-opt image=$(BUILDKIT) >/dev/null
	$(call build-image,plant,simulator)
	$(call build-image,plant,inspection)
	$(call build-image,diagnostics,analysis)
	$(call build-image,diagnostics,agent)
	$(call build-image-at,.,diagnostics/gateway/Dockerfile,edge-gateway,)
	$(call build-image-at,.,diagnostics/ui/Dockerfile,diagnostics-ui,)
	$(call build-image-at,plant,plant/hmi/Dockerfile,plant-hmi,)

# Handbook §9: fail on HIGH and CRITICAL "with fixes available". --ignore-unfixed is that
# second half, and it is not a softening — without it the gate fails on vulnerabilities nobody
# can act on, which is how a scanner ends up switched off.
scan-images: images
	@for img in $(BUILD_DIR)/images/*/; do \
	  name="$$(basename $$img)"; echo "trivy: $$name"; \
	  docker run --rm -v "$(CURDIR)/$(BUILD_DIR)/images":/scan:ro $(TRIVY) image \
	    --input "/scan/$$name" \
	    --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 --quiet || exit 1; \
	done

# uv reads uv.lock directly, so this is a dependency SBOM with no build required — the
# "at least top-level dependencies" the CRA asks for (handbook §9).
# The format value is `cyclonedx1.5`; handbook §9 names it `cyclonedx`, which uv rejects.
#
# Covers the two Python workspaces and nothing else. Named in full, because there are four
# dependency graphs in this repository and two of them are not in here:
#
#   in    plant/uv.lock        -> build/sbom/plant.cdx.json        (simulator, inspection)
#   in    diagnostics/uv.lock  -> build/sbom/diagnostics.cdx.json  (analysis, agent)
#   NOT   diagnostics/gateway/Gateway/packages.lock.json           (NuGet, the edge gateway)
#   NOT   diagnostics/ui/pnpm-lock.yaml AND plant/hmi/pnpm-lock.yaml  (npm, two frontends)
#
# All four have lock files that could produce one — dotnet through CycloneDX.NET, pnpm through
# @cyclonedx/cyclonedx-npm, each a third-party tool this repository would have to adopt and
# pin. Stated rather than left to be inferred from a build directory holding two files: an
# SBOM that silently covers half the dependencies is worse than one that says which half,
# because the first gets believed. `scan-images` is the other half of the answer and it does
# cover all seven images, the gateway and both frontends included.
sbom:
	@mkdir -p $(BUILD_DIR)/sbom
	cd plant && uv export --frozen --all-packages --no-dev --format cyclonedx1.5 \
	  -o $(CURDIR)/$(BUILD_DIR)/sbom/plant.cdx.json
	cd diagnostics && uv export --frozen --all-packages --no-dev --format cyclonedx1.5 \
	  -o $(CURDIR)/$(BUILD_DIR)/sbom/diagnostics.cdx.json

# --results=verified is the entire point: it turns "47 findings" into "one live key, rotate it
# now", which is what makes a weekly scan actionable with no triage budget (handbook §6).
# trufflehog exits 183 on a finding, not 1.
secrets-scan:
	docker run --rm -v "$(CURDIR)":/repo:ro $(TRUFFLEHOG) git file:///repo \
	  --results=verified --fail --no-update

# NuGet audit warnings are carved out of warnings-as-errors in Directory.Build.props so a CVE
# published overnight cannot break an unrelated build at an arbitrary time. This is the
# scheduled job that carve-out promises: the same build with the carve-out removed.
audit-dotnet:
	$(call in-gateway,dotnet build $(LOCKED) $(WARNASERROR) -p:WarningsNotAsErrors= -p:NuGetAuditMode=all)

# The whole pull-request pipeline in one command. This is the answer to "do I need a remote to
# run CI": no. gate.yml calls exactly these targets and nothing else.
ci: lint-commits lint-actions check sbom

# What weekly.yml runs. Split from `ci` so that `ci` keeps meaning "what the pull-request
# gate runs" — the claim CLAUDE.md makes. Two kinds of check live here: the time-dependent
# ones, whose verdict moves when a third party publishes rather than when this repository
# changes; and `authenticity`, which is too slow for a pull request and was in nothing at all
# until §1's four proofs turned out to have been broken for three tasks with no run to say so.
ci-scheduled: secrets-scan audit-dotnet scan-images authenticity
