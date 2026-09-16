"""§6.1 stage 1: the question, read into the fixed set of types the rest of the pipeline
is written against.

The type is not decoration. It is the `question_types` facet routing selects a procedure
from (§6.2) and the class §8.1 scores against, so a question read as the wrong type is a
wrong investigation carried out correctly — which is harder to notice than a wrong answer.
Two rules follow, and both are structural rather than asked for in a prompt:

**Nothing outside the set.** The model answers against a schema whose enum is §6.1's eight
types. A reply naming anything else is not repaired into the nearest neighbour; it falls
back to `knowledge` and the answer says the question could not be classified.

**Nothing the line does not have reaches routing.** A question naming station S9 or defect
class `warping` has named something that does not exist, and §6.7 is explicit that the
answer is to correct it rather than to ask about it. The value is kept out of the facets —
where it would match nothing and say nothing — and kept in `unknown`, where the correction
is built from it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from knowledge.documents import (
    DEFECT_CLASSES,
    DIMENSIONS,
    QUESTION_TYPES,
    STATIONS,
    Facets,
    is_alarm_code,
)

from agent.provider import Provider

FALLBACK_TYPE = "knowledge"
"""§6.1: "An unclassifiable question falls back to `knowledge` with a caveat rather than
guessing." Knowledge because it is the one type that needs no window, no tool and no
assumption about the plant — the most it can be wrong about is which document it read."""

#: The closed vocabularies a question may name things from, by the front-matter key each
#: one belongs to. Alarm codes are deliberately absent: §4.1 adds them as the plant grows,
#: so an unrecognised code is news rather than a mistake, and `Facets` checks its shape.
VOCABULARY: Mapping[str, frozenset[str]] = {
    "defect_classes": DEFECT_CLASSES,
    "dimensions": DIMENSIONS,
    "stations": STATIONS,
}


@dataclass(frozen=True)
class Reading:
    """One way the question can be read, and the investigation it would lead to."""

    question_type: str
    description: str


@dataclass(frozen=True)
class Unknown:
    """Something the question named that the line does not have."""

    key: str
    value: str


@dataclass(frozen=True)
class Classification:
    question_type: str
    facets: Facets
    time_phrase: str = ""
    singular: bool = False
    readings: tuple[Reading, ...] = ()
    ranked: bool = True
    fallback: bool = False
    unknown: tuple[Unknown, ...] = ()

    @property
    def ask_back(self) -> bool:
        """§6.7's rule, with both halves and nothing else.

        > Ask back only when the plausible readings lead to materially different
        > investigations **and** no reading is clearly more likely.

        "Materially different investigation" is not a judgement call here: the question
        type is what selects the procedure and the tools, so two readings differ materially
        exactly when they are of different types. "No reading is clearly more likely" is the
        model's to say — `ranked` is its claim that it could pick one, and when it can, §6.7
        says to assume that one and put the alternative in a caveat instead.
        """
        return not self.ranked and len({r.question_type for r in self.readings}) > 1


CLASSIFY_TOOL: dict[str, object] = {
    "name": "classify",
    "description": (
        "Read the question. Say which of the fixed question types it is, quote the time "
        "expression it contains if it contains one, and name the stations, defect classes, "
        "dimensions and alarm codes it mentions. If the question can be read in more than "
        "one way, list every reading; set ranked to true and put the most likely reading "
        "first if one is clearly more likely, and to false only if none is."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "question_type": {
                "type": "string",
                "enum": sorted(QUESTION_TYPES),
                "description": "The kind of question this is.",
            },
            "time_phrase": {
                "type": "string",
                "description": (
                    "The time expression exactly as the question words it — 'last night', "
                    "'this week'. Empty if the question names no time at all. Do not "
                    "compute what it means; that is done against the shift calendar."
                ),
            },
            "singular": {
                "type": "boolean",
                "description": (
                    "True if the question is about one occurrence — 'why did the line "
                    "stand' rather than 'which stops were there'."
                ),
            },
            "stations": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Stations the question names, e.g. S2.",
            },
            "defect_classes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Defect classes the question names.",
            },
            "dimensions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Dimensions the question is about: carrier, lane, lot, "
                "defect_class, time.",
            },
            "alarm_codes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Alarm codes the question names, e.g. A-207.",
            },
            "readings": {
                "type": "array",
                "description": "Every way the question can plausibly be read.",
                "items": {
                    "type": "object",
                    "properties": {
                        "question_type": {
                            "type": "string",
                            "enum": sorted(QUESTION_TYPES),
                        },
                        "description": {"type": "string"},
                    },
                },
            },
            "ranked": {
                "type": "boolean",
                "description": (
                    "True if one reading is clearly more likely than the others."
                ),
            },
        },
        "required": ["question_type"],
    },
}


async def classify(provider: Provider, question: str) -> Classification:
    """Stage 1, as the model call §6.1 describes it.

    A reply that is not the `classify` tool call is not repaired: nothing was classified,
    so the fallback is what ships, and the caveat says so.
    """
    reply = await provider.call(
        SYSTEM, [{"role": "user", "content": question}], [CLASSIFY_TOOL]
    )
    call = next((c for c in reply.tool_calls if c.name == CLASSIFY_TOOL["name"]), None)
    if call is None:
        return classification_of({})
    return classification_of(call.arguments)


SYSTEM = (
    "You read maintenance and quality questions about one production line and classify "
    "them. You do not answer them here and you do not compute what a time expression "
    "means. Use the classify tool and nothing else."
)


def classification_of(arguments: Mapping[str, object]) -> Classification:
    """The model's reply, checked against the vocabulary rather than trusted."""
    raw = arguments.get("question_type")
    question_type = raw if isinstance(raw, str) and raw in QUESTION_TYPES else None

    readings = _readings(arguments.get("readings"))
    facets, unknown = _facets(arguments, question_type or FALLBACK_TYPE)

    return Classification(
        question_type=question_type or FALLBACK_TYPE,
        facets=facets,
        time_phrase=str(arguments.get("time_phrase") or "").strip(),
        singular=bool(arguments.get("singular", False)),
        readings=readings,
        ranked=bool(arguments.get("ranked", True)),
        fallback=question_type is None,
        unknown=unknown,
    )


def corrections(unknown: Sequence[Unknown]) -> list[str]:
    """§6.7 row three, as sentences: name what the line does have.

    Answered from the closed vocabulary the front-matter is validated against rather than
    from a query, because the set is fixed by §3.1 and §3.4 — asking the database which
    stations exist would make the correction depend on whether anything had reported
    lately, and "the line has no S2 today" is a worse answer than no answer.
    """
    out: list[str] = []
    for entry in unknown:
        vocabulary = VOCABULARY.get(entry.key)
        if vocabulary is None:
            # An alarm code: open-ended by §4.1, so there is no list to offer — only the
            # shape it failed, which is still a correction rather than a question.
            out.append(
                f"{entry.value!r} is not an alarm code; codes read A-101, A-207 and so on. "
                f"The answer below ignores it."
            )
            continue
        noun = entry.key.replace("_", " ").rstrip("s")
        out.append(
            f"This line has no {noun} {entry.value!r}; its {entry.key.replace('_', ' ')} "
            f"are {', '.join(sorted(vocabulary))}. The answer below ignores it."
        )
    return out


def _readings(value: object) -> tuple[Reading, ...]:
    if not isinstance(value, list):
        return ()
    out: list[Reading] = []
    for entry in value:
        if not isinstance(entry, dict):
            continue
        item = entry
        question_type = item.get("question_type")
        if isinstance(question_type, str) and question_type in QUESTION_TYPES:
            out.append(
                Reading(
                    question_type=question_type,
                    description=str(item.get("description", "")),
                )
            )
    return tuple(out)


def _facets(
    arguments: Mapping[str, object], question_type: str
) -> tuple[Facets, tuple[Unknown, ...]]:
    unknown: list[Unknown] = []

    def known(key: str) -> frozenset[str]:
        vocabulary = VOCABULARY[key]
        named = _strings(arguments.get(key))
        kept = frozenset(value for value in named if value in vocabulary)
        unknown.extend(
            Unknown(key=key, value=value) for value in sorted(set(named) - kept)
        )
        return kept

    stations = known("stations")
    defect_classes = known("defect_classes")
    dimensions = known("dimensions")

    named_codes = _strings(arguments.get("alarm_codes"))
    codes = frozenset(value for value in named_codes if is_alarm_code(value))
    unknown.extend(
        Unknown(key="alarm_codes", value=value)
        for value in sorted(set(named_codes) - codes)
    )

    facets = Facets(
        question_types=frozenset({question_type}),
        defect_classes=defect_classes,
        dimensions=dimensions,
        stations=stations,
        alarm_codes=codes,
    )
    return facets, tuple(unknown)


def _strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [entry for entry in value if isinstance(entry, str)]
