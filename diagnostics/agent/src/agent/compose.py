"""§6.1 step 6.5. The composer arranges; it does not author.

> It decides the headline, ordering, which findings lead and which support, which chart
> goes where, what collapses. It may write a summary sentence — but that summary becomes a
> finding itself, inheriting the citations of what it summarises, and passes through
> verification like everything else. Every sentence the user reads traces to a verified
> finding.

Three decisions implement that, and each is a decision rather than a default:

**Which findings lead.** The ones whose citations can be opened. §6.5 asks for everything
to be cited, so a finding that resolves to evidence is the one a reader can check; one that
cannot is support for it. A basis ranking would have been the other candidate and was
rejected: `measured` and `derived` are both facts, and ordering by how they were obtained
sorts by provenance rather than by what the reader needs first.

**What collapses.** Findings that say the same thing collapse to one, and the leading
findings collapse into the summary rather than being printed twice underneath it.

**The summary sentence, and its citations.** It is built out of the statements it
summarises, so the composer adds arrangement and not content; its citations are the union
of theirs; its basis is the *least* certain of theirs, because a summary claiming the
stronger basis would launder the weaker claim; and it is returned in `findings` rather than
only in the prose, so it reaches §7.3's evidence panel as a claim with sources like any
other. It needs no second verification pass: §6.1 verifies before composing, so every
citation the summary inherits has already resolved.
"""

from __future__ import annotations

from collections.abc import Sequence

from agent.answer import Basis, Citation, Finding

_CERTAINTY: dict[Basis, int] = {"measured": 0, "derived": 1, "hypothesis": 2}

SUMMARY_PREFIX = "In short:"


def compose(
    findings: Sequence[Finding],
    caveats: Sequence[str],
    *,
    summary_after: int,
) -> tuple[list[Finding], str]:
    """The findings the answer ships — the summary included — and the prose that renders
    them."""
    ordered = _collapse(findings)
    leads = [finding for finding in ordered if finding.citations] or ordered[:1]
    support = [finding for finding in ordered if finding not in leads]

    summary = _summarise(leads) if len(ordered) >= summary_after else None
    head = [summary] if summary is not None else leads

    lines = [finding.statement for finding in head]
    lines.extend(f"- {finding.statement}" for finding in support)
    lines.extend(f"_{caveat}_" for caveat in caveats)

    shipped = [summary, *leads, *support] if summary is not None else [*leads, *support]
    return shipped, "\n\n".join(lines)


def _collapse(findings: Sequence[Finding]) -> list[Finding]:
    """Two findings that say the same thing are one finding said twice."""
    seen: set[str] = set()
    out: list[Finding] = []
    for finding in findings:
        if finding.statement in seen:
            continue
        seen.add(finding.statement)
        out.append(finding)
    return out


def _summarise(leads: Sequence[Finding]) -> Finding | None:
    if not leads:
        return None
    citations: list[Citation] = []
    for finding in leads:
        for citation in finding.citations:
            if citation not in citations:
                citations.append(citation)

    basis = max((finding.basis for finding in leads), key=lambda b: _CERTAINTY[b])
    strengths = [
        finding.evidence_strength for finding in leads if finding.evidence_strength
    ]
    return Finding(
        statement=f"{SUMMARY_PREFIX} "
        + " ".join(finding.statement for finding in leads),
        basis=basis,
        citations=citations,
        # §6.3 requires it whenever the basis is `hypothesis`, and a summary that dropped
        # it would be the one claim in the answer whose support is not stated.
        evidence_strength="; ".join(strengths) if basis == "hypothesis" else None,
    )
