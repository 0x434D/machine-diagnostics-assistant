"""§5.3's `/knowledge/{id}`, which is what makes a `sop` citation openable (§7.2).

No database and no container: this endpoint reads Markdown from the repository, and §5.2
has no `docs` table on purpose.
"""

from __future__ import annotations

from analysis.app import app
from auth.testing import mint
from fastapi.testclient import TestClient

# A `user` token, because §10.5 puts reading a cited procedure in both columns of its matrix
# and these tests are about the document, not about who may see it. `conftest.py` configures
# the key this was minted against.
client = TestClient(app, headers={"Authorization": f"Bearer {mint(role='user')}"})


def test_a_procedure_can_be_opened_by_the_id_a_citation_carries() -> None:
    response = client.get("/knowledge/SOP-01")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "SOP-01"
    assert body["title"] == "Investigating a line stop"
    assert body["applies_to"]["question_types"] == ["stop_investigation"]
    assert "A line stop is defined by **output**" in body["body"]


def test_the_served_body_carries_no_front_matter() -> None:
    """The front-matter is routing metadata; it is served as fields, not as prose."""
    body = client.get("/knowledge/DP-02").json()

    assert not body["body"].startswith("---")
    assert "id: DP-02" not in body["body"]
    assert sorted(body["applies_to"]["defect_classes"]) == ["misalignment", "scratch"]


def test_every_kind_of_id_in_the_resource_vocabulary_resolves() -> None:
    """§6.2's resource keys: `sop://SOP-01`, `defect://crack`, `pattern://DP-02`,
    `station://S2`, `alarm://A-207`. All five are the same id space and the same route."""
    for document_id in ("SOP-01", "crack", "DP-02", "S2", "A-207", "CORE-01"):
        assert client.get(f"/knowledge/{document_id}").status_code == 200, document_id


def test_an_id_that_does_not_exist_is_a_404_and_not_an_empty_document() -> None:
    """§6.5: citation verification rests on the two being distinguishable."""
    response = client.get("/knowledge/SOP-99")

    assert response.status_code == 404


def test_the_always_load_flag_is_visible_on_the_document_it_belongs_to() -> None:
    assert client.get("/knowledge/CORE-01").json()["always_load"] is True
    assert client.get("/knowledge/SOP-01").json()["always_load"] is False
