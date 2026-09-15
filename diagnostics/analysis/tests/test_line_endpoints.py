"""`/alarms`, `/signals/trend` and `/line/status` — and §2.2, made checkable.

The last of the three is the one that matters. Every other endpoint takes a window and is
honest for free: an hour in the past reads the same whether the plant is running or not.
"What is happening right now" over a database that stopped receiving six hours ago is the one
place an answer can be confidently, silently wrong, and §2.2 requires this stack to answer
with the plant shut down. So the last two tests here run the clock forward and assert that
the line reports as stale rather than as quiet.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

import pytest
from analysis.app import app
from analysis.config import Settings
from analysis.dependencies import now_dependency
from fastapi.testclient import TestClient

from tests.conftest import DECOY_FORCE, FROZEN_NOW, PARTS_IN_AN_HOUR, STOP_FROM

WINDOW = {"from": "2026-09-12T01:00:00Z", "to": "2026-09-12T02:00:00Z"}
TREND = {**WINDOW, "station": "S2", "signal": "JoiningForcePeak"}


# --- alarms ---------------------------------------------------------------------------


@pytest.mark.usefixtures("seeded_db_with_a_stop")
def test_the_three_lifecycle_states_are_distinguishable(client: TestClient) -> None:
    """Raised, acknowledged and cleared, and two of the three are still standing.

    A list carrying only `raised_at` would flatten them into one, and "three alarms in this
    hour" would read the same whether an operator had touched any of them.
    """
    alarms = client.get("/alarms", params=WINDOW).json()["alarms"]

    assert [(alarm["code"], alarm["status"], alarm["active"]) for alarm in alarms] == [
        ("A-207", "cleared", False),
        ("A-050", "acknowledged", True),
        ("A-100", "raised", True),
    ]


@pytest.mark.usefixtures("seeded_db_with_a_stop")
def test_an_alarm_raised_before_the_window_and_still_standing_is_in_it(
    client: TestClient,
) -> None:
    """Overlap, not containment. All three of these were raised before the minute below and
    none had been cleared when it started — a containment test on `raised_at` would report a
    quiet minute during an unacknowledged fault."""
    alarms = client.get(
        "/alarms",
        params={"from": "2026-09-12T01:32:00Z", "to": "2026-09-12T01:33:00Z"},
    ).json()["alarms"]

    assert {alarm["code"] for alarm in alarms} == {"A-100", "A-050", "A-207"}


@pytest.mark.usefixtures("seeded_db_with_a_stop")
def test_the_station_filter_narrows_and_an_unknown_station_is_refused(
    client: TestClient,
) -> None:
    """404 rather than an empty list: "S9 raised no alarms" is a false statement about a
    station the line does not have, and §6.5 checks cited ids against the database."""
    filtered = client.get("/alarms", params={**WINDOW, "station": "S2"}).json()

    assert [alarm["code"] for alarm in filtered["alarms"]] == ["A-207"]
    assert filtered["station"] == "S2"
    assert client.get("/alarms", params={**WINDOW, "station": "S9"}).status_code == 404


@pytest.mark.usefixtures("seeded_db_with_a_stop")
def test_a_window_with_no_alarms_is_an_empty_list_and_not_a_404(
    client: TestClient,
) -> None:
    body = client.get(
        "/alarms",
        params={"from": "2026-09-12T00:00:00Z", "to": "2026-09-12T01:00:00Z"},
    ).json()

    assert body["alarms"] == []
    assert body["station"] is None


# --- signal trends --------------------------------------------------------------------


@pytest.mark.usefixtures("seeded_db")
def test_an_hour_bucket_is_one_point_over_every_sample_in_it(
    client: TestClient,
) -> None:
    """Aggregated in SQL. The fixture publishes 900 + (index mod 10) every 6 s, so the hour
    holds 600 samples spanning [900, 909] with a mean of 904.5 — three numbers a query that
    averaged the wrong rows cannot land on together."""
    body = client.get("/signals/trend", params={**TREND, "agg": "hour"}).json()

    assert body["bucket_seconds"] == 3600
    assert len(body["points"]) == 1
    point = body["points"][0]
    assert point["at"] == "2026-09-12T01:00:00Z"
    assert point["count"] == PARTS_IN_AN_HOUR
    assert point["value"] == pytest.approx(DECOY_FORCE + 4.5)
    assert point["min_value"] == DECOY_FORCE
    assert point["max_value"] == DECOY_FORCE + 9


@pytest.mark.usefixtures("seeded_db")
def test_minute_buckets_are_aligned_to_the_clock_and_half_open(
    client: TestClient,
) -> None:
    """Sixty buckets of ten samples each, the first starting exactly on the minute.

    Clock alignment is what lets two trends over different windows be read against each
    other: the 01:07 bucket is the same minute whoever asked for it.
    """
    body = client.get("/signals/trend", params={**TREND, "agg": "minute"}).json()

    assert body["bucket_seconds"] == 60
    assert len(body["points"]) == 60
    assert body["points"][0]["at"] == "2026-09-12T01:00:00Z"
    assert body["points"][1]["at"] == "2026-09-12T01:01:00Z"
    assert {point["count"] for point in body["points"]} == {10}


@pytest.mark.usefixtures("seeded_db")
def test_raw_returns_the_samples_themselves(client: TestClient) -> None:
    body = client.get("/signals/trend", params={**TREND, "agg": "raw"}).json()

    assert body["bucket_seconds"] is None
    assert len(body["points"]) == PARTS_IN_AN_HOUR
    assert body["points"][0] == {
        "at": "2026-09-12T01:00:00Z",
        "value": DECOY_FORCE,
        "min_value": DECOY_FORCE,
        "max_value": DECOY_FORCE,
        "count": 1,
    }
    assert body["truncated"] is False


@pytest.mark.usefixtures("seeded_db")
def test_a_cut_series_says_it_was_cut(
    analysis_url: str, tuned_client: Callable[[Settings], TestClient]
) -> None:
    """A trend that stops early looks exactly like a signal that stopped, which is a
    diagnosis. So the cut has to be a field rather than something a reader infers from the
    point count happening to equal a limit it cannot see."""
    client = tuned_client(Settings(database_url=analysis_url, signal_point_limit=10))
    body = client.get("/signals/trend", params={**TREND, "agg": "raw"}).json()

    assert len(body["points"]) == 10
    assert body["truncated"] is True


@pytest.mark.usefixtures("seeded_db")
def test_an_unknown_station_is_a_404_and_an_unknown_signal_is_an_empty_series(
    client: TestClient,
) -> None:
    """ "S2 has no signal called JoiningForce" and "S2's JoiningForce was silent for this
    hour" lead to different next steps, and one empty list for both would hide a typo as a
    measurement."""
    assert (
        client.get("/signals/trend", params={**TREND, "station": "S9"}).status_code
        == 404
    )
    quiet = client.get("/signals/trend", params={**TREND, "signal": "Nonesuch"})
    assert quiet.status_code == 200
    assert quiet.json()["points"] == []


@pytest.mark.usefixtures("seeded_db")
def test_an_aggregation_the_contract_does_not_offer_is_refused(
    client: TestClient,
) -> None:
    assert (
        client.get("/signals/trend", params={**TREND, "agg": "fortnight"}).status_code
        == 422
    )


# --- the line right now ---------------------------------------------------------------


@pytest.mark.usefixtures("seeded_db_with_a_stop")
def test_the_line_reports_what_it_last_saw_each_station_doing(
    client: TestClient,
) -> None:
    body = client.get("/line/status").json()

    states = {station["station"]: station["state"] for station in body["stations"]}
    assert states == {
        "S1": "Execute",
        "S2": "Execute",
        "S3": "Execute",
        "S4": "Execute",
    }
    assert {buffer["buffer"] for buffer in body["buffers"]} == {
        "B1_2",
        "B2_3",
        "B3_4",
    }
    assert {alarm["code"] for alarm in body["active_alarms"]} == {"A-100", "A-050"}
    assert body["last_part_out"]["at"] == "2026-09-12T02:00:00Z"


@pytest.mark.usefixtures("seeded_db_with_a_stop")
def test_data_arriving_now_reads_as_live(client: TestClient) -> None:
    """The frozen clock sits exactly on the newest row, so the staleness is zero."""
    body = client.get("/line/status").json()

    assert body["staleness_seconds"] == 0.0
    assert body["live"] is True
    assert body["latest_data_at"] == "2026-09-12T02:00:00Z"


@pytest.mark.usefixtures("seeded_db_with_a_stop")
def test_with_the_plant_down_the_line_reads_as_stale_and_not_as_quiet(
    client: TestClient,
) -> None:
    """**§2.2, as an assertion.**

    Six hours after the last row: every station still reports `Execute`, every buffer still
    reports a level, and none of that has been true for six hours. The staleness is the one
    number that says so, and it is beside the threshold it was judged against so the verdict
    can be checked rather than believed.

    The clock is moved by replacing the dependency the fixture installed — which is the
    whole reason the clock is a dependency.
    """
    app.dependency_overrides[now_dependency] = lambda: FROZEN_NOW + timedelta(hours=6)

    body = client.get("/line/status").json()

    assert body["staleness_seconds"] == 6 * 3600.0
    assert body["live"] is False
    assert body["live_within_seconds"] == 60.0
    # The stale picture is still returned rather than withheld: it is the last thing known
    # about the line, and §2.2's point is that it stays answerable. What must not happen is
    # it being returned *without* the number above.
    assert body["stations"][0]["state"] == "Execute"


@pytest.mark.usefixtures("database")
def test_a_database_with_nothing_in_it_says_so_rather_than_reporting_a_quiet_line(
    client: TestClient,
) -> None:
    """Null staleness, not an enormous one: nothing has ever arrived, which is a fresh
    deployment and calls for a different action from a gateway that has stopped.

    Every station and every buffer is still listed, with nulls where a reading would be. A
    line reported with no stations would be a wrong picture of the line; a line reported
    with four silent ones is a true one.
    """
    body = client.get("/line/status").json()

    assert body["latest_data_at"] is None
    assert body["staleness_seconds"] is None
    assert body["live"] is False
    assert [station["state"] for station in body["stations"]] == [None] * 4
    assert [buffer["level"] for buffer in body["buffers"]] == [None] * 3
    assert body["last_part_out"] is None
    assert body["as_of"] == FROZEN_NOW.isoformat().replace("+00:00", "Z")


@pytest.mark.usefixtures("seeded_db_with_a_stop")
def test_a_buffer_level_carries_the_instant_it_was_published_at(
    client: TestClient,
) -> None:
    """§4.1 publishes a level only when a carrier moves through, so a stopped line's last
    level is arbitrarily old — and the number without the instant would read as now."""
    buffers = {
        buffer["buffer"]: buffer
        for buffer in client.get("/line/status").json()["buffers"]
    }

    assert buffers["B3_4"]["level"] == 0
    assert buffers["B3_4"]["capacity"] > 0
    # Published before the line even stopped, and still the newest level there is.
    assert buffers["B3_4"]["at"] < STOP_FROM.isoformat().replace("+00:00", "Z")
