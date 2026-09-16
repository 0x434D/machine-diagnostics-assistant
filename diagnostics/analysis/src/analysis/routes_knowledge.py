"""§5.3's `/knowledge/{id}`: an SOP or catalogue entry, by id.

§7.3 resolves a `sop` citation here — `{ kind: "sop", id: "SOP-01" }` → `GET
/knowledge/SOP-01` — so this is the endpoint that makes a cited procedure openable rather
than merely named. It is the only route in this service that touches no database: §5.2 has
no `docs` table on purpose, because the knowledge base lives as Markdown in the repository
(§6.2), at the top level and deliberately (§10.1).

The loader is the `knowledge` workspace member, which this service, the agent and (§6.11)
the MCP server all depend on and none of which depends on either of the others. A second
parser here would be the copy that agrees today.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, HTTPException
from knowledge.documents import Document, KnowledgeBase

from analysis.dependencies import SettingsDep
from analysis.models import KnowledgeAppliesTo, KnowledgeDocument

router = APIRouter()


@lru_cache(maxsize=1)
def _knowledge(root: Path) -> KnowledgeBase:
    """The tree, read once per process and re-read when it changes.

    Built on the first request rather than at import so that a deployment which has not
    been given the tree still starts and still answers every other §5.3 endpoint. §2.2
    already requires this service to work with the plant shut down; what it cannot do
    should be one endpoint rather than the service.
    """
    return KnowledgeBase(root)


@router.get("/knowledge/{document_id}", operation_id="getKnowledgeDocument")
def get_knowledge_document(
    document_id: str, settings: SettingsDep
) -> KnowledgeDocument:
    """One document by its front-matter id — `SOP-01`, `DP-02`, `A-207`, `S2`, `crack`.

    §6.2 hot-reloads the tree, so an edit made while tuning is visible on the next request
    without a restart. The reload swaps an immutable index; this handler holds the one it
    was given for the whole request.
    """
    index = _knowledge(settings.knowledge_root).reload_if_changed()
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
