"""§6.2's knowledge base, read off disk.

These run against the real `knowledge/` tree rather than a fixture one, because the claim
being tested is about *those* documents: "adding diagnostic competence means adding a file"
is only true if every file already there is readable, unique and routable. A fixture tree
would pass while the real one was broken, which is the one outcome that matters.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from knowledge.documents import (
    DEFAULT_ROOT,
    QUESTION_TYPES,
    Document,
    Facets,
    KnowledgeBase,
    KnowledgeError,
    load,
    parse,
    search,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
KNOWLEDGE = REPOSITORY_ROOT / "knowledge"


def test_the_default_root_is_the_repositorys_knowledge_tree() -> None:
    assert DEFAULT_ROOT == KNOWLEDGE


def test_every_document_parses() -> None:
    index = load(KNOWLEDGE)

    assert index.documents, "the knowledge tree produced no documents at all"
    for document in index.documents:
        assert document.id
        assert document.title
        assert document.body.strip(), f"{document.path} has front-matter and no body"


def test_every_markdown_file_with_front_matter_is_indexed() -> None:
    """The count is read off the tree rather than written down.

    A test that asserts "30" fails the day a thirty-first document is added, which trains
    whoever added it to edit the number. This one fails only when a file that declares
    itself a document did not reach the index — which is the failure worth catching.
    """
    declared = {
        path for path in KNOWLEDGE.rglob("*.md") if path.read_text().startswith("---\n")
    }

    assert {document.path for document in load(KNOWLEDGE).documents} == declared


def test_every_id_is_unique() -> None:
    index = load(KNOWLEDGE)

    assert len(index.by_id) == len(index.documents)


def test_a_duplicate_id_is_a_startup_failure(tmp_path: Path) -> None:
    (tmp_path / "one.md").write_text("---\nid: X-1\ntitle: One\n---\n\nbody\n")
    (tmp_path / "two.md").write_text("---\nid: X-1\ntitle: Two\n---\n\nbody\n")

    with pytest.raises(KnowledgeError, match="X-1"):
        load(tmp_path)


def test_a_file_without_front_matter_is_ignored() -> None:
    """knowledge/README.md states the rule and is itself the case it describes."""
    readme = KNOWLEDGE / "README.md"

    assert parse(readme.read_text(), readme) is None
    assert readme not in {document.path for document in load(KNOWLEDGE).documents}


def test_the_two_core_documents_carry_always_load() -> None:
    index = load(KNOWLEDGE)

    flagged = {document.path for document in index.always_load}
    assert flagged == set((KNOWLEDGE / "core").glob("*.md"))
    assert len(flagged) == 2


def test_no_document_outside_core_carries_always_load() -> None:
    for document in load(KNOWLEDGE).documents:
        if document.always_load:
            assert document.path.parent.name == "core", document.path


def test_front_matter_becomes_the_index_entry() -> None:
    index = load(KNOWLEDGE)
    document = index.by_id["DP-02"]

    assert document.title == "Misalignment and scratch concentrated on one carrier"
    assert document.applies_to.defect_classes == frozenset({"misalignment", "scratch"})
    assert document.applies_to.dimensions == frozenset({"carrier"})
    assert document.applies_to.question_types == frozenset({"quality_investigation"})
    assert document.applies_to.stations == frozenset()
    assert document.body.startswith("# DP-02")
    assert "---" not in document.body.splitlines()[0]


def test_an_unknown_front_matter_key_is_a_failure(tmp_path: Path) -> None:
    """A key nobody reads is a claim the document makes that nothing honours."""
    (tmp_path / "doc.md").write_text(
        "---\nid: X-1\ntitle: One\napplies_too: yes\n---\n\nbody\n"
    )

    with pytest.raises(KnowledgeError, match="applies_too"):
        load(tmp_path)


def test_an_unknown_applies_to_key_is_a_failure(tmp_path: Path) -> None:
    (tmp_path / "doc.md").write_text(
        "---\nid: X-1\ntitle: One\napplies_to:\n  question_type: [status]\n---\n\nbody\n"
    )

    with pytest.raises(KnowledgeError, match="question_type"):
        load(tmp_path)


def test_a_misspelt_question_type_is_a_failure(tmp_path: Path) -> None:
    """The failure this whole loader exists to make loud.

    `stop_investigations` routes nothing, ever, and nothing else in the system would ever
    say so: the document simply never arrives and the answer is written without it.
    """
    (tmp_path / "doc.md").write_text(
        "---\nid: X-1\ntitle: One\napplies_to:\n"
        "  question_types: [stop_investigations]\n---\n\nbody\n"
    )

    with pytest.raises(KnowledgeError, match="stop_investigations"):
        load(tmp_path)


def test_a_document_with_front_matter_and_no_id_is_a_failure(tmp_path: Path) -> None:
    (tmp_path / "doc.md").write_text("---\ntitle: One\n---\n\nbody\n")

    with pytest.raises(KnowledgeError, match="id"):
        load(tmp_path)


def test_an_unclosed_front_matter_block_is_a_failure(tmp_path: Path) -> None:
    (tmp_path / "doc.md").write_text("---\nid: X-1\ntitle: One\n\nbody\n")

    with pytest.raises(KnowledgeError):
        load(tmp_path)


def test_an_empty_applies_to_list_is_a_failure(tmp_path: Path) -> None:
    """A declared key with nothing in it reads as scope and carries none."""
    (tmp_path / "doc.md").write_text(
        "---\nid: X-1\ntitle: One\napplies_to:\n  stations: []\n---\n\nbody\n"
    )

    with pytest.raises(KnowledgeError):
        load(tmp_path)


def test_facets_reject_a_value_outside_the_vocabulary() -> None:
    with pytest.raises(KnowledgeError, match="S9"):
        Facets(stations=frozenset({"S9"}))


def test_a_reload_swaps_the_index_rather_than_mutating_it(tmp_path: Path) -> None:
    """§6.2: an SOP edit must not require a restart, and a reload mid-question is safe."""
    document = tmp_path / "doc.md"
    document.write_text("---\nid: X-1\ntitle: One\n---\n\nbefore\n")
    base = KnowledgeBase(tmp_path)

    held = base.index  # what a question in flight is holding
    document.write_text("---\nid: X-1\ntitle: One\n---\n\nafter\n")
    reloaded = base.reload()

    assert held is not reloaded
    assert held.by_id["X-1"].body.strip() == "before"
    assert reloaded.by_id["X-1"].body.strip() == "after"
    assert base.index is reloaded


def test_an_index_a_question_holds_is_unaffected_by_a_later_edit(
    tmp_path: Path,
) -> None:
    document = tmp_path / "doc.md"
    document.write_text("---\nid: X-1\ntitle: One\n---\n\nbefore\n")
    base = KnowledgeBase(tmp_path)
    held = base.index

    (tmp_path / "second.md").write_text("---\nid: X-2\ntitle: Two\n---\n\nnew\n")
    document.unlink()
    base.reload()

    assert [d.id for d in held.documents] == ["X-1"]
    assert [d.id for d in base.index.documents] == ["X-2"]


def test_reload_if_changed_notices_an_edit_and_otherwise_does_not_reload(
    tmp_path: Path,
) -> None:
    document = tmp_path / "doc.md"
    document.write_text("---\nid: X-1\ntitle: One\n---\n\nbefore\n")
    base = KnowledgeBase(tmp_path)

    assert base.reload_if_changed() is base.index
    first = base.index

    document.write_text("---\nid: X-1\ntitle: One\n---\n\nafter\n")
    second = base.reload_if_changed()

    assert second is not first
    assert second.by_id["X-1"].body.strip() == "after"


def test_free_text_search_finds_a_document_by_its_words() -> None:
    """§6.2's other path: direct knowledge questions, never the procedure."""
    index = load(KNOWLEDGE)

    hits = search(index, "contamination", limit=3)

    assert hits
    assert "contamination" in {document.id for document in hits}


def test_free_text_search_returns_nothing_for_words_that_are_not_there() -> None:
    index = load(KNOWLEDGE)

    assert search(index, "hydraulic accumulator preload", limit=3) == ()


def test_documents_are_immutable() -> None:
    document: Document = load(KNOWLEDGE).documents[0]

    with pytest.raises(AttributeError):
        document.title = "something else"  # type: ignore[misc]


def test_a_title_holding_a_colon_survives_the_parser(tmp_path: Path) -> None:
    """One of the things a real YAML parser is for.

    Nothing in the tree needs it today, and the day one document does, the failure of a
    hand-rolled reader would be a truncated title in an answer rather than an error.
    """
    (tmp_path / "doc.md").write_text(
        '---\nid: X-1\ntitle: "S2: the press did not reach home"\n---\n\nbody\n'
    )

    assert load(tmp_path).by_id["X-1"].title == "S2: the press did not reach home"


def test_always_load_must_be_a_boolean_and_not_a_word_that_looks_like_one(
    tmp_path: Path,
) -> None:
    (tmp_path / "doc.md").write_text(
        "---\nid: X-1\ntitle: One\nalways_load: maybe\n---\n\nbody\n"
    )

    with pytest.raises(KnowledgeError, match="always_load"):
        load(tmp_path)


def test_an_applies_to_value_that_is_not_a_list_is_a_failure(tmp_path: Path) -> None:
    (tmp_path / "doc.md").write_text(
        "---\nid: X-1\ntitle: One\napplies_to:\n  stations: S2\n---\n\nbody\n"
    )

    with pytest.raises(KnowledgeError, match="stations"):
        load(tmp_path)


def test_exactly_the_procedures_declare_a_question_type_and_nothing_else() -> None:
    """The reading of the front-matter that `routing` reserves a slot on.

    §6.2 sizes `sops/` as "one per investigation type", and a document that names a
    question type while making no claim about any workcell, class, dimension or alarm code
    is saying it is about the kind of question rather than about anything in it. That this
    picks out the procedures and only the procedures is a property of the tree, so it is
    asserted here rather than assumed in the router.
    """
    index = load(KNOWLEDGE)

    procedures = {
        document.path.parent.name
        for document in index.documents
        if document.applies_to.declares_only_question_types
    }

    assert procedures == {"sops"}
    assert len(
        [d for d in index.documents if d.applies_to.declares_only_question_types]
    ) == len(list((KNOWLEDGE / "sops").glob("*.md")))


def test_every_question_type_has_a_procedure_except_the_one_that_is_not_an_investigation() -> (
    None
):
    """Seven of §6.1's eight types have a procedure. `knowledge` has none, on purpose.

    A direct knowledge question — *"what does contamination mean?"* — is not an
    investigation and has no procedure to run; §6.2 sends exactly that case to free-text
    search instead, which is the one path allowed to miss. Asserted rather than assumed,
    both directions: if a procedure for `knowledge` is ever added this fails and the
    routing reservation picks it up, and if one of the other seven loses its procedure
    this fails before a question of that type is answered without it.
    """
    index = load(KNOWLEDGE)

    covered: set[str] = set()
    for document in index.documents:
        if document.applies_to.declares_only_question_types:
            covered |= document.applies_to.question_types

    assert covered == QUESTION_TYPES - {"knowledge"}


def test_a_root_that_does_not_exist_is_a_startup_failure(tmp_path: Path) -> None:
    """The failure that defeats every guard above it, and the reason it is not obvious.

    `Path.rglob` on a directory that is not there yields nothing rather than raising. So a
    typo in `AGENT_KNOWLEDGE_ROOT`, an image that stopped copying the documents, or a root
    pointed one level too deep produces an index with no documents in it — and `route`
    then returns nothing at all, `always_load` included. Every question is answered with no
    method, no procedure and no error anywhere.

    §6.2's failed retrieval silently becoming a failed method, moved one level up to where
    the flag, the reservation and the budget exemption cannot see it.
    """
    with pytest.raises(KnowledgeError, match="does not exist"):
        load(tmp_path / "typo")


def test_a_root_that_is_a_file_is_a_startup_failure(tmp_path: Path) -> None:
    document = tmp_path / "doc.md"
    document.write_text("---\nid: X-1\ntitle: One\n---\n\nbody\n")

    with pytest.raises(KnowledgeError, match="not a directory"):
        load(document)


def test_a_root_holding_no_documents_is_a_startup_failure(tmp_path: Path) -> None:
    """An empty knowledge base is not a valid state for this system.

    Distinct from the missing root above and worth its own refusal: a root that exists and
    is empty is what a wrong-but-plausible path looks like — one directory too deep, or the
    parent of the tree instead of the tree.
    """
    (tmp_path / "README.md").write_text("# not a document\n")

    with pytest.raises(KnowledgeError, match="no knowledge documents"):
        load(tmp_path)
