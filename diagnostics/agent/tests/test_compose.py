"""§6.4: the composer arranges; it does not author.

The one sentence the composer is allowed to write is a summary — and the moment it writes
one, that sentence becomes a finding, inherits the citations of what it summarises, and is
verified like every other claim. These tests are about that rule, because it is the seam
where a composer could start inventing.
"""

from __future__ import annotations

from agent.answer import Citation, Finding
from agent.compose import compose

SEVEN = Citation(kind="part", id="A-00000007")
EIGHT = Citation(kind="part", id="A-00000008")


def _finding(statement: str, *citations: Citation, basis: str = "measured") -> Finding:
    return Finding(
        statement=statement,
        # `str` so the helper can be called with a basis per test; pydantic validates it
        # against `Basis` anyway, so a wrong one fails at construction rather than passing.
        basis=basis,  # type: ignore[arg-type]  # the Literal is enforced at runtime
        citations=list(citations),
    )


def test_a_single_finding_gets_no_summary() -> None:
    """A summary of one sentence is that sentence again."""
    findings, markdown = compose(
        [_finding("600 parts, 30 rejected.")], [], summary_after=2
    )

    assert len(findings) == 1
    assert "600 parts, 30 rejected." in markdown


def test_the_summary_inherits_the_citations_of_what_it_summarises() -> None:
    """§6.4, literally: "that summary becomes a finding itself, inheriting the citations of
    what it summarises"."""
    findings, _markdown = compose(
        [
            _finding("Carrier 7 produced 92 % of the misalignment.", SEVEN, EIGHT),
            _finding("The joining force fell through the shift."),
        ],
        [],
        summary_after=2,
    )

    summary = findings[0]
    assert summary.statement.startswith("In short:")
    assert {citation.id for citation in summary.citations} == {SEVEN.id, EIGHT.id}


def test_the_summary_does_not_repeat_the_finding_it_leads_with() -> None:
    """§6.4's "what collapses": the leading finding is already in the summary sentence, so
    rendering it a second time is noise the reader has to skip."""
    _findings, markdown = compose(
        [
            _finding("Carrier 7 produced 92 % of the misalignment.", SEVEN),
            _finding("The joining force fell through the shift."),
        ],
        [],
        summary_after=2,
    )

    assert markdown.count("Carrier 7 produced 92 % of the misalignment.") == 1
    assert "The joining force fell through the shift." in markdown


def test_the_summary_is_a_finding_and_is_returned_for_verification() -> None:
    """Not a decoration on the markdown: it is in `findings`, so §6.5 resolves its ids and
    strips it like anything else."""
    findings, _markdown = compose(
        [_finding("a", SEVEN), _finding("b")], [], summary_after=2
    )

    assert len(findings) == 3
    assert findings[0].basis == "measured"


def test_the_summary_is_no_more_certain_than_what_it_summarises() -> None:
    """A measured sentence and a derived one summarised together are derived: a summary
    that claimed the stronger basis would launder the weaker claim."""
    findings, _markdown = compose(
        [
            _finding("The line stood for 11 minutes.", SEVEN, basis="derived"),
            _finding("600 parts were inspected.", EIGHT),
        ],
        [],
        summary_after=2,
    )

    assert findings[0].basis == "derived"


def test_every_sentence_in_the_prose_comes_from_a_finding_or_a_caveat() -> None:
    """§6.4: "every sentence the user reads traces to a verified finding". The only prose
    the composer adds of its own is the bullet marker and the caveat's emphasis."""
    findings, markdown = compose(
        [_finding("a statement.", SEVEN), _finding("another statement.")],
        ["a caveat."],
        summary_after=2,
    )

    statements = {finding.statement for finding in findings}
    for line in markdown.splitlines():
        stripped = line.removeprefix("- ").strip()
        if not stripped:
            continue
        assert stripped in statements or stripped == "_a caveat._"


def test_two_findings_that_say_the_same_thing_collapse_to_one() -> None:
    findings, _markdown = compose(
        [_finding("the same sentence."), _finding("the same sentence.")],
        [],
        summary_after=2,
    )

    assert len(findings) == 1


def test_a_cited_finding_leads_and_an_uncited_one_supports() -> None:
    """§6.4 decides "which findings lead and which support". A claim whose evidence can be
    opened leads; one that cannot is support for it."""
    _findings, markdown = compose(
        [_finding("uncited."), _finding("cited.", SEVEN)], [], summary_after=99
    )

    assert markdown.index("cited.") < markdown.index("uncited.")
    assert "- uncited." in markdown
