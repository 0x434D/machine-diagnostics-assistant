"""Regenerate contracts/analysis.openapi.yaml from the analysis service.

The committed file is what the frontend's TypeScript is generated from, and
analysis/tests/test_contract.py fails when it and the served schema disagree — so an API
change that was not propagated breaks the gate instead of drifting quietly.
"""

from __future__ import annotations

import pathlib
import sys

import yaml

sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parents[1] / "diagnostics/analysis/src")
)

from analysis.app import app

HEADER = """# Generated from diagnostics/analysis (FastAPI) and committed as the contract.
# §10.1: contracts/ is the single source of truth — the frontend's TypeScript types are
# generated from this file, and analysis/tests/test_contract.py fails if the served
# schema and this file disagree, so an API change that was not propagated breaks the
# gate rather than drifting quietly.
#
# Regenerate with: make contract
"""


def main() -> None:
    target = (
        pathlib.Path(__file__).resolve().parents[1] / "contracts/analysis.openapi.yaml"
    )
    target.write_text(HEADER + yaml.safe_dump(app.openapi(), sort_keys=True, width=100))
    print(f"wrote {target}")


if __name__ == "__main__":
    main()
