"""`/stops` and `/stops/{id}`: the wiring, the ids, and §5.4's chain over HTTP.

`test_stops.py` proves the detection and `test_propagation.py` proves the walk. What is left
is everything between them and the wire: that the query layer reads far enough back for the
walk to reach the root, that a stop id is stable and resolves on its own, and that 404, 422
and an empty list are three different answers.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from analysis.config import Settings
from fastapi.testclient import TestClient

from tests.conftest import STOP_FROM, STOP_TO, WINDOW_START

WINDOW = {"from": "2026-09-12T01:00:00Z", "to": "2026-09-12T02:00:00Z"}
STOP_ID = "stop-20260912T013000.000000Z"
"""The id of the seeded stop, written out rather than computed.

Spelled literally because §6.5 verifies cited ids against the database and an id is only
worth anything if it is the same string every time — a test that derived it from the same
function the endpoint uses would pass however that function changed.
"""


@pytest.mark.usefixtures("seeded_db")
def test_a_line_that_never_stopped_reports_no_stops(client: TestClient) -> None:
    """The base fixture is a part every 6 s for an hour. Nothing in it is a stop, and
    nothing in it is a micro-stop either: an ordinary takt is neither."""
    body = client.get("/stops", params=WINDOW).json()

    assert body["stops"] == []
    assert body["micro_stops"] == 0
    assert body["truncated"] is False


@pytest.mark.usefixtures("seeded_db_with_a_stop")
def test_the_stop_is_bounded_by_the_two_parts_that_left(client: TestClient) -> None:
    """Five minutes with no output, between the part at 01:30 and the part at 01:35.

    Both flags are false and that is the assertion beside the duration: a stop reported as
    300 s because the window closed 300 s into it is a different claim from one that ended
    after 300 s, and only these say which was meant.
    """
    body = client.get("/stops", params=WINDOW).json()

    assert len(body["stops"]) == 1
    stop = body["stops"][0]
    assert stop["from_ts"] == STOP_FROM.isoformat().replace("+00:00", "Z")
    assert stop["to_ts"] == STOP_TO.isoformat().replace("+00:00", "Z")
    assert stop["duration_seconds"] == 300.0
    assert stop["started_before_window"] is False
    assert stop["open_at_window_end"] is False


@pytest.mark.usefixtures("seeded_db_with_a_stop")
def test_the_id_is_the_instant_the_line_stopped_and_not_a_row_number(
    client: TestClient,
) -> None:
    """§6.5 verifies every cited id against the database, so an id meaning "the first stop
    in the window you asked about" would name a different stop the moment the window moved.
    The two windows below overlap on this stop and nothing else, and they must agree."""
    whole_hour = client.get("/stops", params=WINDOW).json()["stops"][0]
    later_half = client.get(
        "/stops",
        params={"from": "2026-09-12T01:20:00Z", "to": "2026-09-12T01:40:00Z"},
    ).json()["stops"][0]

    assert whole_hour["id"] == STOP_ID
    assert later_half["id"] == STOP_ID


@pytest.mark.usefixtures("seeded_db_with_a_stop")
def test_a_stop_id_resolves_without_being_told_which_window_found_it(
    client: TestClient,
) -> None:
    body = client.get(f"/stops/{STOP_ID}").json()

    assert body["stop"]["id"] == STOP_ID
    assert body["stop"]["duration_seconds"] == 300.0
    assert body["stop"]["open_at_window_end"] is False


@pytest.mark.usefixtures("seeded_db_with_a_stop")
def test_the_chain_walks_back_from_the_outfeed_to_the_press(client: TestClient) -> None:
    """§5.4's worked example, end to end.

    S4 starves because B3_4 ran empty, B3_4 ran empty because S3 starved, S3 starved because
    B2_3 ran empty, and B2_3 ran empty because S2 aborted. The category is derived from
    where the chain stopped — a cause candidate inside the line — and never assigned.
    """
    body = client.get(f"/stops/{STOP_ID}").json()
    derivation = body["derivation"]

    assert [link["station"] for link in derivation["links"]] == ["S4", "S3", "S2"]
    assert [link["buffer"] for link in derivation["links"]] == ["B3_4", "B2_3", None]
    assert [link["state"] for link in derivation["links"]] == [
        "Suspended",
        "Suspended",
        "Aborted",
    ]
    assert derivation["termination"] == "cause_candidate"
    assert derivation["category"] == "internal"
    assert derivation["unexplained"] is None
    assert [episode["station"] for episode in derivation["cause_candidates"]] == ["S2"]


@pytest.mark.usefixtures("seeded_db_with_a_stop")
def test_each_link_names_when_its_buffer_reached_the_condition(
    client: TestClient,
) -> None:
    """The instant a buffer *became* empty and stayed so, which is what the station above it
    has to account for — not the last sample before the walk looked."""
    links = client.get(f"/stops/{STOP_ID}").json()["derivation"]["links"]

    assert links[0]["buffer_condition_since"] == "2026-09-12T01:29:54Z"
    assert links[1]["buffer_condition_since"] == "2026-09-12T01:29:35Z"
    assert links[2]["buffer_condition_since"] is None


@pytest.mark.usefixtures("seeded_db_with_a_stop")
def test_the_root_is_not_the_first_station_to_raise_an_alarm(
    client: TestClient,
) -> None:
    """**The circularity guard, at the HTTP boundary.**

    The earliest alarm in this fixture is at S4 — the tail, downstream of everything, and
    90 s before the root's own alarm. §3.3 and `004_m2c.sql` both warn that reading the
    alarms back is circular, and two of M2c's eight scenarios raise none at all. The alarms
    are returned beside the derivation and the derivation is computed without them, so the
    chain still ends at S2.
    """
    body = client.get(f"/stops/{STOP_ID}").json()

    earliest = min(body["alarms"], key=lambda alarm: alarm["raised_at"])
    assert earliest["station"] == "S4"
    assert body["derivation"]["links"][-1]["station"] == "S2"


@pytest.mark.usefixtures("seeded_db_with_a_stop")
def test_the_timeline_carries_every_station_and_closes_its_bars(
    client: TestClient,
) -> None:
    """Shaped for a Gantt: one bar per episode, per station, with an end.

    The transitions that ended the stop are stamped at exactly the stop's end, so a read
    that stopped a microsecond earlier would leave every bar open and the recovery
    invisible. The closed `Suspended` bar below is what proves the read reaches them.
    """
    body = client.get(f"/stops/{STOP_ID}").json()
    timeline = body["timeline"]

    assert {episode["station"] for episode in timeline} == {"S1", "S2", "S3", "S4"}
    suspended = [
        episode
        for episode in timeline
        if episode["station"] == "S4" and episode["state"] == "Suspended"
    ]
    assert len(suspended) == 1
    assert suspended[0]["to_ts"] == STOP_TO.isoformat().replace("+00:00", "Z")
    assert suspended[0]["reason"] == "starved:B3_4"
    assert suspended[0]["reason_buffer"] == "B3_4"


@pytest.mark.usefixtures("seeded_db_with_a_stop")
def test_the_history_read_reaches_further_back_than_the_stop(
    client: TestClient,
) -> None:
    """**By the configured depth, and the multiple is what is asserted.**

    "Earlier than the stop" is satisfied by a one-second read and would not falsify the
    decision this test exists to document: the walk applies its lead-in again at *every*
    link, so the history depth is a multiple of the lead-in and not equal to it. A read
    bounded by one lead-in hands the walk a timeline that stops mid-chain, and the chain
    terminates `UNEXPLAINED` over evidence the database holds -- with nothing in the answer
    saying so.
    """
    settings = Settings()
    body = client.get(f"/stops/{STOP_ID}").json()

    reach = datetime.fromisoformat(body["stop"]["from_ts"]) - datetime.fromisoformat(
        body["history_from_ts"]
    )
    assert reach == settings.propagation_history
    assert reach > settings.propagation_lead_in
    assert any(
        episode["from_ts"] < body["stop"]["from_ts"] for episode in body["timeline"]
    )


@pytest.mark.usefixtures("seeded_db_with_a_stop")
def test_the_buffer_levels_make_the_chain_checkable_by_eye(
    client: TestClient,
) -> None:
    levels = client.get(f"/stops/{STOP_ID}").json()["buffer_levels"]

    assert {point["buffer"] for point in levels} == {"B1_2", "B2_3", "B3_4"}
    assert any(point["buffer"] == "B3_4" and point["level"] == 0 for point in levels)


@pytest.mark.usefixtures("seeded_db_with_a_stop")
def test_an_instant_the_line_was_running_through_is_not_a_stop(
    client: TestClient,
) -> None:
    """404, and the detail says how long the line was actually without output there.

    An empty 200 would be the wrong answer twice over: it would read as a stop with no
    detail, and §6.5 could not tell a citation that does not resolve from one that resolves
    to nothing.
    """
    response = client.get("/stops/stop-20260912T011500.000000Z")

    assert response.status_code == 404
    assert "micro-stop threshold" in response.json()["detail"]


@pytest.mark.usefixtures("seeded_db_with_a_stop")
def test_something_that_is_not_an_id_is_refused_rather_than_looked_up(
    client: TestClient,
) -> None:
    """422 against the 404 above: the first says this stop does not exist, the second says
    this is not a way of naming one. An agent does different things about each."""
    assert client.get("/stops/the-big-one").status_code == 422
    assert client.get("/stops/stop-20260231T013000.000000Z").status_code == 422


@pytest.mark.usefixtures("seeded_db_with_a_stop")
def test_a_stop_list_carries_the_coverage_of_the_window_it_is_over(
    client: TestClient,
) -> None:
    """A stop list is a claim about absence, and an ingest gap is an absence that looks
    exactly the same from here. Without coverage beside it, an hour the gateway was down
    reports as an hour the line was stopped."""
    body = client.get("/stops", params=WINDOW).json()

    assert body["coverage"]["fully_covered"] is True
    assert body["micro_stop_threshold_seconds"] == 60.0


@pytest.mark.usefixtures("seeded_db_with_a_stop")
def test_an_id_no_part_ever_preceded_is_a_404_and_not_a_six_year_stop(
    client: TestClient,
) -> None:
    """**§6.5's whole purpose, defended.**

    M4 verifies every cited id against the database precisely to catch one the model
    invented. An instant before the history horizon has no part leaving S4 at or before it,
    so nothing in the database says the line was ever producing there — and a stop built
    from the caller's own string would be perfectly self-consistent, pass verification, and
    be cited as a line stop measured in years.
    """
    response = client.get("/stops/stop-20200101T000000.000000Z")

    assert response.status_code == 404
    assert "no part left S4 at or before it" in response.json()["detail"]


@pytest.mark.usefixtures("seeded_db_with_a_stop")
def test_a_stop_already_running_when_the_window_opened_cites_where_it_began(
    client: TestClient,
) -> None:
    """The window's edge is a property of the question and would give one stop a different
    id in every window it appeared in. The part that left before it is the line's own fact.

    The second stop below starts at a real output and cites it; the first starts at a
    boundary the database has nothing before, so it carries no id at all rather than one
    that would answer 404 — a citation the agent could name and nobody could open.
    """
    body = client.get(
        "/stops",
        params={"from": "2026-09-11T23:00:00Z", "to": "2026-09-12T01:00:00Z"},
    ).json()

    assert len(body["stops"]) == 2
    assert body["stops"][0]["started_before_window"] is True
    assert body["stops"][0]["id"] is None
    assert body["stops"][1]["id"] is not None
    assert client.get(f"/stops/{body['stops'][1]['id']}").status_code == 200


@pytest.mark.usefixtures("seeded_db_with_a_stop")
def test_a_stop_carries_the_coverage_of_its_own_interval(client: TestClient) -> None:
    body = client.get(f"/stops/{STOP_ID}").json()

    assert body["coverage"]["fully_covered"] is True
    assert body["coverage"]["window"]["from_ts"] == STOP_FROM.isoformat().replace(
        "+00:00", "Z"
    )
    assert body["coverage"]["window"]["to_ts"] == STOP_TO.isoformat().replace(
        "+00:00", "Z"
    )


@pytest.mark.usefixtures("seeded_db_with_a_stop_and_a_gap")
def test_a_gateway_outage_does_not_resolve_to_a_line_stop_unremarked(
    client: TestClient,
) -> None:
    """**§4.4, at the endpoint that needs it most.**

    Both of this stop's boundaries are reconstructed from `part_dispositions` rows that are
    not there, and an ingest gap is an absence of exactly the same shape. So a five-minute
    gateway outage produces a five-minute stop with an unexplained derivation, and without
    coverage beside it the agent reports a line stop where the line may well have been
    running. The endpoint still answers — the stop is what the output says — but it says
    where the data is not.
    """
    body = client.get(f"/stops/{STOP_ID}").json()

    assert body["stop"]["duration_seconds"] == 300.0
    assert body["coverage"]["fully_covered"] is False
    assert body["coverage"]["covered_fraction"] == 0.0
    assert [gap["reason"] for gap in body["coverage"]["gaps"]] == ["plant_unreachable"]


@pytest.mark.usefixtures("seeded_db_with_a_stop")
def test_the_window_edge_is_not_a_stop_the_database_can_confirm(
    client: TestClient,
) -> None:
    """A guard on the fixture rather than on the code: the first disposition of the whole
    database lies after the window above opens, which is what makes that test's null id the
    real case and not an accident of ordering."""
    assert WINDOW_START.hour == 1
    assert client.get("/stops/stop-20260911T230000.000000Z").status_code == 404
