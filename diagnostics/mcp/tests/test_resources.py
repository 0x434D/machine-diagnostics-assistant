"""The knowledge base as resources — the half of §6.11 that makes this more than a data API.

Run against the real tree in `knowledge/`, because the resources §6.11 exposes *are* those
documents. A fixture tree would assert that a fixture is addressable.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from knowledge.documents import KnowledgeBase
from mcp_server import resources


def test_every_document_in_the_tree_is_addressable(knowledge: KnowledgeBase) -> None:
    """Adding diagnostic competence means adding a file (§6.2), and a file nothing can
    reach is competence an external agent does not have."""
    listing = resources.resources_for(knowledge.index)

    assert len(listing) == len(knowledge.index.documents)
    assert len({resource.uri for resource in listing}) == len(listing)


@pytest.mark.parametrize(
    "uri",
    [
        "sop://SOP-01",
        "defect://misalignment",
        "pattern://DP-02",
        "station://S2",
        "alarm://A-207",
    ],
)
def test_the_scheme_knowledge_readme_documents_resolves(
    knowledge: KnowledgeBase, uri: str
) -> None:
    """`knowledge/README.md` fixes these five by example. They are the contract an external
    agent is handed, so they are asserted literally rather than derived here too."""
    result = resources.read(knowledge.index, uri)

    assert result.contents[0].uri == uri


def test_the_always_loaded_core_documents_are_reachable_too(
    knowledge: KnowledgeBase,
) -> None:
    """The README names five schemes and the tree has six directories; `core/` is the one
    it omits.

    CORE-01 and CORE-02 are the method and the evidence rules, and §6.2 says the method
    never arrives through retrieval. An external agent that could read every procedure and
    not the method those procedures are written against would have the runbooks and not the
    discipline — which is the failure §6.2 spends its length on.
    """
    for document in knowledge.index.always_load:
        uri = resources.uri_for(document)
        assert uri.startswith("core://")
        assert resources.read(knowledge.index, uri).contents


def test_a_listing_says_which_documents_are_always_loaded(
    knowledge: KnowledgeBase,
) -> None:
    listing = {
        resource.uri: resource.description or ""
        for resource in resources.resources_for(knowledge.index)
    }
    always = {resources.uri_for(d) for d in knowledge.index.always_load}

    assert always
    for uri, description in listing.items():
        assert ("always loaded" in description) is (uri in always), uri


def test_an_id_under_the_wrong_scheme_resolves_to_nothing(
    knowledge: KnowledgeBase,
) -> None:
    """`sop://misalignment` naming a defect document would let a caller believe it had read
    a procedure. The id exists; the resource does not."""
    with pytest.raises(resources.ResourceError):
        resources.read(knowledge.index, "sop://misalignment")


def test_an_unknown_uri_resolves_to_nothing(knowledge: KnowledgeBase) -> None:
    with pytest.raises(resources.ResourceError, match="SOP-99"):
        resources.read(knowledge.index, "sop://SOP-99")


def test_a_resource_carries_the_document_body_and_not_its_front_matter(
    knowledge: KnowledgeBase,
) -> None:
    """The front-matter is routing metadata. Handing it to a reading agent as though it
    were prose would spend context on a vocabulary it has no use for."""
    text = resources.read(knowledge.index, "sop://SOP-01").contents[0]

    assert "applies_to" not in getattr(text, "text", "")


def test_a_document_added_to_the_tree_is_addressable_without_a_restart(
    tmp_path: Path,
) -> None:
    """§6.2 hot-reloads, and a listing that needed a restart would make "add a file" false
    for exactly the person tuning the SOPs."""
    (tmp_path / "sops").mkdir()
    base = KnowledgeBase(tmp_path)
    assert resources.resources_for(base.index) == []

    (tmp_path / "sops" / "SOP-99-new.md").write_text(
        "---\nid: SOP-99\ntitle: A procedure added while running\n---\n\nStep one.\n"
    )

    listing = resources.resources_for(base.reload_if_changed())
    assert [resource.uri for resource in listing] == ["sop://SOP-99"]
