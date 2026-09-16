import sys
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]  # diagnostics/

# This deliberately mirrors plant/simulator/tests/test_toolchain.py instead of sharing a
# helper with it. §10.1 says the two stacks share no code, and a module imported across the
# workspace boundary would be the first thing to make that false.


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
    gate = yaml.safe_load((ROOT.parent / ".github/workflows/gate.yml").read_text())
    filters = [
        step["with"]["filters"]
        for job in gate["jobs"].values()
        for step in job.get("steps", [])
        if isinstance(step, dict) and "filters" in step.get("with", {})
    ]
    assert len(filters) == 1, "expected exactly one paths-filter step"
    watched = set(yaml.safe_load(filters[0])["python"])

    members = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["uv"][
        "workspace"
    ]["members"]
    missing = [
        member for member in members if f"diagnostics/{member}/**" not in watched
    ]
    assert missing == [], f"gate.yml's python filter does not watch: {missing}"


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
