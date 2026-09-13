"""§6.5: every cited id is resolved against the database before the answer ships.

A citation that cannot be opened is, in the spec's words, barely a citation — so an id that
does not resolve is removed rather than shipped, and the answer says that it was.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent.answer import Answer
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


async def strip(answer: Answer, analysis: AnalysisClient) -> Answer:
    """Removes findings whose citations do not resolve and records that it happened.

    §6.5 gives the model one retry before this runs. What it must never do is ship the
    claim quietly: the removal is visible in the answer, and countable in the trace, so the
    frequency of the failure is measurable rather than anecdotal.
    """
    failed = set((await verify(answer, analysis)).failed_ids)
    if not failed:
        return answer

    kept = [
        finding
        for finding in answer.findings
        if not any(citation.id in failed for citation in finding.citations)
    ]
    removed = len(answer.findings) - len(kept)

    return answer.model_copy(
        update={
            "findings": kept,
            "caveats": [
                *answer.caveats,
                (
                    f"{removed} claim(s) were removed because their citations could not "
                    f"be verified against the database: {', '.join(sorted(failed))}."
                ),
            ],
        }
    )
