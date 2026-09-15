"""`/coverage`, and the guarantee that `/inspection/stats` answers with the same one.

`test_coverage.py` proves the arithmetic of clipping and merging. What is left here is the
distinction the endpoint exists for — *no data because the line was quiet* against *no data
because ingest was down* — and the claim that the two exposures of coverage are one
implementation rather than two that happen to agree today.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

WINDOW = {"from": "2026-09-12T01:00:00Z", "to": "2026-09-12T02:00:00Z"}
QUIET_WINDOW = {"from": "2026-09-12T05:00:00Z", "to": "2026-09-12T06:00:00Z"}
"""An hour the fixture puts nothing in at all, and covers completely."""


@pytest.mark.usefixtures("seeded_db")
def test_a_window_with_no_gaps_is_fully_covered(client: TestClient) -> None:
    body = client.get("/coverage", params=WINDOW).json()

    assert body["gaps"] == []
    assert body["covered_fraction"] == 1.0
    assert body["fully_covered"] is True


@pytest.mark.usefixtures("seeded_db_with_gap")
def test_a_gap_costs_the_window_exactly_its_own_length(client: TestClient) -> None:
    """Five minutes missing out of sixty. The number matters rather than the boolean: a
    caller deciding whether to answer at all reads the fraction."""
    body = client.get("/coverage", params=WINDOW).json()

    assert body["covered_fraction"] == pytest.approx(1.0 - 300 / 3600)
    assert body["fully_covered"] is False
    assert [gap["reason"] for gap in body["gaps"]] == ["plant_unreachable"]


@pytest.mark.usefixtures("seeded_db")
def test_a_quiet_line_and_an_ingest_outage_are_not_the_same_empty_answer(
    client: TestClient,
) -> None:
    """**The distinction the endpoint exists for.**

    Both windows below hold no rows. One of them holds none because nothing happened, and
    the other because nothing was recorded, and an answer that could not tell them apart
    would let the agent report a line as running quietly through the hour its gateway was
    down. `fully_covered` separates them and `observed.events` is what makes the first one
    legible as emptiness rather than as an unread window.
    """
    quiet = client.get("/coverage", params=QUIET_WINDOW).json()

    assert quiet["observed"]["events"] == 0
    assert quiet["observed"]["from_ts"] is None
    assert quiet["fully_covered"] is True


@pytest.mark.usefixtures("seeded_db_with_gap")
def test_an_outage_is_visible_as_an_outage_and_not_as_a_quiet_hour(
    client: TestClient,
) -> None:
    body = client.get("/coverage", params=WINDOW).json()

    assert body["fully_covered"] is False
    assert body["observed"]["events"] > 0


@pytest.mark.usefixtures("seeded_db_with_gap")
def test_the_stats_coverage_is_the_same_object_the_coverage_endpoint_returns(
    client: TestClient,
) -> None:
    """One implementation, two exposures.

    M2b folded coverage into `/inspection/stats` so that §6.1's step-3 check could not be
    skipped by forgetting a second call, and M3 split `/coverage` out for the agent that
    wants to ask before it answers. Both are true only while these two bodies are equal —
    two implementations would agree on the day they were written and diverge on the first
    edit to either.
    """
    standalone = client.get("/coverage", params=WINDOW).json()
    folded = client.get("/inspection/stats", params=WINDOW).json()["coverage"]

    assert standalone == folded


@pytest.mark.usefixtures("seeded_db")
def test_a_backwards_window_is_refused_rather_than_answered_empty(
    client: TestClient,
) -> None:
    """A window that runs backwards is always a caller bug, and answering it with an empty
    result would hide one — the caller reads zero parts and reports a stopped line."""
    response = client.get(
        "/coverage", params={"from": WINDOW["to"], "to": WINDOW["from"]}
    )

    assert response.status_code == 422


@pytest.mark.usefixtures("seeded_db")
def test_a_timestamp_without_an_offset_is_refused(client: TestClient) -> None:
    """ "2026-09-12T01:00:00" from Berlin and from Tokyo are nine hours apart. Assuming UTC
    would answer a question nobody asked, in a way nothing downstream could detect."""
    response = client.get(
        "/coverage", params={"from": "2026-09-12T01:00:00", "to": "2026-09-12T02:00:00"}
    )

    assert response.status_code == 422
