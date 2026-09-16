"""§6.2's knowledge base as MCP resources — `sop://SOP-01`, `defect://misalignment`.

This is the half of §6.11 that makes the server more than a data API:

> an external agent can read `sop://SOP-01`, follow it, call the same tools and reach the
> same conclusion. **The method transfers to an agent we did not build.**

`knowledge/README.md` fixes the scheme: the front-matter `id` is the resource key, and the
scheme names the kind of document. The kind is read off the directory the file is in, minus
its plural — `sops/` gives `sop://`, `defects/` gives `defect://` — rather than from a table
here, for the same reason routing has none: adding diagnostic competence has to mean adding a
file, and a mapping in code is a second place that would have to be edited to make a new
directory reachable.

**The README names five schemes and the tree has six directories.** `core/` is not in its
list, and CORE-01 and CORE-02 are precisely the two documents §6.2 says must reach every
question — so leaving them unaddressable would give an external agent every procedure and
not the method those procedures are written against. They are served as `core://CORE-01`,
which is what the directory rule produces, and the gap is reported rather than papered over.
"""

from __future__ import annotations

import re

import mcp_types as types
from knowledge.documents import Document, KnowledgeIndex

MIME_TYPE = "text/markdown"

_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*$")
"""RFC 3986's scheme shape, narrowed to lowercase. A directory that does not produce one is
a directory whose documents would be unreachable, so it raises rather than being skipped."""


class ResourceError(Exception):
    """A document that cannot be addressed, or a URI that addresses nothing."""


def uri_for(document: Document) -> str:
    """This document's resource URI, derived from where it sits and what its id is."""
    scheme = document.path.parent.name.removesuffix("s")
    if not _SCHEME.match(scheme):
        raise ResourceError(
            f"{document.path}: {scheme!r} is not a usable URI scheme, so this document "
            "could never be read over MCP"
        )
    return f"{scheme}://{document.id}"


def resources_for(index: KnowledgeIndex) -> list[types.Resource]:
    """Every document in the tree, as a resource listing.

    `always_load` rides along in the description rather than being hidden: §6.2 calls a
    retrieval miss that silently becomes a missing method the single most common failure of
    runbook-driven agents, and an external agent deciding what to read deserves to be told
    which documents our own pipeline never leaves to retrieval.
    """
    return [
        types.Resource(
            uri=uri_for(document),
            name=document.id,
            title=document.title,
            description=_description(document),
            mime_type=MIME_TYPE,
            size=document.size,
        )
        for document in index.documents
    ]


def read(index: KnowledgeIndex, uri: str) -> types.ReadResourceResult:
    """One document's Markdown, by URI.

    Raises `ResourceError` when nothing is at that URI — including when the id exists under
    a different scheme, because `sop://misalignment` naming a defect document would let a
    caller believe it had read a procedure.
    """
    for document in index.documents:
        if uri_for(document) == uri:
            return types.ReadResourceResult(
                contents=[
                    types.TextResourceContents(
                        uri=uri, mime_type=MIME_TYPE, text=document.body
                    )
                ]
            )
    raise ResourceError(f"no knowledge document at {uri!r}")


def _description(document: Document) -> str | None:
    """What this document claims to apply to, as the front-matter states it.

    `None` rather than an empty string for a document that claims nothing: an empty
    description is a field on the wire that says "described, and the description is nothing".
    """
    facets = document.applies_to.as_mapping()
    applies = ", ".join(
        f"{key}: {' '.join(sorted(values))}" for key, values in facets.items() if values
    )
    parts = ["always loaded" if document.always_load else "", applies]
    return "; ".join(part for part in parts if part) or None
