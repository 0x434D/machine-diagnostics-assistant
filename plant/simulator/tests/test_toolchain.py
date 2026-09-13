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
