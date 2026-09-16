"""§6.2's routing: deterministic, from the documents' own front-matter.

The three tests this module exists for are the `always_load` guarantee, the absence of a
routing table in code, and the budget. Everything else here supports one of those.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from agent.knowledge import Facets, KnowledgeError, load
from agent.routing import DEFAULT_BUDGET, RetrievalBudget, route, specificity

KNOWLEDGE = Path(__file__).resolve().parents[3] / "knowledge"
ROUTING_SOURCE = Path(__file__).resolve().parents[1] / "src" / "agent" / "routing.py"

GENEROUS = RetrievalBudget(documents=100, characters=10_000_000)


# --- the flag that must not fail silently --------------------------------------------------


def test_always_load_documents_arrive_for_a_question_that_matches_nothing() -> None:
    """§6.2's single most common failure of runbook-driven agents, refused structurally."""
    loaded = load(KNOWLEDGE)

    selection = route(loaded, Facets())

    assert selection.documents
    assert {"CORE-01", "CORE-02"} <= set(selection.ids)
    assert set(selection.ids) == {"CORE-01", "CORE-02"}, (
        "an empty question matched a document it has no basis to match"
    )


def test_always_load_survives_a_budget_of_one_document() -> None:
    """The budget is a cap on retrieval, and the method does not arrive by retrieval."""
    loaded = load(KNOWLEDGE)

    selection = route(
        loaded,
        Facets(question_types=frozenset({"stop_investigation"})),
        RetrievalBudget(documents=1, characters=1),
    )

    assert {"CORE-01", "CORE-02"} <= set(selection.ids)


def test_always_load_documents_come_first() -> None:
    """The method is read before what it is applied to, so it is placed that way."""
    loaded = load(KNOWLEDGE)

    selection = route(loaded, Facets(question_types=frozenset({"stop_investigation"})))

    assert selection.ids[:2] == ("CORE-01", "CORE-02")


# --- there is no routing table in code -----------------------------------------------------


def test_the_routing_module_hardcodes_no_document_id() -> None:
    """§6.2: "There is no routing table in code."

    Greps the module's own source for every id the tree actually defines. The vocabulary
    the front-matter is written in — question types, defect classes, dimensions, stations —
    lives in `knowledge.py` and is validation of a closed set the spec fixes, not a mapping
    from a question to a document. Six of those vocabulary words are also document ids, so
    this check would fire on them too if routing ever named one.
    """
    source = ROUTING_SOURCE.read_text()

    hardcoded = sorted(
        document.id
        for document in load(KNOWLEDGE).documents
        if re.search(rf"\b{re.escape(document.id)}\b", source)
    )

    assert hardcoded == [], f"{ROUTING_SOURCE.name} names documents: {hardcoded}"


def test_the_routing_module_names_no_knowledge_subdirectory_either() -> None:
    """A rule keyed on `sops/` or `patterns/` is the same table by another name."""
    source = ROUTING_SOURCE.read_text()

    named = [
        directory.name
        for directory in KNOWLEDGE.iterdir()
        if directory.is_dir() and re.search(rf"\b{directory.name}\b", source)
    ]

    assert named == []


# --- selection from front-matter alone -----------------------------------------------------


def test_a_stop_investigation_routes_the_line_stop_sop() -> None:
    loaded = load(KNOWLEDGE)

    selection = route(loaded, Facets(question_types=frozenset({"stop_investigation"})))

    assert "SOP-01" in selection.ids


def test_a_defect_class_question_routes_that_defects_document() -> None:
    loaded = load(KNOWLEDGE)

    selection = route(
        loaded,
        Facets(
            question_types=frozenset({"quality_investigation"}),
            defect_classes=frozenset({"contamination"}),
        ),
    )

    assert "contamination" in selection.ids
    assert "SOP-02" in selection.ids


def test_an_alarm_code_routes_that_alarms_document() -> None:
    loaded = load(KNOWLEDGE)

    selection = route(
        loaded,
        Facets(
            question_types=frozenset({"stop_investigation"}),
            alarm_codes=frozenset({"A-207"}),
        ),
    )

    assert "A-207" in selection.ids


def test_every_document_in_the_tree_is_reachable() -> None:
    """Adding a file adds competence — or it does not, and this says which.

    Worth more than a count: a document whose front-matter declares nothing routable is
    inert, and nothing else in the system would ever mention it again.
    """
    loaded = load(KNOWLEDGE)

    unreachable = [
        document.id
        for document in loaded.documents
        if not document.always_load
        and document.id not in route(loaded, document.applies_to, GENEROUS).ids
    ]

    assert unreachable == []


def test_a_dimension_alone_routes_the_documents_that_declare_it_and_no_others() -> None:
    loaded = load(KNOWLEDGE)

    selection = route(loaded, Facets(dimensions=frozenset({"lot"})), GENEROUS)

    assert "DP-05" in selection.ids
    assert "SOP-01" not in selection.ids


# --- specificity -------------------------------------------------------------------------


def test_a_narrow_document_outranks_a_broad_one_on_the_same_question() -> None:
    """DP-10 declares every class and every dimension; DP-02 declares the two that fit."""
    loaded = load(KNOWLEDGE)
    asked = Facets(
        question_types=frozenset({"quality_investigation"}),
        defect_classes=frozenset({"misalignment"}),
        dimensions=frozenset({"carrier"}),
    )

    narrow = specificity(loaded.by_id["DP-02"].applies_to, asked)
    broad = specificity(loaded.by_id["DP-10"].applies_to, asked)

    assert narrow > broad


def test_matching_on_more_keys_beats_matching_on_one_at_equal_precision() -> None:
    loaded = load(KNOWLEDGE)
    asked = Facets(
        question_types=frozenset({"quality_investigation"}),
        defect_classes=frozenset({"misalignment"}),
        dimensions=frozenset({"carrier"}),
    )

    three_keys = specificity(loaded.by_id["misalignment"].applies_to, asked)
    one_key = specificity(loaded.by_id["SOP-02"].applies_to, asked)

    assert three_keys > one_key


def test_a_document_that_matches_nothing_scores_zero() -> None:
    loaded = load(KNOWLEDGE)

    assert (
        specificity(
            loaded.by_id["SOP-01"].applies_to,
            Facets(question_types=frozenset({"traceability"})),
        )
        == 0.0
    )


def test_ranking_is_deterministic() -> None:
    loaded = load(KNOWLEDGE)
    asked = Facets(
        question_types=frozenset({"stop_investigation"}),
        stations=frozenset({"S2"}),
        alarm_codes=frozenset({"A-207"}),
    )

    assert route(loaded, asked).ids == route(loaded, asked).ids
    assert route(loaded, asked).ids == route(load(KNOWLEDGE), asked).ids


# --- the budget ----------------------------------------------------------------------------


def test_over_selection_is_truncated_to_the_budget_and_the_rest_is_reported() -> None:
    """§6.2 calls the budget a correctness measure: context dilution degrades these
    systems, so what was left out is reported rather than silently dropped."""
    loaded = load(KNOWLEDGE)
    everything = Facets(
        question_types=frozenset({"quality_investigation"}),
        defect_classes=frozenset(
            {"gap", "crack", "misalignment", "missing_part", "scratch", "contamination"}
        ),
        dimensions=frozenset({"carrier", "lane", "lot", "defect_class", "time"}),
        stations=frozenset({"S1", "S2", "S3", "S4"}),
    )
    budget = RetrievalBudget(documents=4, characters=DEFAULT_BUDGET.characters)

    selection = route(loaded, everything, budget)

    retrieved = [d for d in selection.documents if not d.always_load]
    assert len(retrieved) == 4
    assert selection.dropped
    assert not {d.id for d in retrieved} & {d.id for d in selection.dropped}


def test_truncation_keeps_the_most_specific() -> None:
    loaded = load(KNOWLEDGE)
    asked = Facets(
        question_types=frozenset({"quality_investigation"}),
        defect_classes=frozenset({"misalignment", "scratch"}),
        dimensions=frozenset({"carrier"}),
    )

    full = route(loaded, asked, GENEROUS)
    trimmed = route(loaded, asked, RetrievalBudget(documents=2, characters=100_000))

    retrieved_full = [d.id for d in full.documents if not d.always_load]
    retrieved_trimmed = [d.id for d in trimmed.documents if not d.always_load]
    assert retrieved_trimmed == retrieved_full[:2]


def test_the_character_budget_also_truncates() -> None:
    loaded = load(KNOWLEDGE)
    asked = Facets(question_types=frozenset({"quality_investigation"}))

    selection = route(loaded, asked, RetrievalBudget(documents=100, characters=3_000))

    retrieved = [d for d in selection.documents if not d.always_load]
    assert sum(d.size for d in retrieved) <= 3_000
    assert selection.dropped


def test_the_budget_is_configuration_not_a_literal_in_the_walk() -> None:
    """§10.3: every number is configuration, and the retrieval budget is two of them."""
    assert DEFAULT_BUDGET.documents > 0
    assert DEFAULT_BUDGET.characters > 0
    assert (
        route(load(KNOWLEDGE), Facets()).documents
        == route(load(KNOWLEDGE), Facets(), DEFAULT_BUDGET).documents
    )


def test_routing_refuses_a_facet_value_outside_the_vocabulary() -> None:
    with pytest.raises(KnowledgeError):
        Facets(question_types=frozenset({"stop_investigations"}))
