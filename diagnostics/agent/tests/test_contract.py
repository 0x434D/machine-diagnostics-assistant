"""The committed contracts and the live models must not drift apart.

§10.1: contracts/ is the single source of truth, and §7.3 generates the frontend's
TypeScript from it. The analysis service has had this guard since Task 12; the answer
object had none, and the committed file had already been hand-decorated after generation
— which is how a contract stops being checkable. The decoration now lives in the model,
so this comparison is exact.

§7.2's trace joined them at M6: §7.4 has the UI read a tool result out of a trace rather
than summarise one, so the shape it reads is now a contract like any other. So did the
feedback, at M6 Task 8, for the narrower reason that the endpoint answers with everything
now stored rather than with an echo of the request — a response the UI renders from.
"""

from __future__ import annotations

import json
from pathlib import Path

from agent.answer import json_schema as answer_schema
from agent.records import feedback_json_schema as feedback_schema
from agent.records import json_schema as trace_schema

CONTRACTS = Path(__file__).resolve().parents[3] / "contracts"


def test_the_committed_answer_schema_matches_the_model() -> None:
    committed = json.loads((CONTRACTS / "answer.schema.json").read_text())
    assert committed == answer_schema(), (
        "contracts/answer.schema.json is stale — regenerate it with `make contract`"
    )


def test_the_committed_trace_schema_matches_the_model() -> None:
    committed = json.loads((CONTRACTS / "trace.schema.json").read_text())
    assert committed == trace_schema(), (
        "contracts/trace.schema.json is stale — regenerate it with `make contract`"
    )


def test_the_committed_feedback_schema_matches_the_model() -> None:
    committed = json.loads((CONTRACTS / "feedback.schema.json").read_text())
    assert committed == feedback_schema(), (
        "contracts/feedback.schema.json is stale — regenerate it with `make contract`"
    )
