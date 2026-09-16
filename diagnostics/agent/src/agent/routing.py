"""§6.2's routing. Deterministic, and derived entirely from the documents' front-matter.

> There is no routing table in code.

Taken literally: nothing in this module names a document, a directory or a filename, and
`tests/test_routing.py` reads this file and fails if it finds one. The only vocabulary here
is the five front-matter keys, which live in `knowledge.py` because they are also what a
document is validated against.

Three things happen, in this order, and the order is the design:

1. **Everything flagged `always_load` is in, unconditionally and first.** It is not scored,
   not ranked and not subject to the budget. §6.2: *"the method never arrives through
   search — a failed retrieval must not silently become a failed method."* A budget that
   could evict the method would be that same failure with a number in front of it.
2. **What is left is scored by specificity** — see `specificity` for the definition and
   why it is that one.
3. **The ranked remainder is truncated to the budget**, and what did not fit is returned
   rather than dropped. §6.2 calls the budget a correctness measure and not an economy,
   because context dilution degrades these systems; a truncation nobody can see is a
   retrieval miss with extra steps.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent.knowledge import FACET_KEYS, Document, Facets, KnowledgeIndex


@dataclass(frozen=True)
class RetrievalBudget:
    """§6.2's cap, which §10.3 makes configuration: a count and a total size.

    Both defaults are read against the tree as it stands — a retrieved document averages
    just under 2.5 kB, so eight of them is a little under 20 kB, and the character cap
    binds first only when the selection runs long on the larger prose. The dial belongs in
    `Settings` beside the tool budget; it is defaulted here for the same reason
    `analysis.config` reads its numbers off the modules that define them, so that there is
    one number and an environment variable that moves it rather than two that agree today.

    Neither cap applies to `always_load`.
    """

    documents: int = 8
    characters: int = 24_000


DEFAULT_BUDGET = RetrievalBudget()


@dataclass(frozen=True)
class Selection:
    """What routing chose, and what it had to leave out."""

    documents: tuple[Document, ...]
    dropped: tuple[Document, ...] = ()

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(document.id for document in self.documents)

    @property
    def characters(self) -> int:
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
    ranked = sorted(
        (
            (specificity(document.applies_to, asked), document)
            for document in index.documents
            if not document.always_load
        ),
        key=lambda scored: (-scored[0], scored[1].id),
    )
    matched = [document for score, document in ranked if score > 0]

    kept: list[Document] = []
    size = 0
    cut = len(matched)
    for position, document in enumerate(matched):
        if len(kept) >= budget.documents or size + document.size > budget.characters:
            cut = position
            break
        kept.append(document)
        size += document.size

    return Selection(
        documents=(*index.always_load, *kept),
        dropped=tuple(matched[cut:]),
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

    **The five keys weigh the same.** There is no basis in the front-matter for ranking an
    alarm code above a question type, and a per-key constant would be a routing table by
    another name: a number in this file deciding which documents win. If a weighting is
    ever needed it belongs in the documents, as something they declare about themselves.

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
