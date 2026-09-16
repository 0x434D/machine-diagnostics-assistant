"""§6.5: every cited id is resolved against the database before the answer ships.

A citation that cannot be opened is, in the spec's words, barely a citation — so an id that
does not resolve is removed rather than shipped, and the answer says that it was.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from agent.answer import Answer, Finding
from agent.tools import AnalysisClient


@dataclass
class VerificationResult:
    failed_ids: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed_ids


async def verify(answer: Answer, analysis: AnalysisClient) -> VerificationResult:
    result = VerificationResult()
    for finding in answer.findings:
        for citation in finding.citations:
            if not await analysis.part_exists(citation.id):
                result.failed_ids.append(citation.id)
    return result


async def keep(
    findings: Sequence[Finding], analysis: AnalysisClient
) -> tuple[list[Finding], list[str]]:
    """The findings whose citations all resolve, and the note about the ones removed.

    §6.5 gives the model one retry before this runs. What it must never do is ship the
    claim quietly: the removal is visible in the answer, and countable in the trace, so the
    frequency of the failure is measurable rather than anecdotal.

    Findings rather than an assembled answer, because §6.1 composes *after* this: a claim
    stripped from `findings` but left standing in `answer_markdown` is the removal made
    invisible again, in the one field the reader actually reads.
    """
    failed: set[str] = set()
    for finding in findings:
        for citation in finding.citations:
            if not await analysis.part_exists(citation.id):
                failed.add(citation.id)
    if not failed:
        return list(findings), []

    kept = [
        finding
        for finding in findings
        if not any(citation.id in failed for citation in finding.citations)
    ]
    removed = len(findings) - len(kept)
    note = (
        f"{removed} claim(s) were removed because their citations could not be verified "
        f"against the database: {', '.join(sorted(failed))}."
    )
    return kept, [note]


async def strip(answer: Answer, analysis: AnalysisClient) -> Answer:
    """`keep`, applied to an answer that has already been composed."""
    kept, notes = await keep(answer.findings, analysis)
    if not notes:
        return answer
    return answer.model_copy(
        update={"findings": kept, "caveats": [*answer.caveats, *notes]}
    )
