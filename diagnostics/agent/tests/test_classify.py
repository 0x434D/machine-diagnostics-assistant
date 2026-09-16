"""§6.1 stage 1: classification into a fixed set, and the two things it must never do.

It must never guess a type that is not in the set, and it must never silently invent one
for a question it could not read — that falls back to `knowledge` *with a caveat*, because
the type drives routing (§6.2) and evaluation (§8.1) and a wrong one is a wrong
investigation carried out correctly.
"""

from __future__ import annotations

import pytest
from agent.classify import (
    CLASSIFY_TOOL,
    FALLBACK_TYPE,
    Classification,
    Unknown,
    classification_of,
    corrections,
)
from knowledge.documents import QUESTION_TYPES


def _schema() -> dict[str, object]:
    schema = CLASSIFY_TOOL["input_schema"]
    assert isinstance(schema, dict)
    properties = schema["properties"]
    assert isinstance(properties, dict)
    return properties


def test_the_tool_constrains_the_model_to_exactly_the_fixed_set() -> None:
    """§6.1's set is fixed. The constraint is in the schema the model answers against, not
    in a sentence in the prompt asking it nicely."""
    question_type = _schema()["question_type"]
    assert isinstance(question_type, dict)

    assert set(question_type["enum"]) == QUESTION_TYPES


@pytest.mark.parametrize("question_type", sorted(QUESTION_TYPES))
def test_every_type_in_the_set_survives_classification(question_type: str) -> None:
    classification = classification_of({"question_type": question_type})

    assert classification.question_type == question_type
    assert not classification.fallback


def test_a_type_outside_the_set_falls_back_to_knowledge_with_a_caveat() -> None:
    """Never a guess. A model that answers `maintenance_planning` has said something the
    rest of the pipeline cannot route, and inventing the nearest neighbour would be a
    wrong investigation carried out correctly."""
    classification = classification_of({"question_type": "maintenance_planning"})

    assert classification.question_type == FALLBACK_TYPE
    assert classification.fallback


def test_no_type_at_all_falls_back_the_same_way() -> None:
    classification = classification_of({})

    assert classification.question_type == FALLBACK_TYPE
    assert classification.fallback


def test_the_question_type_is_the_routing_facet() -> None:
    """Stage 1 drives stage 4: the classification *is* the `question_types` facet."""
    classification = classification_of({"question_type": "stop_investigation"})

    assert classification.facets.question_types == frozenset({"stop_investigation"})


def test_facets_the_line_does_not_have_are_kept_out_and_named() -> None:
    """§6.7: "refers to something that does not exist — correct it and name what does".

    The unknown value must not reach routing (it would match nothing and say nothing) and
    must not be dropped silently either, because the correction is the answer.
    """
    classification = classification_of(
        {"question_type": "status", "stations": ["S2", "S9"]}
    )

    assert classification.facets.stations == frozenset({"S2"})
    assert Unknown(key="stations", value="S9") in classification.unknown


def test_a_correction_names_what_the_line_does_have() -> None:
    sentences = corrections((Unknown(key="stations", value="S9"),))

    assert len(sentences) == 1
    assert "S9" in sentences[0]
    for station in ("S1", "S2", "S3", "S4"):
        assert station in sentences[0]


# --- §6.7's rule, both halves ---------------------------------------------------------


def _readings(*pairs: tuple[str, str], ranked: bool) -> Classification:
    return classification_of(
        {
            "question_type": pairs[0][0],
            "readings": [
                {"question_type": question_type, "description": description}
                for question_type, description in pairs
            ],
            "ranked": ranked,
        }
    )


def test_asking_back_needs_both_halves_of_the_rule() -> None:
    """§6.7: ask only when the readings lead to materially different investigations *and*
    no reading is clearly more likely."""
    classification = _readings(
        ("stop_investigation", "the line stood still"),
        ("quality_investigation", "parts were scrapped"),
        ranked=False,
    )

    assert classification.ask_back


def test_a_ranked_reading_is_assumed_rather_than_asked_about() -> None:
    """The mirror case §8.1 lists as its own evaluation class: looks ambiguous, is not.
    Asking here is the failure, not the caution."""
    classification = _readings(
        ("quality_investigation", "parts were scrapped"),
        ("stop_investigation", "the line stood still"),
        ranked=True,
    )

    assert not classification.ask_back
    assert classification.question_type == "quality_investigation"


def test_two_readings_of_the_same_kind_are_not_materially_different() -> None:
    """Two readings that lead to the *same* investigation are not a reason to ask: the
    work to do is identical either way."""
    classification = _readings(
        ("quality_investigation", "scrap at S3"),
        ("quality_investigation", "scrap at S4"),
        ranked=False,
    )

    assert not classification.ask_back
