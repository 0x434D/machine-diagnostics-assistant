"""Regenerate the two files in contracts/ from the code that serves them.

The committed files are what the frontend's TypeScript is generated from, and a test in
each package fails when a committed file and the live definition disagree — so an API
change that was not propagated breaks the gate instead of drifting quietly.
"""

from __future__ import annotations

import json
import pathlib
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "diagnostics/analysis/src"))
sys.path.insert(0, str(ROOT / "diagnostics/agent/src"))

from agent.answer import json_schema
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
    openapi = ROOT / "contracts/analysis.openapi.yaml"
    openapi.write_text(
        HEADER + yaml.safe_dump(app.openapi(), sort_keys=True, width=100)
    )
    print(f"wrote {openapi}")

    answer = ROOT / "contracts/answer.schema.json"
    answer.write_text(json.dumps(json_schema(), indent=2) + "\n")
    print(f"wrote {answer}")


if __name__ == "__main__":
    main()
