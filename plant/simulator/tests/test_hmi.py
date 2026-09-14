"""The HMI's payload. Asserted as a contract -- the frontend reads these names -- and
deliberately not as a rendering."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

import pytest
import yaml
from conftest import STATION_CODES, build_running_line
from fastapi.testclient import TestClient
from simulator.address_space import BUFFERS
from simulator.config import Settings
from simulator.hmi import CATEGORIES, build_app, category_for, line_snapshot
from simulator.packml import State

PLANT = Path(__file__).resolve().parents[2]
HMI = PLANT / "hmi"
NGINX_TEMPLATE = HMI / "nginx.conf.template"
COMPOSE = PLANT / "compose.yml"

# The Makefile's `in-frontend` macro skips a frontend directory that is not in this
# checkout rather than failing, so a simulator unit test must not be the one thing that
# hard-requires it. The tests this guards are about the wiring between the two, and a
# checkout with no wiring has none to be wrong.
needs_the_screen = pytest.mark.skipif(
    not HMI.is_dir(), reason=f"no {HMI.name}/ in this checkout"
)

# How many cycles to run before asserting. Four stations at roughly one takt each, so
# ten rounds of the line -- long enough for a buffer to empty and the station below it
# to notice, and short enough to stay a unit test.
CYCLES = 40


@pytest.mark.asyncio
async def test_the_snapshot_carries_every_station_and_every_buffer() -> None:
    line, clock = await build_running_line()
    snapshot = line_snapshot(line, clock)
    assert [s["browse_name"] for s in snapshot["stations"]] == list(STATION_CODES)
    assert [b["code"] for b in snapshot["buffers"]] == [code for code, _, _ in BUFFERS]


@pytest.mark.asyncio
async def test_a_station_is_named_by_its_browse_name_and_the_field_says_so() -> None:
    """The plant's identity for a station is §4.1's browse name, and emitting anything
    else would make the HMI perform a split the plant never performs. But `code` means
    `S1` one stack over (`stations.code`, from `TopologyDiscovery.SplitBrowseName`), so
    a field called `code` carrying `S1_Feeding` is a join that returns nothing and says
    nothing. The name is what keeps the two apart.
    """
    line, clock = await build_running_line()
    snapshot = line_snapshot(line, clock)
    for station in snapshot["stations"]:
        assert "_" in station["browse_name"]
    # A buffer's own code is NOT split by the gateway -- it stores the browse name whole
    # -- so `code` there is the same word the diagnostics stack uses for the same value.
    # The station references beside it are split, and are named for what they hold.
    for buffer in snapshot["buffers"]:
        assert buffer["upstream_browse_name"] in STATION_CODES
        assert buffer["downstream_browse_name"] in STATION_CODES


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
        if s["browse_name"] == "S3_Inspection"
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
    by_name = {s["browse_name"]: s for s in line_snapshot(line, clock)["stations"]}
    assert by_name["S2_Joining"]["category"] == "held-by-own-fault"
    assert by_name["S3_Inspection"]["category"] == "waiting-on-others"


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


@needs_the_screen
def test_the_screens_proxy_reads_the_port_rather_than_restating_it() -> None:
    """`Settings.hmi_server_port` has to be configuration in effect and not only in
    form. It was neither: nginx.conf named 8200 as a literal, so raising the setting
    moved the server, left the proxy pointing where it used to be, and reported that as
    a blank screen and a 502 in a log nobody reads.

    The template names the variable instead, and the two Compose services are filled
    from one interpolation -- so the only number left to drift is the default, which is
    what this pins.
    """
    template = NGINX_TEMPLATE.read_text()
    assert "${PLANT_HMI_SERVER_PORT}" in template
    # Directives only. A comment naming the number is prose about it, not a second place
    # nginx reads it from -- and this file's own comment explains why the literal went.
    directives = "\n".join(
        line for line in template.splitlines() if not line.lstrip().startswith("#")
    )
    assert str(Settings().hmi_server_port) not in directives, (
        "the template must not restate the port it is rendered from"
    )

    services = cast(
        dict[str, dict[str, object]], yaml.safe_load(COMPOSE.read_text())["services"]
    )
    default = f"${{PLANT_HMI_SERVER_PORT:-{Settings().hmi_server_port}}}"
    for service in ("line-simulator", "plant-hmi"):
        environment = cast(dict[str, str], services[service].get("environment") or {})
        assert environment["PLANT_HMI_SERVER_PORT"] == default, (
            f"{service} names a different default port than simulator.config does"
        )


@needs_the_screen
def test_the_screen_gets_somewhere_writable_to_render_its_configuration_into() -> None:
    """The container is read_only, and rendering the template is the one write it has to
    make at start. Without a writable mount there the entrypoint refuses, nginx falls
    back to the image's stock configuration, and the container comes up healthy serving
    the wrong site -- observed, which is why the mode is asserted and not just the path.
    A bare tmpfs is root-owned at 0775 and this container runs as 101.
    """
    services = cast(
        dict[str, dict[str, object]], yaml.safe_load(COMPOSE.read_text())["services"]
    )
    mounts = cast(list[dict[str, object]], services["plant-hmi"]["volumes"])
    conf_d = next(m for m in mounts if m.get("target") == "/etc/nginx/conf.d")
    assert conf_d["type"] == "tmpfs"
    # 01777 in the file; YAML reads a leading zero as octal, so this is that number.
    assert cast(dict[str, int], conf_d["tmpfs"])["mode"] == 0o1777
