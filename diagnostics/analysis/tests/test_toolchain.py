import sys
import tomllib
from pathlib import Path

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
