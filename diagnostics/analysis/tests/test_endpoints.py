"""What the two endpoints must answer, and what they must refuse to answer."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

WINDOW = {"from": "2026-09-12T01:00:00Z", "to": "2026-09-12T02:00:00Z"}


@pytest.mark.usefixtures("seeded_db")
def test_stats_window_is_closed_and_counts_are_exact(client: TestClient) -> None:
    body = client.get("/inspection/stats", params=WINDOW).json()

    assert body["total"] == 600  # one hour at 6 s takt
    assert body["rejects"] == 30  # seeded at 5 %
    assert sum(d["count"] for d in body["by_defect_class"]) == 30


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
def test_part_lookup_reads_the_per_part_record_directly(client: TestClient) -> None:
    """§3.4a: the per-part record is authoritative and is never reconstructed by joining the
    time series on "when was this part at S3"."""
    response = client.get("/parts/A-00000007")

    assert response.status_code == 200
    assert response.json()["assembly_serial"] == "A-00000007"


@pytest.mark.usefixtures("seeded_db")
def test_unknown_serial_is_404_not_an_empty_object(client: TestClient) -> None:
    """Citation verification (§6.5) depends on this distinction."""
    assert client.get("/parts/A-99999999").status_code == 404


@pytest.mark.usefixtures("seeded_db")
def test_only_rejects_expose_an_image_url(client: TestClient) -> None:
    assert client.get("/parts/A-00000007").json()["image_url"] is not None
    assert client.get("/parts/A-00000006").json()["image_url"] is None


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
