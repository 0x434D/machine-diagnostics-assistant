import re
import sys
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]  # diagnostics/

# This deliberately mirrors plant/simulator/tests/test_toolchain.py instead of sharing a
# helper with it. §10.1 says the two stacks share no code, and a module imported across the
# workspace boundary would be the first thing to make that false.


def _ci_filters() -> dict[str, list[str]]:
    gate = yaml.safe_load((ROOT.parent / ".github/workflows/gate.yml").read_text())
    filters = [
        step["with"]["filters"]
        for job in gate["jobs"].values()
        for step in job.get("steps", [])
        if isinstance(step, dict) and "filters" in step.get("with", {})
    ]
    assert len(filters) == 1, "expected exactly one paths-filter step"
    parsed: dict[str, list[str]] = yaml.safe_load(filters[0])
    return parsed


def _commit_hook_filters() -> tuple[dict[str, str], str]:
    """The `grep -qE` patterns in `.claude/hooks/gate-commit.sh`, by the target they select.

    Read off the script rather than restated here, so this asserts what the hook does and not
    what someone typed into a test alongside it.
    """
    hook = (ROOT.parent / ".claude/hooks/gate-commit.sh").read_text()
    per_target = {
        target: pattern
        for pattern, target in re.findall(
            r"grep -qE '([^']+)' <<<\"\$staged\" \\\n\s*&& targets\+=\((check-\w+)\)",
            hook,
        )
    }
    fallback = re.search(
        r"grep -qE '([^']+)' <<<\"\$staged\" && targets=\(check\)", hook
    )
    assert fallback is not None, "the hook no longer has a run-everything fallback"
    assert set(per_target) == {"check-python", "check-dotnet", "check-frontend"}, (
        f"the hook selects {sorted(per_target)}, which is not the three stacks"
    )
    return per_target, fallback.group(1)


def test_runs_on_pinned_python() -> None:
    pinned = (ROOT / ".python-version").read_text().strip()
    assert sys.version.startswith(pinned), f"expected {pinned}, running {sys.version}"


def test_workspace_members_pin_the_same_python() -> None:
    root = tomllib.loads((ROOT / "pyproject.toml").read_text())
    members = root["tool"]["uv"]["workspace"]["members"]

    # Read off the tree rather than written down here. A package added under diagnostics/
    # and left out of `members` is not locked, not built into either image and not reached
    # by `make check`, and nothing else in the repository would say so — where a listed
    # member that does not exist fails on the next `uv lock` and is loud already.
    assert set(members) == {path.parent.name for path in ROOT.glob("*/pyproject.toml")}

    for member in members:
        cfg = tomllib.loads((ROOT / member / "pyproject.toml").read_text())
        assert cfg["project"]["requires-python"] == ">=3.13,<3.14"


def test_ci_watches_every_workspace_member() -> None:
    """gate.yml's path filter decides whether the python job runs at all.

    A member missing from it is invisible in exactly the way that matters: a pull request
    touching only that package skips lint, `mypy --strict` and every suite, and the gate
    reports green having checked nothing. It happened — `diagnostics/knowledge/` was added
    as a member and not added here, and the package all three services share could have
    been changed without a single check running.

    Note `knowledge/**` already in the filter is the Markdown tree at the repository root,
    a different directory that happens to share a name. That near-miss is most of why the
    omission was easy to make and hard to see, and is why this is asserted rather than
    remembered.
    """
    watched = set(_ci_filters()["python"])

    members = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["uv"][
        "workspace"
    ]["members"]
    missing = [
        member for member in members if f"diagnostics/{member}/**" not in watched
    ]
    assert missing == [], f"gate.yml's python filter does not watch: {missing}"


def test_the_commit_hook_gates_everything_ci_gates() -> None:
    """The commit hook is a second copy of CI's path filters, and it had already drifted.

    `diagnostics/auth/`, `diagnostics/knowledge/`, `scripts/` and `measurements/` were watched
    by gate.yml and by nothing in the hook, so a commit touching only the shared module that
    decides who gets into every service ran no Python gate — the identical omission
    `test_ci_watches_every_workspace_member` above was written for, one list further down and
    invisible for the same reason.

    Asserted rather than derived: the hook is `sh` and runs before any interpreter this
    repository pins is known to be available, so it cannot read gate.yml itself. What it can
    be is checked, which is what this does — every path CI routes to a stack must reach that
    stack's gate locally, either through its own pattern or through the fallback that runs
    everything.
    """
    hook, fallback = _commit_hook_filters()

    unreached: list[str] = []
    for language, globs in _ci_filters().items():
        target = f"check-{language}"
        if target not in hook:
            continue  # `images`, whose filter selects a job with no local gate.
        for glob in globs:
            # A path the glob would match. `plant/**` -> `plant/x`; a bare filename is itself.
            sample = glob.replace("/**", "/x")
            if not (re.search(hook[target], sample) or re.search(fallback, sample)):
                unreached.append(f"{language}: {glob}")

    assert unreached == [], (
        "gate.yml routes these to a stack's gate; .claude/hooks/gate-commit.sh does not: "
        f"{unreached}"
    )


def test_both_stacks_pin_the_same_interpreter() -> None:
    """The two stacks build independently, so nothing else keeps these pins together — and a
    defect that reproduces on one interpreter but not the other is a day lost to a difference
    nobody chose to make."""
    plant = (ROOT.parent / "plant" / ".python-version").read_text().strip()
    assert (ROOT / ".python-version").read_text().strip() == plant


def test_diagnostics_workspace_does_not_reach_into_plant() -> None:
    """§10.7: two workspaces, one per stack. A shared root lockfile would make §10.1's 'the
    two stacks share no code' false at build time."""
    assert not (ROOT.parent / "uv.lock").exists(), (
        "no lockfile may exist at the repository root"
    )
    assert (ROOT / "uv.lock").exists()
    assert (ROOT.parent / "plant" / "uv.lock").exists()
