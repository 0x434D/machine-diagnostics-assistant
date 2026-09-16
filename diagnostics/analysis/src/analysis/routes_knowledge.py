"""§5.3's `/knowledge/{id}`: an SOP or catalogue entry, by id.

§7.3 resolves a `sop` citation here — `{ kind: "sop", id: "SOP-01" }` → `GET
/knowledge/SOP-01` — so this is the endpoint that makes a cited procedure openable rather
than merely named. It is the only route in this service that touches no database: §5.2 has
no `docs` table on purpose, because the knowledge base lives as Markdown in the repository
(§6.2).

**Two things about this module are unfinished, and both are packaging rather than code.**

*It reads the loader from `agent.knowledge`.* The parser, the vocabulary check and the
index are one implementation, and a second one here to avoid the import would be the copy
CLAUDE.md forbids — the copy that agrees today. The direction is at least the right way
round (nothing in `agent` is imported by anything this service is imported by), but
`analysis` does not declare `agent` as a dependency, so an image built with
`--package analysis` does not carry it. Either the loader moves to a workspace member both
stacks' services depend on, or `analysis` declares the dependency. That decision is not
this change's to make.

*The tree has to be reachable at `DEFAULT_ROOT`.* `knowledge/` sits at the repository root
(§10.1, deliberately) and the analysis image is built from `diagnostics/`, so it is not in
the build context and is not mounted. Until it is, this route answers on a developer
machine and in the test suite and nowhere else.

Neither is silent: the import fails at startup and a missing tree fails on the first read.
"""

from __future__ import annotations

from functools import lru_cache

from agent.knowledge import DEFAULT_ROOT, Document, KnowledgeBase
from fastapi import APIRouter, HTTPException

from analysis.models import KnowledgeAppliesTo, KnowledgeDocument

router = APIRouter()


@lru_cache(maxsize=1)
def _knowledge() -> KnowledgeBase:
    """The tree, read once per process and re-read when it changes.

    Built on the first request rather than at import so that a service which does not have
    the tree mounted still starts and still answers every other §5.3 endpoint — the
    diagnostics stack has to work with the plant shut down, and it has to work with the
    knowledge base absent for the same reason: what it cannot do should be one endpoint,
    not the service.
    """
    return KnowledgeBase(DEFAULT_ROOT)


@router.get("/knowledge/{document_id}", operation_id="getKnowledgeDocument")
def get_knowledge_document(document_id: str) -> KnowledgeDocument:
    """One document by its front-matter id — `SOP-01`, `DP-02`, `A-207`, `S2`, `crack`.

    §6.2 hot-reloads the tree, so an edit made while tuning is visible on the next request
    without a restart. The reload swaps an immutable index; this handler holds the one it
    was given for the whole request.
    """
    index = _knowledge().reload_if_changed()
    document = index.by_id.get(document_id)
    if document is None:
        # 404 and not an empty document, for §6.5's reason: citation verification rests on
        # "this id does not resolve" being distinguishable from "this id resolves to
        # nothing", and a model that cited an invented SOP must meet the first.
        raise HTTPException(
            status_code=404, detail=f"no knowledge document with id {document_id!r}"
        )
    return _document(document)


def _document(document: Document) -> KnowledgeDocument:
    applies_to = document.applies_to
    return KnowledgeDocument(
        id=document.id,
        title=document.title,
        always_load=document.always_load,
        applies_to=KnowledgeAppliesTo(
            question_types=sorted(applies_to.question_types),
            defect_classes=sorted(applies_to.defect_classes),
            dimensions=sorted(applies_to.dimensions),
            stations=sorted(applies_to.stations),
            alarm_codes=sorted(applies_to.alarm_codes),
        ),
        body=document.body,
    )
