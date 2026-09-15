"""`/time/resolve`: the calendar over HTTP, and the refusal that must not become a guess.

`test_time_expressions.py` already proves the arithmetic — DST, the shift boundaries, "last
night" at 05:00. What is left to prove here is the wiring: that the endpoint hands `resolve`
the injected clock rather than the wall one, and that an expression it does not understand
comes back as a 422 carrying what it does understand.
"""

from __future__ import annotations

from analysis.time_expressions import UNDERSTOOD_EXPRESSIONS
from fastapi.testclient import TestClient

from tests.conftest import FROZEN_NOW


def test_a_known_expression_resolves_against_the_injected_clock(
    clockless_client: TestClient,
) -> None:
    """The window is a function of `now`, and `now` is a dependency.

    Asserting the window against a frozen clock is the whole point: with the real one this
    test would pass on a Tuesday and fail on a Wednesday, and the endpoint would be free to
    read `datetime.now()` inside the handler without anything noticing.
    """
    body = clockless_client.get("/time/resolve", params={"expression": "today"}).json()

    assert body["window"]["from_ts"] == "2026-09-11T22:00:00Z"
    assert body["window"]["to_ts"] == "2026-09-12T22:00:00Z"
    assert body["now"] == FROZEN_NOW.isoformat().replace("+00:00", "Z")
    assert "2026-09-12" in body["label"]


def test_a_window_still_running_says_so_rather_than_saying_nothing(
    clockless_client: TestClient,
) -> None:
    """§5.3 caches a closed window indefinitely, so `closed` is what a cache keys on.

    At the frozen clock "today" is still being written to and "yesterday" can never change
    again, and the response has to tell the two apart — a closed flag that were always true
    would have a caller serving a partial day forever.
    """
    today = clockless_client.get("/time/resolve", params={"expression": "today"}).json()
    yesterday = clockless_client.get(
        "/time/resolve", params={"expression": "yesterday"}
    ).json()

    assert today["closed"] is False
    assert yesterday["closed"] is True


def test_an_unknown_expression_is_refused_with_the_list_of_understood_ones(
    clockless_client: TestClient,
) -> None:
    """**Never a guess.** A near-miss resolved to the wrong window is the worst of the three
    outcomes: the caller gets a window, believes it asked for it, and every number computed
    over it answers a different question. The list is in the error so the agent can retry
    with something real instead of rephrasing at random."""
    response = clockless_client.get(
        "/time/resolve", params={"expression": "last fortnight"}
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert set(detail["understood"]) == set(UNDERSTOOD_EXPRESSIONS)
    assert "last night" in detail["understood"]


def test_the_refusal_is_not_an_empty_window(clockless_client: TestClient) -> None:
    """422 and not 200-with-nothing, because a caller that did not check would otherwise
    compute over a window of zero length and report a quiet line."""
    response = clockless_client.get("/time/resolve", params={"expression": ""})

    assert response.status_code == 422
    assert "window" not in response.json()
