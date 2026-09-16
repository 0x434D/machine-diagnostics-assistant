"""§6.2's routing. Deterministic, and derived entirely from the documents' front-matter.

> There is no routing table in code.

Taken literally: nothing in this module names a document, a directory or a filename, and
`tests/test_routing.py` reads this file and fails if it finds one. The only vocabulary here
is the five front-matter keys, which live in `knowledge.documents` because they are also
what a document is validated against.

Four things happen, in this order, and the order is the design:

1. **Everything flagged `always_load` is in, unconditionally and first.** It is not scored,
   not ranked and not evicted by the budget. §6.2: *"the method never arrives through
   search — a failed retrieval must not silently become a failed method."* A budget that
   could evict the method would be that same failure with a number in front of it.
2. **The procedure for the question's own type is reserved a place**, on the same reasoning
   one level down. `always_load` protects *how to work in general*; it does not protect the
   procedure for **this** kind of question, and under budget pressure a ranking by
   specificity keeps the supporting detail and drops the method — measured, before this
   existed: for a stop question naming a workcell and an alarm code, the procedure ranked
   seventh of eight and would have been the first thing lost. Which document that is comes
   from the front-matter and not from here: see `procedures`.
3. **What is left is scored by specificity** — see `specificity` for the definition and
   why it is that one.
4. **The ranked remainder is truncated to the budget**, and what did not fit is returned
   rather than dropped. §6.2 calls the budget a correctness measure and not an economy,
   because context dilution degrades these systems; a truncation nobody can see is a
   retrieval miss with extra steps.
"""

from __future__ import annotations

from dataclasses import dataclass

from knowledge.documents import FACET_KEYS, Document, Facets, KnowledgeIndex


@dataclass(frozen=True)
class RetrievalBudget:
    """§6.2's cap, which §10.3 makes configuration: a count and a total size.

    Both defaults are read against the tree as it stands — a retrieved document averages
    just under 2.5 kB, so eight of them is a little under 20 kB, and the character cap
    binds first only when the selection runs long on the larger prose. The dial belongs in
    `Settings` beside the tool budget; it is defaulted here for the same reason
    `analysis.config` reads its numbers off the modules that define them, so that there is
    one number and an environment variable that moves it rather than two that agree today.

    Neither cap applies to `always_load`. Reserved documents *consume* the budget — they
    take a slot and their size counts — but are never evicted by it.
    """

    documents: int = 8
    characters: int = 24_000


DEFAULT_BUDGET = RetrievalBudget()


@dataclass(frozen=True)
class Selection:
    """What routing chose, and what it had to leave out.

    `reserved` is called out separately because §6.5 checks a cited procedure against what
    routing actually loaded, and "was it loaded" and "was it guaranteed" are different
    questions about the same answer.
    """

    documents: tuple[Document, ...]
    reserved: tuple[Document, ...] = ()
    dropped: tuple[Document, ...] = ()

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(document.id for document in self.documents)

    @property
    def budgeted_characters(self) -> int:
        """The size of what the character cap actually governs.

        Named apart from the total because `always_load` is exempt from the budget, so
        `total_characters <= budget.characters` is a comparison between two different
        things and fails by roughly the size of the method documents. This is the one to
        compare against `RetrievalBudget.characters`.
        """
        return sum(
            document.size for document in self.documents if not document.always_load
        )

    @property
    def total_characters(self) -> int:
        """Everything the question will carry, exempt documents included."""
        return sum(document.size for document in self.documents)


def route(
    index: KnowledgeIndex,
    asked: Facets,
    budget: RetrievalBudget = DEFAULT_BUDGET,
) -> Selection:
    """The documents a question gets, from the documents' own declarations.

    `asked` is the question in the same vocabulary the front-matter uses: its type from
    stage 1, and whatever else it names. A question that declares nothing still gets the
    `always_load` documents, which is the whole point of the flag.
    """
    reserved = procedures(index, asked)
    reserved_ids = {document.id for document in reserved}

    ranked = sorted(
        (
            (specificity(document.applies_to, asked), document)
            for document in index.documents
            if not document.always_load and document.id not in reserved_ids
        ),
        key=lambda scored: (-scored[0], scored[1].id),
    )
    matched = [document for score, document in ranked if score > 0]

    room = budget.documents - len(reserved)
    characters = budget.characters - sum(document.size for document in reserved)

    kept: list[Document] = []
    size = 0
    cut = len(matched)
    for position, document in enumerate(matched):
        if len(kept) >= room or size + document.size > characters:
            cut = position
            break
        kept.append(document)
        size += document.size

    return Selection(
        documents=(*index.always_load, *reserved, *kept),
        reserved=reserved,
        dropped=tuple(matched[cut:]),
    )


def procedures(index: KnowledgeIndex, asked: Facets) -> tuple[Document, ...]:
    """The documents that declare themselves the procedure for this question's type.

    Read off the front-matter, not decided here: a document that names a question type and
    makes **no** claim about any workcell, defect class, dimension or alarm code is saying
    it is about the *kind* of question rather than about anything the question mentions.
    §6.2 sizes that directory as "one per investigation type", and the test suite asserts
    that this reading picks out those documents and only those — which is a property of the
    tree and is checked there rather than assumed here.

    The alternative, weighting the question-type key above the other four, was refused for
    the reason `specificity` gives: there is nothing in the front-matter to calibrate a
    weight from, so any number chosen would be arbitrary. And a weight would only make the
    procedure *likely* to survive the budget. A reservation makes it certain, which is what
    §6.2 asks for — a question of a given kind always gets the document written for that
    kind, the way `always_load` always arrives — and it is a claim the documents make about
    themselves rather than one this module makes about them.
    """
    return tuple(
        document
        for document in index.documents
        if not document.always_load
        and document.applies_to.declares_only_question_types
        and document.applies_to.question_types & asked.question_types
    )


def specificity(declared: Facets, asked: Facets) -> float:
    """How precisely a document's declared scope fits this question.

    The overlap between the two sides of each front-matter key, summed over the five keys:

        Σ  |declared ∩ asked| / |declared ∪ asked|

    with a key contributing nothing when either side is silent about it. Three properties,
    each of which is a decision:

    **Overlap, not a boolean.** A document declaring one workcell and a document declaring
    all four both "match" a question about one of them, and they are not equally about it.
    The denominator is what separates the document written for this case from the document
    written for every case.

    **Summed across keys, not averaged.** A document that applies along three axes at once
    is more specific than one that applies along one, and the sum is what says so. It
    follows that precision can outweigh count — a single exact key scores 1.0 and beats
    three vague ones at a fifth each — and that is intended: a document about exactly this
    is worth more than a document that is vaguely about three things.

    **The five keys weigh the same, because there is nothing to calibrate a difference
    from.** A weight vector would be five numbers with no measurement behind them: the
    front-matter records where a document applies and says nothing about which kind of
    match matters more, and no question in the tree tells you whether an alarm code should
    outrank a question type. Equal weights are the only choice that is not arbitrary. If a
    weighting is ever wanted, the way to earn it is to let the documents declare it — a
    number here would be a guess that outlives the person who guessed.

    Returns 0.0 when nothing matches, which is what keeps an unrelated document out.
    """
    left, right = declared.as_mapping(), asked.as_mapping()
    total = 0.0
    for key in FACET_KEYS:
        union = left[key] | right[key]
        if not union:
            continue
        total += len(left[key] & right[key]) / len(union)
    return total
