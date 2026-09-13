SHELL := /bin/bash
.PHONY: preflight lock-check fmt lint test check verify ci ci-scheduled contract m1-report \
        m2a-r5 \
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

# Spec §10.7 says "restore with --locked-mode", and that reads like a contradiction here.
# --locked-mode is a `dotnet restore` switch; `dotnet build` and `dotnet test` forward it to
# MSBuild, which rejects it (MSB1001). This property is the same instruction in the form
# those two commands accept.
LOCKED := -p:RestoreLockedMode=true

# Handbook §2: TreatWarningsAsErrors covers the C# compiler only — not MSBuild, NuGet or
# custom tasks. Without this on the command line, those slip through silently.
WARNASERROR := -warnaserror

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
define pytest-package
	cd $(1) && uv run --frozen --package $(2) pytest $(2)/tests -q
endef

# The same, for a marker. Here exit 5 IS forgiven and stays forgiven: a package with no
# authenticity-marked test has nothing to prove, not a broken suite, and `make verify` failing
# on the plant's empty selection is why it had never once run through to the proofs that do
# exist — which is a worse failure than the one the strictness was guarding against.
define pytest-marked
	cd $(1) && uv run --frozen --package $(2) pytest $(2)/tests -q -m $(3); \
	  status=$$?; [ $$status -eq 0 ] || [ $$status -eq 5 ]
endef

define in-gateway
	@if [ -d "$(GATEWAY)" ]; then cd "$(GATEWAY)" && $(1); \
	else echo "skip [$(GATEWAY) arrives in M1 Task 7]: $(1)"; fi
endef

define in-ui
	@if [ -d "$(UI)" ]; then cd "$(UI)" && $(1); \
	else echo "skip [no $(UI) in this checkout]: $(1)"; fi
endef

# BuildKit attaches attestations only on an exporter that can carry them; the default docker
# exporter drops them silently, which is why this writes an OCI layout rather than loading into
# the daemon. `tar=false` makes that layout a directory: Trivy reads an OCI *directory* and a
# Docker-format tar, but not an OCI *tar*, which fails with a misleading "manifest.json not
# found". BUILDKIT_SBOM_SCAN_* widen the scan past the final stage — without them a multi-stage
# build's SBOM omits everything the builder installed (handbook §9).
define build-image
	SOURCE_DATE_EPOCH=$(SOURCE_EPOCH) docker buildx build $(1) \
	  --builder $(BUILDER) \
	  --build-arg PACKAGE=$(2) \
	  --build-arg BUILDKIT_SBOM_SCAN_CONTEXT=true \
	  --build-arg BUILDKIT_SBOM_SCAN_STAGE=true \
	  --sbom=generator=$(SYFT) --provenance=true \
	  --label org.opencontainers.image.source="$(IMAGE_SOURCE)" \
	  --label org.opencontainers.image.revision="$$(git rev-parse HEAD)" \
	  --label org.opencontainers.image.created="$$(date -u -d @$(SOURCE_EPOCH) +%Y-%m-%dT%H:%M:%SZ)" \
	  --tag machine-agent/$(2):$(IMAGE_TAG) \
	  --output type=oci,tar=false,dest=$(BUILD_DIR)/images/$(2)
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
# Per package, not over the workspace: both packages have a tests/ with a conftest.py, and a
# single run sees one module name defined twice and stops before checking anything.
	cd diagnostics && uv run --frozen mypy --strict --config-file $(CURDIR)/mypy.ini analysis
	cd diagnostics && uv run --frozen mypy --strict --config-file $(CURDIR)/mypy.ini agent
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
	docker compose -f diagnostics/compose.yml up -d --build
	@until curl -sf localhost:$${GATEWAY_PORT:-8080}/status | grep -q '"state":"live"'; do \
	    curl -s localhost:$${GATEWAY_PORT:-8080}/status; echo; sleep 5; done
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
	@sleep 30; curl -s localhost:$${GATEWAY_PORT:-8080}/status; echo
	docker compose -f diagnostics/compose.yml start postgres
	@until [ "$$(curl -sf localhost:$${GATEWAY_PORT:-8080}/status | sed -n 's/.*"queueDepth":\([0-9]*\).*/\1/p')" = "0" ]; do \
	    curl -s localhost:$${GATEWAY_PORT:-8080}/status; echo; sleep 5; done
	@$(MAKE) verify-no-gaps
	@echo "== 6. upstream outage: the plant stops, and the question is asked again anyway"
	docker compose -f plant/compose.yml stop line-simulator
	@$(MAKE) ask
	@echo "   ^ answered from history, with the plant shut down. That is the whole point."
	docker compose -f plant/compose.yml start line-simulator
	@echo "   ...and the outage window closes by HistoryRead, not by being forgotten."
	@until curl -sf localhost:$${GATEWAY_PORT:-8080}/status | grep -q '"state":"live"'; do \
	    curl -s localhost:$${GATEWAY_PORT:-8080}/status; echo; sleep 5; done
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
	   -X POST "localhost:$$port/ask" -H 'Content-Type: application/json' \
	   --data-binary "$$(python3 -c 'import json,os; print(json.dumps({"question": os.environ["ASK"]}))')") \
	 || { echo "the agent did not answer on localhost:$$port"; exit 1; }; \
	 printf '%s' "$$body" | sed -n 's/^data: //p' | tail -1 \
	   | python3 -c 'import json,sys; a = json.load(sys.stdin); print(a["answer_markdown"]); \
	       print("citations:", [c["id"] for f in a["findings"] for c in f["citations"]] or "none")'

# §1's second and third proofs both end here: is what is stored still everything the plant
# produced. Exits non-zero on a hole, so it can be a demo step rather than a thing to read.
verify-no-gaps:
	@curl -sf "localhost:$${GATEWAY_PORT:-8080}/reconcile" \
	  | python3 -c 'import json,sys; r = json.load(sys.stdin); \
	      print(json.dumps(r, indent=2)); \
	      sys.exit(0 if r["reconciled"] else "RECONCILIATION FAILED")' \
	  && echo "reconciled: nothing was read and lost, and no window is recorded as missing"

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

contract:
	cd diagnostics && uv run --frozen --package analysis python $(CURDIR)/scripts/generate-contract.py
	$(call in-ui,pnpm install --frozen-lockfile && pnpm generate)

test-python: lock-check
	$(call pytest-package,plant,simulator)
	$(call pytest-package,plant,inspection)
	$(call pytest-package,diagnostics,analysis)
	$(call pytest-package,diagnostics,agent)

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
	$(call in-ui,pnpm install --frozen-lockfile && pnpm oxlint && pnpm prettier --check . && pnpm tsc --build --force)
# The generated types compile whether or not they still match contracts/ -- a stale one is
# valid TypeScript asserting the shape of an endpoint that has moved on. Regenerating and
# failing on a diff is the same guard lock-check is, in the one place the handbook left to
# "it compiles".
	$(call in-ui,pnpm generate && git diff --exit-code src/generated)

test-frontend:
	$(call in-ui,pnpm vitest run)

check-frontend: lint-frontend test-frontend

fmt:
	cd plant && uv run --frozen ruff format . && uv run --frozen ruff check --fix .
	cd diagnostics && uv run --frozen ruff format . && uv run --frozen ruff check --fix .
	$(call in-gateway,dotnet format)
	$(call in-ui,pnpm oxlint --fix && pnpm prettier --write .)

lint: lint-python lint-dotnet lint-frontend

test: test-python test-dotnet test-frontend

check: lint test

# Separate from `check` on purpose: the authenticity proofs stop and restart containers, and a
# gate slow enough to skip is not a gate. Task 15 registers the `authenticity` marker.
verify:
	$(call pytest-marked,plant,simulator,authenticity)
	$(call pytest-marked,diagnostics,analysis,authenticity)

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

images:
	@mkdir -p $(BUILD_DIR)/images
	@docker buildx inspect $(BUILDER) >/dev/null 2>&1 \
	  || docker buildx create --name $(BUILDER) --driver docker-container \
	       --driver-opt image=$(BUILDKIT) >/dev/null
	$(call build-image,plant,simulator)
	$(call build-image,plant,inspection)
	$(call build-image,diagnostics,analysis)
	$(call build-image,diagnostics,agent)

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
# Covers the two Python workspaces and nothing else. The gateway's NuGet graph and the UI's
# npm graph are NOT in here, and both have lock files that could produce one — dotnet through
# CycloneDX.NET, pnpm through @cyclonedx/cyclonedx-npm, each a third-party tool this repository
# would have to adopt and pin. Stated rather than left to be inferred from a file listing two
# of the four stacks: an SBOM that silently covers half the dependencies is worse than one that
# says which half, because the first gets believed.
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
# gate runs" — the claim CLAUDE.md makes. These are the time-dependent checks: their
# verdict moves when a third party publishes, not when this repository changes.
ci-scheduled: secrets-scan audit-dotnet scan-images
