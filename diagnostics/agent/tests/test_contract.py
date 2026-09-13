"""The committed answer contract and the live model must not drift apart.

§10.1: contracts/ is the single source of truth, and §7.3 generates the frontend's
TypeScript from it. The analysis service has had this guard since Task 12; the answer
object had none, and the committed file had already been hand-decorated after generation
— which is how a contract stops being checkable. The decoration now lives in the model,
so this comparison is exact.
"""

from __future__ import annotations

import json
from pathlib import Path

from agent.answer import json_schema

CONTRACT = Path(__file__).resolve().parents[3] / "contracts" / "answer.schema.json"


def test_the_committed_answer_schema_matches_the_model() -> None:
    assert json.loads(CONTRACT.read_text()) == json_schema(), (
        "contracts/answer.schema.json is stale — regenerate it with `make contract`"
    )
