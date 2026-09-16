"""§6.2's routing: deterministic, from the documents' own front-matter.

The three tests this module exists for are the `always_load` guarantee, the absence of a
routing table in code, and the budget. Everything else here supports one of those.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from agent.routing import (
    DEFAULT_BUDGET,
    RetrievalBudget,
    procedures,
    route,
    specificity,
)
from knowledge.documents import (
    QUESTION_TYPES,
    Facets,
    KnowledgeError,
    KnowledgeIndex,
    load,
)

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
    budget = RetrievalBudget(documents=100, characters=8_000)

    selection = route(loaded, asked, budget)

    retrieved = [d for d in selection.documents if not d.always_load]
    assert sum(d.size for d in retrieved) <= budget.characters
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


# --- the procedure for the question's own type, reserved ---


def test_the_procedure_for_the_question_type_survives_a_budget_the_detail_would_fill() -> (
    None
):
    """The failure §6.2 names, one level below `always_load`.

    Measured before the reservation existed: for this question the procedure ranked
    seventh on specificity, behind three alarm documents, a workcell document and two
    pattern documents. At a budget of three it was the first thing lost — and a stop
    investigation answered without the stop procedure is an improvisation that sounds
    exactly as confident as the real thing.
    """
    loaded = load(KNOWLEDGE)
    asked = Facets(
        question_types=frozenset({"stop_investigation"}),
        stations=frozenset({"S2"}),
        alarm_codes=frozenset({"A-207"}),
    )

    ranked_only = [
        document.id
        for document in loaded.documents
        if not document.always_load
        and specificity(document.applies_to, asked)
        > specificity(loaded.by_id["SOP-01"].applies_to, asked)
    ]
    assert len(ranked_only) >= 3, "the premise of this test no longer holds"

    selection = route(loaded, asked, RetrievalBudget(documents=3, characters=24_000))

    assert "SOP-01" in selection.ids
    assert "SOP-01" in {document.id for document in selection.reserved}


def test_the_procedure_survives_a_budget_of_one_document() -> None:
    loaded = load(KNOWLEDGE)

    selection = route(
        loaded,
        Facets(question_types=frozenset({"traceability"})),
        RetrievalBudget(documents=1, characters=1),
    )

    assert "SOP-04" in selection.ids


def test_every_investigation_type_gets_its_procedure_at_the_tightest_budget() -> None:
    """One per investigation type, and none of the seven left without one."""
    loaded = load(KNOWLEDGE)
    tightest = RetrievalBudget(documents=1, characters=1)

    for question_type in sorted(QUESTION_TYPES - {"knowledge"}):
        selection = route(
            loaded, Facets(question_types=frozenset({question_type})), tightest
        )
        assert selection.reserved, question_type


def test_a_direct_knowledge_question_reserves_nothing_and_still_gets_the_method() -> (
    None
):
    """The one type §6.2 answers by free-text search rather than by a procedure.

    There is no document written for "what does this word mean", and reserving a slot for
    one that does not exist would be a silent no-op. What must still hold is the level
    above: the method arrives regardless.
    """
    loaded = load(KNOWLEDGE)

    selection = route(
        loaded,
        Facets(question_types=frozenset({"knowledge"})),
        RetrievalBudget(documents=1, characters=1),
    )

    assert selection.reserved == ()
    assert {"CORE-01", "CORE-02"} <= set(selection.ids)


def test_the_reservation_comes_from_the_front_matter_and_not_from_a_list() -> None:
    """A document that declares a question type *and* something else is not a procedure."""
    loaded = load(KNOWLEDGE)

    reserved = procedures(
        loaded, Facets(question_types=frozenset({"stop_investigation"}))
    )

    assert [document.id for document in reserved] == ["SOP-01"]
    for document in reserved:
        assert document.applies_to.declares_only_question_types


def test_the_reserved_procedure_is_placed_after_the_method_and_before_the_detail() -> (
    None
):
    loaded = load(KNOWLEDGE)

    selection = route(loaded, Facets(question_types=frozenset({"status"})))

    assert selection.ids[:3] == ("CORE-01", "CORE-02", "SOP-03")


def test_the_reservation_consumes_the_budget_rather_than_adding_to_it() -> None:
    """Reserved, not free: it takes a slot, so the ranked remainder gets one fewer."""
    loaded = load(KNOWLEDGE)
    asked = Facets(
        question_types=frozenset({"quality_investigation"}),
        defect_classes=frozenset({"misalignment"}),
    )

    selection = route(loaded, asked, RetrievalBudget(documents=5, characters=100_000))

    retrieved = [d for d in selection.documents if not d.always_load]
    assert len(retrieved) == 5
    assert len(selection.reserved) == 1


# --- the guards that are inert against today's tree ---


def _tree(tmp_path: Path, **documents: str) -> KnowledgeIndex:
    for name, text in documents.items():
        (tmp_path / f"{name}.md").write_text(text)
    return load(tmp_path)


def test_an_always_load_document_that_also_declares_scope_is_not_selected_twice(
    tmp_path: Path,
) -> None:
    """The `always_load` guard in the ranking filter, made live.

    Inert against the tree as it stands, because the two method documents declare no
    `applies_to` at all and so score zero anyway — remove the guard and every routing test
    still passes. The day a method document declares scope, it would be selected once
    guaranteed and once ranked, and the duplicate would eat a retrieval slot with nothing
    failing. This is the test that would notice.
    """
    index = _tree(
        tmp_path,
        method=(
            "---\nid: CORE-99\ntitle: Method\nalways_load: true\n"
            "applies_to:\n  question_types: [status]\n  stations: [S2]\n---\n\nmethod\n"
        ),
        detail=(
            "---\nid: S2\ntitle: A workcell\napplies_to:\n"
            "  stations: [S2]\n  question_types: [status]\n---\n\ndetail\n"
        ),
    )
    asked = Facets(question_types=frozenset({"status"}), stations=frozenset({"S2"}))

    selection = route(index, asked)

    assert selection.ids.count("CORE-99") == 1
    assert "CORE-99" not in {document.id for document in selection.dropped}


def test_an_always_load_document_shaped_like_a_procedure_is_not_reserved_as_well(
    tmp_path: Path,
) -> None:
    """The same guard in `procedures`. It is already in, so reserving it would double it."""
    index = _tree(
        tmp_path,
        method=(
            "---\nid: CORE-99\ntitle: Method\nalways_load: true\n"
            "applies_to:\n  question_types: [status]\n---\n\nmethod\n"
        ),
        procedure=(
            "---\nid: SOP-99\ntitle: A procedure\napplies_to:\n"
            "  question_types: [status]\n---\n\nprocedure\n"
        ),
    )
    asked = Facets(question_types=frozenset({"status"}))

    selection = route(index, asked)

    assert [document.id for document in procedures(index, asked)] == ["SOP-99"]
    assert selection.ids.count("CORE-99") == 1


def test_the_character_property_measures_what_the_cap_governs() -> None:
    """`always_load` is exempt from the budget, so the total is not the budgeted size."""
    loaded = load(KNOWLEDGE)
    budget = RetrievalBudget(documents=4, characters=DEFAULT_BUDGET.characters)

    selection = route(
        loaded, Facets(question_types=frozenset({"stop_investigation"})), budget
    )

    method = sum(document.size for document in loaded.always_load)
    assert selection.budgeted_characters <= budget.characters
    assert selection.total_characters == selection.budgeted_characters + method
    assert method > 0
