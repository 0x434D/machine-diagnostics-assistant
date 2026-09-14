"""What the two endpoints must answer, and what they must refuse to answer."""

from __future__ import annotations

import pytest
from analysis.config import Settings
from fastapi.testclient import TestClient

from tests.conftest import BOOSTED_SCORE, CURVE_SAMPLES, DEFECT_CLASSES

WINDOW = {"from": "2026-09-12T01:00:00Z", "to": "2026-09-12T02:00:00Z"}


@pytest.mark.usefixtures("seeded_db")
def test_stats_window_is_closed_and_counts_are_exact(client: TestClient) -> None:
    body = client.get("/inspection/stats", params=WINDOW).json()

    assert body["total"] == 600  # one hour at 6 s takt
    assert body["rejects"] == 30  # the fixture's every-20th-part, not the plant's rate


@pytest.mark.usefixtures("seeded_db")
def test_the_breakdown_reads_the_score_vector_rather_than_the_dead_scalar(
    client: TestClient,
) -> None:
    """The endpoint answered `by_defect_class: []` for a whole milestone with no error.

    It grouped by `inspection_results.defect_class`, which the widened inspection event
    stopped filling, so the filter that excluded nulls excluded every row. The counts below
    are what a query over `defect_classes`/`confidences` produces and nothing else does:
    the fixture spreads its 30 rejects five to a class, and gives one of them a second
    class the model also believes it saw — so a breakdown that collapsed to one class per
    part would report 5 for `scratch` instead of 6, and the old query reports nothing at all.
    """
    body = client.get("/inspection/stats", params=WINDOW).json()

    counts = {row["defect_class"]: row["count"] for row in body["by_defect_class"]}
    assert counts == {
        "gap": 5,
        "crack": 5,
        "misalignment": 5,
        "missing_part": 5,
        "scratch": 6,
        "contamination": 5,
    }
    # 31 against 30 rejects. §3.4's scores are independent and do not sum to 1, so the
    # breakdown is not a partition and must not be read as one.
    assert sum(counts.values()) == 31 > body["rejects"]


@pytest.mark.usefixtures("seeded_db")
def test_the_breakdown_says_which_threshold_it_counted_at(client: TestClient) -> None:
    """A count whose meaning lives in a config file is a number the agent would cite as
    if it meant something else."""
    body = client.get("/inspection/stats", params=WINDOW).json()

    assert body["defect_class_threshold"] == Settings().defect_class_threshold
    assert body["defect_class_threshold"] < BOOSTED_SCORE


@pytest.mark.usefixtures("seeded_db_with_unaccounted_rejects")
def test_a_reject_no_class_can_explain_is_counted_rather_than_dropped(
    client: TestClient,
) -> None:
    """Removing the cause of the empty breakdown did not remove the shape.

    Two rejects here are real and neither reaches the group-by: one predates §3.4's vector
    and carries no scores at all, and one is §3.5 scenario 6 — every class present, every
    one of them decayed below the threshold. Before `rejects_without_class`, `rejects` moved
    and the breakdown did not, with nothing in the response saying so; a run where *every*
    reject looked like this would have reported "no defects seen" while the line scrapped
    parts, which is the quietest wrong answer this endpoint can give.
    """
    body = client.get("/inspection/stats", params=WINDOW).json()

    assert body["rejects"] == 32
    assert body["rejects_without_class"] == 2
    # The breakdown is unmoved by either row, which is exactly why the count beside it has
    # to exist: the two together account for every reject, and neither reads alone.
    counts = {row["defect_class"]: row["count"] for row in body["by_defect_class"]}
    assert sum(counts.values()) == 31


@pytest.mark.usefixtures("seeded_db")
def test_every_reject_in_an_ordinary_window_is_accounted_for(
    client: TestClient,
) -> None:
    """The other direction, and the one that makes the number above falsifiable: on a window
    of rows the plant can actually produce, nothing is unaccounted for. A `NOT EXISTS` that
    matched everything would pass the test above and fail this one."""
    body = client.get("/inspection/stats", params=WINDOW).json()

    assert body["rejects"] == 30
    assert body["rejects_without_class"] == 0


@pytest.mark.usefixtures("seeded_db")
def test_a_good_parts_low_scores_are_not_defects_it_has(client: TestClient) -> None:
    """A good part carries all six classes scored low, not an absent vector (§3.4). Every
    one of those rows is in the window, so a breakdown that counted classes rather than
    classes above the threshold would report 600-odd of each."""
    body = client.get("/inspection/stats", params=WINDOW).json()

    assert all(row["count"] < 10 for row in body["by_defect_class"])


@pytest.mark.usefixtures("seeded_db")
def test_the_window_excludes_its_upper_bound(client: TestClient) -> None:
    """Half-open, so consecutive windows neither double-count a part nor lose one."""
    first = client.get(
        "/inspection/stats",
        params={"from": "2026-09-12T01:00:00Z", "to": "2026-09-12T01:30:00Z"},
    ).json()
    second = client.get(
        "/inspection/stats",
        params={"from": "2026-09-12T01:30:00Z", "to": "2026-09-12T02:00:00Z"},
    ).json()

    assert first["total"] + second["total"] == 600


@pytest.mark.usefixtures("seeded_db_with_gap")
def test_stats_reports_gaps_rather_than_hiding_them(client: TestClient) -> None:
    """§4.4: without gap markers, missing data is indistinguishable from a quiet machine,
    and the agent will confidently describe a stop that was a blackout."""
    assert client.get("/inspection/stats", params=WINDOW).json()["coverage"]["gaps"]


@pytest.mark.usefixtures("seeded_db")
def test_a_window_with_no_gap_says_so_rather_than_omitting_coverage(
    client: TestClient,
) -> None:
    """The field is always present. An absent coverage key and an empty one would be
    indistinguishable to the agent, and one of them means "nobody looked"."""
    body = client.get("/inspection/stats", params=WINDOW).json()

    assert "coverage" in body
    assert body["coverage"]["gaps"] == []


@pytest.mark.usefixtures("seeded_db")
def test_a_serial_answers_with_every_section_of_its_history(
    client: TestClient,
) -> None:
    """§14's line, as one response: genealogy, what each station recorded, the verdict and
    the disposition."""
    body = client.get("/parts/A-00000007").json()

    assert body["assembly_serial"] == "A-00000007"
    assert body["created_at"] is not None
    assert body["carrier_id"] is not None
    assert [row["component_serial"] for row in body["genealogy"]] == [
        "C1-00000007",
        "C2-00000007",
    ]
    assert {row["signal"] for row in body["process_values"]} == {
        "PeakForce",
        "JoiningDistance",
    }
    assert len(body["process_curves"][0]["samples"]) == CURVE_SAMPLES
    assert body["inspection"]["result"] == "reject"
    assert body["disposition"]["disposition"] == "reject"


@pytest.mark.usefixtures("seeded_db")
def test_the_genealogy_carries_the_supplier_lot_each_component_came_from(
    client: TestClient,
) -> None:
    """§3.5 scenario 7 resolves to a component lot, so the lot has to be reachable from the
    part rather than from a second query the agent has to know to make.

    The two parts below differ only in when they were made: lane 1 changed lot halfway
    through the window, and lane 2 did not.
    """
    early = client.get("/parts/A-00000007").json()["genealogy"]
    late = client.get("/parts/A-00000407").json()["genealogy"]

    assert [row["lot_code"] for row in early] == ["LOT-A1", "LOT-B1"]
    assert [row["lot_code"] for row in late] == ["LOT-A2", "LOT-B1"]
    assert {row["supplier"] for row in early} == {"Acme", "Borealis"}


@pytest.mark.usefixtures("seeded_db")
def test_the_verdict_carries_every_class_the_classifier_scored(
    client: TestClient,
) -> None:
    """Not the one that won. §3.5's scenarios 4 and 5 each turn on two classes being high
    on one part, and scenario 6 on all six falling together."""
    inspection = client.get("/parts/A-00000007").json()["inspection"]

    assert inspection["defect_classes"] == list(DEFECT_CLASSES)
    assert len(inspection["confidences"]) == len(DEFECT_CLASSES)
    high = [
        name
        for name, score in zip(
            inspection["defect_classes"], inspection["confidences"], strict=True
        )
        if score >= Settings().defect_class_threshold
    ]
    assert high == ["gap", "scratch"]
    # §3.4: six independent scores, not a distribution. The measured cost of reading them
    # as one was a good part reported as 27 % confident and ~30 % misaligned, so a
    # normalisation introduced anywhere between the classifier and here has to fail.
    assert sum(inspection["confidences"]) != pytest.approx(1.0)


@pytest.mark.usefixtures("seeded_db")
def test_unknown_serial_is_404_not_an_empty_object(client: TestClient) -> None:
    """Citation verification (§6.5) depends on this distinction."""
    assert client.get("/parts/A-99999999").status_code == 404


@pytest.mark.usefixtures("seeded_db")
def test_only_rejects_expose_an_image_url(client: TestClient) -> None:
    assert client.get("/parts/A-00000007").json()["inspection"]["image_url"] is not None
    assert client.get("/parts/A-00000006").json()["inspection"]["image_url"] is None


@pytest.mark.usefixtures("seeded_db")
def test_a_good_parts_image_is_404_rather_than_an_empty_body(
    client: TestClient,
) -> None:
    assert client.get("/parts/A-00000006/image").status_code == 404


@pytest.mark.usefixtures("seeded_db")
def test_a_rejects_image_comes_back_as_png_bytes(client: TestClient) -> None:
    response = client.get("/parts/A-00000007/image")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content.startswith(b"\x89PNG")
