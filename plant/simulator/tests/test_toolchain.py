import json
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]  # plant/

# Both frontends, in both stacks. Here rather than in either of them because §10.7
# forbids the two stacks depending on each other, and a test living in one that read the
# other's package.json would be exactly that. `test_plant_workspace_does_not_reach_into
# _diagnostics` below already establishes this file as the place repository-wide
# toolchain facts are asserted from.
FRONTENDS = [ROOT / "hmi", ROOT.parent / "diagnostics" / "ui"]


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


def test_both_frontends_pin_the_same_pnpm() -> None:
    """gate.yml's frontend job installs ONE pnpm, from `diagnostics/ui/package.json`,
    and runs `pnpm install --frozen-lockfile` in both frontends with it -- while each
    image build reads its own `packageManager` through corepack. Let the two declarations
    drift and CI resolves a lockfile with a different pnpm than the container that ships
    it, which is the class of difference that shows up as a dependency that is present
    locally and absent in the image.

    Asserted rather than avoided: `packageManager` is where corepack reads the version
    from, so there is no one place to move it to.
    """
    present = [path for path in FRONTENDS if path.is_dir()]
    if not present:
        pytest.skip("no frontend directory in this checkout")

    pinned = {
        path.name: json.loads((path / "package.json").read_text())["packageManager"]
        for path in present
    }
    assert len(set(pinned.values())) == 1, f"pnpm versions disagree: {pinned}"


def test_both_frontends_pin_the_node_the_pipeline_installs() -> None:
    """`.nvmrc` is what gate.yml's setup-node reads, and `engines.node` is what each
    frontend declares it runs on. Two numbers for one runtime."""
    present = [path for path in FRONTENDS if path.is_dir()]
    if not present:
        pytest.skip("no frontend directory in this checkout")

    nvmrc = (ROOT.parent / ".nvmrc").read_text().strip()
    for path in present:
        engines = json.loads((path / "package.json").read_text())["engines"]["node"]
        assert engines == nvmrc, f"{path.name} runs node {engines}, CI installs {nvmrc}"
