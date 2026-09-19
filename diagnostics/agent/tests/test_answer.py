"""§6.3's answer object, and the claims it refuses to carry.

Every rule here is structural — a constructor that raises — rather than a sentence in a
prompt asking the model nicely, because a prompt is not a guarantee and §6.3 says these are
checked. Which is also the honest limit of this file: it proves that a badly formed claim
**cannot ship**, not that a model would not try to make one.
"""

from __future__ import annotations

import pytest
from agent.answer import (
    ALLOW_HYPOTHESIS,
    CITATION_FIELDS,
    WINDOWED,
    Answer,
    Citation,
    CitationWindow,
    Contradiction,
    Finding,
    Method,
    validate_basis,
)

WINDOW = CitationWindow(
    from_ts="2026-09-11T20:00:00Z",  # type: ignore[arg-type]  # pydantic parses the instant
    to_ts="2026-09-12T04:00:00Z",  # type: ignore[arg-type]  # pydantic parses the instant
    label="night shift 2026-09-11 22:00 – 2026-09-12 06:00 Europe/Berlin",
)


def test_the_vocabulary_is_seven_three_minus_what_nothing_renders() -> None:
    """§7.3 lists `chart` and §7.4 describes it. Nothing renders one until M6, and a
    citation with no renderer is a claim with no referent."""
    assert set(CITATION_FIELDS) == {
        "part",
        "stop",
        "alarm",
        "signal",
        "pattern",
        "sop",
        "serial",
        "lot",
        "containment",
    }
    assert "chart" not in CITATION_FIELDS


@pytest.mark.parametrize("kind", sorted(CITATION_FIELDS))
def test_a_citation_without_its_payload_cannot_exist(kind: str) -> None:
    with pytest.raises(ValueError, match="needs"):
        # Parametrised over the vocabulary, so `kind` is a `str` here and a `Kind` in
        # every caller; the model rejects anything outside the enum at construction.
        Citation(kind=kind)  # type: ignore[arg-type]  # the enum is enforced at runtime


def test_a_citation_carrying_another_kinds_payload_is_refused() -> None:
    """It would render one thing and resolve another."""
    with pytest.raises(ValueError, match="remove"):
        Citation(kind="part", id="A-00000007", station="S2")


def test_a_composite_kind_needs_both_halves() -> None:
    with pytest.raises(ValueError, match="signal"):
        Citation(kind="signal", station="S2")


def test_a_citation_labels_itself_for_the_note_that_removes_it() -> None:
    assert Citation(kind="stop", id="s-1").label == "stop:s-1"
    assert (
        Citation(kind="signal", station="S2", signal="JoiningForce").label
        == "signal:S2/JoiningForce"
    )
    assert (
        Citation(kind="pattern", dimension="carrier", key="7").label
        == "pattern:carrier=7"
    )


# --- the flag, and the constraint that survives it -------------------------------------


def test_a_hypothesis_is_allowed_now_that_knowledge_is_routed() -> None:
    """§6.3: `hypothesis` means the claim required interpretation from the knowledge base.
    Routing loads that knowledge and `method.sops_used` records which documents, so an
    interpretation now has something behind it."""
    assert ALLOW_HYPOTHESIS

    finding = Finding(
        statement="Carrier 7 is worn.",
        basis="hypothesis",
        citations=[Citation(kind="pattern", dimension="carrier", key="7")],
        evidence_strength="92 % of misalignment defects on carrier 7, n=214, p<0.001",
    )

    assert finding.basis == "hypothesis"


def test_a_hypothesis_without_evidence_strength_still_raises() -> None:
    """The flag moved; the constraint under it did not. A label claiming interpretation
    without stating what supports it is the invention the flag was guarding."""
    with pytest.raises(ValueError, match="evidence_strength"):
        Finding(statement="Carrier 7 is worn.", basis="hypothesis", citations=[])


def test_evidence_strength_is_checked_for_presence_and_not_for_content() -> None:
    """§6.3 asks the strength to state the support in figures. A check for a digit would
    pass "1 of my hunches" and fail "n = two hundred and fourteen"; whether the figures are
    the right ones is §8.1's hypothesis-labelling class, scored by something that can read
    them."""
    validate_basis(
        basis="hypothesis", evidence_strength="n = 214", allow_hypothesis=True
    )
    with pytest.raises(ValueError, match="evidence_strength"):
        validate_basis(basis="hypothesis", evidence_strength="", allow_hypothesis=True)


def test_a_measured_claim_needs_no_evidence_strength() -> None:
    assert Finding(statement="600 parts.", basis="measured").evidence_strength is None


# --- contradiction -----------------------------------------------------------------------


def test_a_contradiction_states_both_roots_and_the_reasoning() -> None:
    contradiction = Contradiction(
        derived_root="S3", agent_root="S1", reasoning="S1 was starved first."
    )

    assert contradiction.derived_root == "S3"


def test_a_contradiction_without_reasoning_is_invalid() -> None:
    """DP-11: "Never quietly answer with a different root than the one you were handed —
    silent disagreement is indistinguishable from an error." A bare "the root is elsewhere"
    is a second unexplained verdict beside the first."""
    with pytest.raises(ValueError, match="reasoning"):
        Contradiction(derived_root="S3", agent_root="S1", reasoning="   ")


def test_a_contradiction_needs_a_root_to_contradict() -> None:
    with pytest.raises(ValueError, match="derived_root"):
        Contradiction(derived_root="", agent_root="S1", reasoning="because.")


def test_a_contradiction_whose_roots_agree_contradicts_nothing() -> None:
    """The UI renders this field prominently; a disagreement that is not one would be shown
    as one."""
    with pytest.raises(ValueError, match="contradicts nothing"):
        Contradiction(derived_root="S1", agent_root="S1", reasoning="because.")


def test_an_answer_either_asks_or_answers() -> None:
    """§6.7 makes them alternatives. An answer carrying both has investigated a question it
    claimed it could not read."""
    with pytest.raises(ValueError, match="asks back has no findings"):
        Answer(
            findings=[Finding(statement="600 parts.", basis="measured")],
            answer_markdown="…",
            method=Method(),
            # A dict rather than a Clarification, to prove the validator runs on the
            # coerced value and not only on one built in Python.
            clarification={  # type: ignore[arg-type]  # pydantic coerces the mapping
                "question": "which?",
                "readings": ["a", "b"],
            },
        )


# --- §7.3's window ---------------------------------------------------------------------

#: The payload each windowed kind needs beside its window, so the tests below are
#: parametrised over `WINDOWED` rather than naming the two kinds a second time.
CITED: dict[str, dict[str, str]] = {
    "pattern": {"dimension": "carrier", "key": "7"},
    "signal": {"station": "S2", "signal": "JoiningForce"},
}


def test_the_kinds_that_carry_a_window_are_the_ones_whose_endpoints_need_one() -> None:
    """§7.3 writes a window into exactly these two, and both of their endpoints take
    `from` and `to`: the same `carrier=7` is a different cell over a different interval."""
    assert WINDOWED == {"pattern", "signal"}
    assert WINDOWED <= set(CITATION_FIELDS)


@pytest.mark.parametrize("kind", sorted(WINDOWED))
def test_an_answer_cannot_ship_a_windowed_citation_without_its_window(
    kind: str,
) -> None:
    """Checked on the answer rather than on the citation, because the model's own output is
    validated as findings before the pipeline stamps the window onto them. A citation that
    reached the reader without one would open a panel over an interval the screen chose."""
    citation = Citation(kind=kind, **CITED[kind])  # type: ignore[arg-type]  # the enum is enforced at runtime

    with pytest.raises(ValueError, match="carries none"):
        Answer(
            findings=[
                Finding(statement="…", basis="measured", citations=[citation]),
            ],
            answer_markdown="…",
            method=Method(),
        )


@pytest.mark.parametrize("kind", sorted(WINDOWED))
def test_the_same_citation_with_its_window_ships(kind: str) -> None:
    """The other half: a refusal that is not contingent on the window would pass the test
    above while refusing every answer."""
    citation = Citation(kind=kind, window=WINDOW, **CITED[kind])  # type: ignore[arg-type]  # the enum is enforced at runtime

    answer = Answer(
        findings=[Finding(statement="…", basis="measured", citations=[citation])],
        answer_markdown="…",
        method=Method(),
    )

    assert answer.findings[0].citations[0].window == WINDOW


def test_a_kind_that_takes_no_window_is_not_asked_for_one() -> None:
    Answer(
        findings=[
            Finding(
                statement="…",
                basis="measured",
                citations=[Citation(kind="part", id="A-00000007")],
            )
        ],
        answer_markdown="…",
        method=Method(),
    )
