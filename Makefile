SHELL := /bin/bash
.PHONY: preflight lock-check fmt lint test check verify

# The C# gateway arrives in M1 Task 7 and the frontends in Task 14, but the commit hook runs
# `make check` on every commit from now on. Each language block below is guarded on its stack
# existing; delete the guard when the directory does. TypeScript targets land with Task 14.
GATEWAY := diagnostics/gateway

# Spec §10.7 says "restore with --locked-mode", and that reads like a contradiction here.
# --locked-mode is a `dotnet restore` switch; `dotnet build` and `dotnet test` forward it to
# MSBuild, which rejects it (MSB1001). This property is the same instruction in the form
# those two commands accept.
LOCKED := -p:RestoreLockedMode=true

# pytest exits 5 when it collects no tests. inspection, analysis and agent have none until
# Tasks 5, 12 and 13, and "no tests yet" must not read as a failing gate. Only 5 is forgiven —
# every other non-zero status still fails.
define pytest-package
	cd $(1) && uv run --frozen --package $(2) pytest $(2)/tests -q; \
	  status=$$?; [ $$status -eq 0 ] || [ $$status -eq 5 ]
endef

define in-gateway
	@if [ -d "$(GATEWAY)" ]; then cd "$(GATEWAY)" && $(1); \
	else echo "skip [$(GATEWAY) arrives in M1 Task 7]: $(1)"; fi
endef

preflight:
	@docker --version >/dev/null || { echo "docker missing"; exit 1; }
	@docker network inspect field-net >/dev/null 2>&1 || docker network create field-net
	@grep -qE '^[[:space:]]*127\.0\.0\.1[[:space:]]+line-simulator' /etc/hosts \
	  || { echo "MISSING host entry. Add this line to /etc/hosts:"; \
	       echo "    127.0.0.1 line-simulator"; exit 1; }
	@echo "preflight ok"

# The drift guard spec §10.7 asks for: fails if the lockfile would change, rather than
# letting it move silently.
lock-check:
	cd plant && uv lock --check
	cd diagnostics && uv lock --check

test: lock-check
	$(call pytest-package,plant,simulator)
	$(call pytest-package,plant,inspection)
	$(call pytest-package,diagnostics,analysis)
	$(call pytest-package,diagnostics,agent)
	$(call in-gateway,dotnet test $(LOCKED))

fmt:
	cd plant && uv run --frozen ruff format . && uv run --frozen ruff check --fix .
	cd diagnostics && uv run --frozen ruff format . && uv run --frozen ruff check --fix .
	$(call in-gateway,dotnet format)

lint: lock-check
	cd plant && uv run --frozen ruff format --check . && uv run --frozen ruff check . \
	  && uv run --frozen mypy --strict --config-file $(CURDIR)/mypy.ini .
	cd diagnostics && uv run --frozen ruff format --check . && uv run --frozen ruff check . \
	  && uv run --frozen mypy --strict --config-file $(CURDIR)/mypy.ini .
	$(call in-gateway,dotnet format --verify-no-changes && dotnet build $(LOCKED))

check: lint test

# Separate from `check` on purpose: the authenticity proofs stop and restart containers, and a
# gate slow enough to skip is not a gate. Task 15 registers the `authenticity` marker.
verify:
	cd plant && uv run --frozen --package simulator pytest simulator/tests -q -m authenticity
