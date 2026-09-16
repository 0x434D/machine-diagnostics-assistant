"""§6.3's answer object. Nothing is parsed out of prose — the model produces this shape
through a tool call, and every sentence a user reads traces back to a verified finding.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

Basis = Literal["measured", "derived", "hypothesis"]

# §6.3: `hypothesis` means the claim required interpretation from the knowledge base. M1
# routes no knowledge (that is M4), so a hypothesis here would have nothing behind it —
# it would be an invention wearing an epistemic label. Refused structurally rather than
# left to a prompt, because a prompt is not a guarantee.
ALLOW_HYPOTHESIS = False


class Citation(BaseModel):
    """§7.3's typed citation. M1 emits only `part`; the other kinds arrive with the
    analysis endpoints that resolve them."""

    kind: Literal["part"]
    id: str


class Finding(BaseModel):
    statement: str
    basis: Basis
    citations: list[Citation] = Field(default_factory=list)
    evidence_strength: str | None = None

    @model_validator(mode="after")
    def _check_basis(self) -> Finding:
        validate_basis(
            self.basis, self.evidence_strength, allow_hypothesis=ALLOW_HYPOTHESIS
        )
        return self


class Method(BaseModel):
    sops_used: list[str] = Field(default_factory=list)
    tools_called: list[str] = Field(default_factory=list)
    budget_used: int = 0
    # Which provider produced this answer. Recorded because a simulated answer and a real
    # one are not the same claim, and the difference must survive into the trace.
    provider: str = "unknown"


class Clarification(BaseModel):
    """§6.7's one sanctioned question back, as an object rather than a sentence.

    Asking is a refusal to investigate, so it has to be as inspectable as an answer: §8.1
    scores "asked when it should not have" as its own case class, and a question hidden
    inside `answer_markdown` could only be scored by reading prose. `readings` are the
    investigations that would have differed — fewer than two of them is not ambiguity, and
    the validator says so rather than letting a caveat ship as a question.
    """

    question: str
    readings: list[str]

    @model_validator(mode="after")
    def _check_readings(self) -> Clarification:
        if len(self.readings) < 2:
            raise ValueError(
                "a clarification needs at least two readings (\u00a76.7): one reading is "
                "an assumption to state in a caveat, not a question to ask"
            )
        return self


class Contradiction(BaseModel):
    derived_root: str
    agent_root: str
    reasoning: str


class Answer(BaseModel):
    findings: list[Finding] = Field(default_factory=list)
    answer_markdown: str
    method: Method
    caveats: list[str] = Field(default_factory=list)
    contradiction: Contradiction | None = None
    clarification: Clarification | None = None

    @model_validator(mode="after")
    def _check_clarification(self) -> Answer:
        # §6.7 makes asking and answering alternatives: "ask back *or* assume the most
        # likely one and say so". An answer carrying both has investigated a question it
        # claimed it could not read, and the reader has no way to know which half to trust.
        if self.clarification is not None and self.findings:
            raise ValueError(
                "an answer that asks back has no findings (\u00a76.7): it either "
                "investigated or it did not"
            )
        return self


def validate_basis(
    basis: Basis, evidence_strength: str | None, *, allow_hypothesis: bool
) -> None:
    """§6.3: evidence_strength is required whenever basis is hypothesis, and its presence
    is checked structurally rather than asked for in a prompt."""
    if basis == "hypothesis":
        if not allow_hypothesis:
            raise ValueError(
                "basis 'hypothesis' requires a knowledge base to interpret from, and M1 "
                "routes none; a hypothesis here would be an invention"
            )
        if not evidence_strength:
            raise ValueError("basis 'hypothesis' requires evidence_strength (§6.3)")


#: The committed contract carries a dialect and a human title that Pydantic does not emit.
#: They live here rather than being applied by hand to the generated file, so that
#: regenerating is reproducible and tests/test_contract.py has something exact to compare
#: against. A hand-decorated contract is a contract nothing can check.
SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
SCHEMA_TITLE = "machine-agent answer object (\u00a76.3)"


def json_schema() -> dict[str, object]:
    """§6.3's answer object as JSON Schema. The UI generates its types from this."""
    return {
        "$schema": SCHEMA_DIALECT,
        **Answer.model_json_schema(),
        "title": SCHEMA_TITLE,
    }
