"""§6.5: every cited id is resolved against the database before the answer ships."""

from __future__ import annotations

import pytest
from agent.answer import Answer, Citation, Finding, Method, validate_basis
from agent.citations import strip, verify
from agent.tools import AnalysisClient

from .fakes import FakeAnalysis


def _answer(*findings: Finding) -> Answer:
    return Answer(
        findings=list(findings),
        answer_markdown="...",
        method=Method(budget_used=1, provider="scripted"),
        caveats=[],
    )


async def test_a_citation_that_does_not_resolve_is_rejected(
    fake_analysis: FakeAnalysis,
) -> None:
    answer = _answer(
        Finding(
            statement="Part A-99999999 was rejected.",
            basis="measured",
            citations=[Citation(kind="part", id="A-99999999")],
        )
    )

    result = await verify(answer, cast_client(fake_analysis))

    assert result.failed_ids == ["A-99999999"]
    assert not result.ok


async def test_unresolvable_claims_are_removed_and_the_answer_says_so(
    fake_analysis: FakeAnalysis,
) -> None:
    """After the retry §6.5 allows, the offending claims go and the answer carries a
    visible note — the removal is never silent."""
    answer = _answer(
        Finding(
            statement="A-00000007 was rejected for a gap.",
            basis="measured",
            citations=[Citation(kind="part", id="A-00000007")],
        ),
        Finding(
            statement="A-99999999 was rejected too.",
            basis="measured",
            citations=[Citation(kind="part", id="A-99999999")],
        ),
    )

    stripped = await strip(answer, cast_client(fake_analysis))

    assert len(stripped.findings) == 1
    assert stripped.findings[0].citations[0].id == "A-00000007"
    assert any("could not be verified" in caveat for caveat in stripped.caveats)


async def test_a_fully_verified_answer_is_returned_unchanged(
    fake_analysis: FakeAnalysis,
) -> None:
    """The other direction, so the strip is not passing by removing everything."""
    answer = _answer(
        Finding(
            statement="A-00000007 was rejected.",
            basis="measured",
            citations=[Citation(kind="part", id="A-00000007")],
        )
    )

    stripped = await strip(answer, cast_client(fake_analysis))

    assert stripped.findings == answer.findings
    assert stripped.caveats == []


def test_m1_never_claims_a_hypothesis() -> None:
    """§6.3: `hypothesis` means the claim required interpretation from the knowledge base.
    M1 routes none, so a hypothesis would be an invention wearing an epistemic label."""
    with pytest.raises(ValueError, match="knowledge base"):
        Finding(
            statement="Carrier 7 is worn.",
            basis="hypothesis",
            citations=[],
            evidence_strength="92 %, n=214",
        )


def test_evidence_strength_is_required_for_a_hypothesis() -> None:
    with pytest.raises(ValueError, match="evidence_strength"):
        validate_basis(
            basis="hypothesis", evidence_strength=None, allow_hypothesis=True
        )


def cast_client(fake: FakeAnalysis) -> AnalysisClient:
    """The fake satisfies the two methods citations.py uses; this keeps the annotation
    honest without inventing a Protocol that exists only for a test."""
    return fake  # type: ignore[return-value]
