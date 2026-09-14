"""The HMI's payload. Asserted as a contract -- the frontend reads these names -- and
deliberately not as a rendering."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from conftest import STATION_CODES, build_running_line
from fastapi.testclient import TestClient
from simulator.address_space import BUFFERS
from simulator.config import Settings
from simulator.hmi import CATEGORIES, build_app, category_for, line_snapshot
from simulator.packml import State

NGINX_CONF = Path(__file__).resolve().parents[2] / "hmi" / "nginx.conf"

# How many cycles to run before asserting. Four stations at roughly one takt each, so
# ten rounds of the line -- long enough for a buffer to empty and the station below it
# to notice, and short enough to stay a unit test.
CYCLES = 40


@pytest.mark.asyncio
async def test_the_snapshot_carries_every_station_and_every_buffer() -> None:
    line, clock = await build_running_line()
    snapshot = line_snapshot(line, clock)
    assert [s["code"] for s in snapshot["stations"]] == list(STATION_CODES)
    assert [b["code"] for b in snapshot["buffers"]] == [code for code, _, _ in BUFFERS]


@pytest.mark.asyncio
async def test_every_station_shows_its_state_name_beside_its_colour() -> None:
    """ISA-101: colour is never the only channel. `category` decides the colour, so the
    PackML name it stands for has to travel with it or the screen cannot show both."""
    line, clock = await build_running_line()
    for station in line_snapshot(line, clock)["stations"]:
        assert station["state"] in {state.value for state in State}
        assert station["category"] == category_for(State(station["state"]))


@pytest.mark.asyncio
async def test_a_suspended_station_carries_its_reason_to_the_screen() -> None:
    """The HMI's whole diagnostic value is showing *why* a station is waiting."""
    line, clock = await build_running_line()
    await line.hold("S2_Joining", clock.history_start, "test")
    for _ in range(CYCLES):
        await line.step()
    s3 = next(
        s
        for s in line_snapshot(line, clock)["stations"]
        if s["code"] == "S3_Inspection"
    )
    assert s3["category"] == "waiting-on-others"
    assert s3["reason"] == "starved:B2_3"


@pytest.mark.asyncio
async def test_a_held_station_is_a_cause_candidate_and_a_starved_one_is_not() -> None:
    """§3.3's distinction, carried to the screen as a category rather than left for
    the viewer to infer from a state name."""
    line, clock = await build_running_line()
    await line.hold("S2_Joining", clock.history_start, "jam")
    for _ in range(CYCLES):
        await line.step()
    by_code = {s["code"]: s for s in line_snapshot(line, clock)["stations"]}
    assert by_code["S2_Joining"]["category"] == "held-by-own-fault"
    assert by_code["S3_Inspection"]["category"] == "waiting-on-others"


def test_every_packml_state_maps_to_exactly_one_category() -> None:
    """A state with no category renders as nothing, which on a plant screen is worse
    than rendering wrong.

    `category_for` raises rather than defaulting, so iterating every member *is* the
    exhaustiveness proof: an unmapped state fails here instead of reaching a viewer as
    a blank tile. The equality checks the other direction too -- a category no state
    ever produces is a colour in the legend that the line can never show.
    """
    assert {category_for(state) for state in State} == set(CATEGORIES)


@pytest.mark.asyncio
async def test_the_buffer_bars_get_the_capacity_they_are_a_fraction_of() -> None:
    """A level with no capacity beside it is a number, not a fill bar -- and the
    capacity is `Settings.buffer_capacity`, never a constant in the frontend."""
    line, clock = await build_running_line()
    for buffer in line_snapshot(line, clock)["buffers"]:
        assert buffer["capacity"] == Settings().buffer_capacity
        assert 0 <= buffer["level"] <= Settings().buffer_capacity


def test_one_payload_reaches_the_screen_over_http_and_over_the_websocket() -> None:
    """Both endpoints serve `line_snapshot`, so there is no second rendering to
    disagree with the first. Synchronous because `TestClient` drives its own event
    loop; the line is built on a loop of its own beforehand and holds nothing bound
    to it.
    """
    line, clock = asyncio.run(build_running_line())
    with TestClient(build_app(line, clock, Settings())) as client:
        assert client.get("/health").json() == {"status": "ok"}
        over_http = client.get("/snapshot").json()
        with client.websocket_connect("/ws") as socket:
            over_websocket = socket.receive_json()

    # Not equality: `written_wall` and `simulated_now` move between the two reads, and
    # asserting they do not would be asserting the clock had stopped.
    assert over_websocket["stations"] == over_http["stations"]
    assert over_websocket["buffers"] == over_http["buffers"]
    assert over_websocket["phase"] == over_http["phase"]


def test_the_screens_proxy_names_the_port_the_simulator_serves_on() -> None:
    """Three spellings of one port -- `Settings.hmi_server_port`, the address nginx
    proxies to, and the Vite dev server's proxy target -- and a mismatch shows as an
    empty screen with nothing in any log to say why. This is the one of the three that
    cannot be checked by importing it.
    """
    assert f"line-simulator:{Settings().hmi_server_port}" in NGINX_CONF.read_text()
