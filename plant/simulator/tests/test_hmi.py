"""The HMI's payload. Asserted as a contract -- the frontend reads these names -- and
deliberately not as a rendering."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest
import yaml
from conftest import STATION_CODES, build_running_line, new_clock
from fastapi import FastAPI
from fastapi.testclient import TestClient
from simulator.address_space import BUFFERS
from simulator.clock import SimulatedClock
from simulator.config import Settings
from simulator.faults import FaultKind, FaultSet, parameters_for
from simulator.ground_truth import INJECTION, Injector, open_log
from simulator.hmi import (
    CATEGORIES,
    RecentParts,
    build_app,
    category_for,
    line_snapshot,
)
from simulator.line import Line
from simulator.packml import State
from simulator.scenarios import scenario
from simulator.stations.base import PartOutcome

NOMINAL_WORK = Settings().press_nominal_work
"""What `ProduceFn` carries from S2. The strip reads the serial and the instant
off the call and the rest off the verdict, so this is only here to be passed."""

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
    line, clock, _nodes = await build_running_line()
    snapshot = line_snapshot(line, clock, RecentParts(Settings().hmi_recent_parts))
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
    line, clock, _nodes = await build_running_line()
    snapshot = line_snapshot(line, clock, RecentParts(Settings().hmi_recent_parts))
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
    line, clock, _nodes = await build_running_line()
    for station in line_snapshot(line, clock, RecentParts(Settings().hmi_recent_parts))[
        "stations"
    ]:
        assert station["state"] in {state.value for state in State}
        assert station["category"] == category_for(State(station["state"]))


@pytest.mark.asyncio
async def test_a_suspended_station_carries_its_reason_to_the_screen() -> None:
    """The HMI's whole diagnostic value is showing *why* a station is waiting."""
    line, clock, _nodes = await build_running_line()
    await line.hold("S2_Joining", clock.history_start, "test")
    for _ in range(CYCLES):
        await line.step()
    s3 = next(
        s
        for s in line_snapshot(line, clock, RecentParts(Settings().hmi_recent_parts))[
            "stations"
        ]
        if s["browse_name"] == "S3_Inspection"
    )
    assert s3["category"] == "waiting-on-others"
    assert s3["reason"] == "starved:B2_3"


@pytest.mark.asyncio
async def test_a_held_station_is_a_cause_candidate_and_a_starved_one_is_not() -> None:
    """§3.3's distinction, carried to the screen as a category rather than left for
    the viewer to infer from a state name."""
    line, clock, _nodes = await build_running_line()
    await line.hold("S2_Joining", clock.history_start, "jam")
    for _ in range(CYCLES):
        await line.step()
    by_name = {
        s["browse_name"]: s
        for s in line_snapshot(line, clock, RecentParts(Settings().hmi_recent_parts))[
            "stations"
        ]
    }
    assert by_name["S2_Joining"]["category"] == "held-by-own-fault"
    assert by_name["S3_Inspection"]["category"] == "waiting-on-others"


@pytest.mark.asyncio
async def test_the_screen_lists_the_alarms_a_station_is_shut_down_for() -> None:
    """§3.7: active alarms. The screen's `held-by-own-fault` tile says a station is a
    cause candidate; the alarm beside it is what it is a candidate *for*, and §5.2's own
    row is code, text and severity -- so all three travel.

    Driven through §3.5's scenario 3 rather than by putting an alarm into the system by
    hand: what is being asserted is that the screen shows what the line actually did, and
    a hand-made alarm would assert the serialiser against itself.
    """
    settings = Settings()
    clock = new_clock(settings)
    line, clock, _nodes = await build_running_line(
        settings,
        faults=scenario(3, settings).fault_set(clock.history_start),
        clock=clock,
    )
    horizon = clock.history_start + timedelta(seconds=6000)
    while (due := line.next_due) is not None and due < horizon:
        await line.step()

    assert line.alarms.alarms, "the drift never raised an alarm, so this proved nothing"
    snapshot = line_snapshot(line, clock, RecentParts(settings.hmi_recent_parts))
    listed = snapshot["alarms"]
    assert [alarm["sequence"] for alarm in listed] == [
        alarm.sequence for alarm in line.alarms.active
    ]
    # Cleared alarms are history and history is what the diagnostics stack answers from;
    # a screen that listed them all would put the one being worked on at the bottom.
    assert len(listed) < len(line.alarms.alarms)

    shown = listed[0]
    assert shown["station_browse_name"] in STATION_CODES
    assert (shown["code"], shown["text"]) == ("A-207", "joining force out of tolerance")
    assert 1 <= shown["severity"] <= 1000
    assert datetime.fromisoformat(shown["raised_at"]).tzinfo is not None
    # Every listed alarm is one nobody has reached yet, which is why the payload carries
    # no acknowledgement state: `_intervene` acknowledges, restarts and clears in one
    # call, and this list holds only what is still active. It carried `acknowledged` and
    # `acked_at` until review pointed out that both were always the same value and the
    # assertion about them could not fail.
    assert all(alarm.acked_at is None for alarm in line.alarms.active)


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
    line, clock, _nodes = await build_running_line()
    for buffer in line_snapshot(line, clock, RecentParts(Settings().hmi_recent_parts))[
        "buffers"
    ]:
        assert buffer["capacity"] == Settings().buffer_capacity
        assert 0 <= buffer["level"] <= Settings().buffer_capacity


@dataclass(frozen=True)
class Panel:
    """The screen's server, the fault set behind it, and the log it records into.

    All three together, because §3.5's rule is about the three of them at once: an
    injection made through the app has to reach the set the line is running on *and* the
    log on the ground-truth volume, and a fixture that held only the app could not see
    either half.
    """

    app: FastAPI
    faults: FaultSet
    log_path: Path


def _panel(
    line: Line,
    clock: SimulatedClock,
    recent: RecentParts,
    tmp_path: Path,
    faults: FaultSet | None = None,
    settings: Settings | None = None,
) -> Panel:
    """`build_app` as `server.main` builds it, on a ground-truth log in `tmp_path`.

    The log is a real `GroundTruthLog` on a real file rather than a double: what is being
    asserted is that the bytes reach the volume, and a double would assert the call.
    """
    settings = settings or Settings()
    injected = FaultSet((), clock.history_start) if faults is None else faults
    path = tmp_path / "ground-truth.jsonl"
    log = open_log(path, settings, clock, None)
    injector = Injector(injected, log, clock.history_start)
    return Panel(
        build_app(line, clock, settings, recent, injector, clock.history_start),
        injected,
        path,
    )


def one[T](items: Sequence[T]) -> T:
    """The single element of `items`, asserting there is exactly one.

    Not `items[0]`: a list that grew a second entry is a panel that injected twice, or a
    log that recorded an injection nobody made, and indexing would report neither.
    """
    assert len(items) == 1, f"expected exactly one, got {len(items)}"
    return items[0]


def _injections(path: Path) -> list[dict[str, object]]:
    """Every `injection` record in a ground-truth log, in the order it was written."""
    records = [json.loads(line) for line in path.read_text().splitlines()]
    return [record for record in records if record["record"] == INJECTION]


def test_one_payload_reaches_the_screen_over_http_and_over_the_websocket(
    tmp_path: Path,
) -> None:
    """Both endpoints serve `line_snapshot`, so there is no second rendering to
    disagree with the first. Synchronous because `TestClient` drives its own event
    loop; the line is built on a loop of its own beforehand and holds nothing bound
    to it.
    """
    line, clock, _nodes = asyncio.run(build_running_line())
    recent = asyncio.run(_strip_with(REJECT, GOOD))
    with TestClient(_panel(line, clock, recent, tmp_path).app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        over_http = client.get("/snapshot").json()
        with client.websocket_connect("/ws") as socket:
            over_websocket = socket.receive_json()

    # Not equality: `written_wall` and `simulated_now` move between the two reads, and
    # asserting they do not would be asserting the clock had stopped.
    assert over_websocket["stations"] == over_http["stations"]
    assert over_websocket["buffers"] == over_http["buffers"]
    assert over_websocket["parts"] == over_http["parts"]
    assert over_websocket["phase"] == over_http["phase"]


REJECT = PartOutcome(
    disposition="reject",
    defect_class="gap",
    defect_classes=("gap",),
    confidences=(0.91,),
    confidence=0.91,
    image=b"\x89PNG\r\n\x1a\n" + b"x" * 64,
    model_version="sim-1",
)
GOOD = PartOutcome(
    disposition="good",
    defect_class=None,
    defect_classes=("gap",),
    confidences=(0.03,),
    confidence=0.97,
    # §3.4: only rejects carry an image.
    image=None,
    model_version="sim-1",
)


async def _strip_with(*outcomes: PartOutcome) -> RecentParts:
    """A strip fed the way the plant feeds it: through the wrapped inspection call.

    Not by appending to it directly. The wrapper is the only thing that populates this
    object in the running plant, so a fixture that filled it another way would leave the
    one path that matters untested.
    """
    recent = RecentParts(Settings().hmi_recent_parts)
    queue = list(outcomes)

    # The serial and the instant are `ProduceFn`'s and are what `RecentParts.watching`
    # reads off the call rather than off the result; this stand-in only has to return
    # the verdicts.
    async def produce(
        _part_id: str, _carrier_id: int, _joining_work: float, _at: datetime
    ) -> PartOutcome:
        return queue.pop(0)

    watched = recent.watching(produce)
    at = datetime(2026, 9, 13, 6, 9, 48, tzinfo=UTC)
    for index in range(len(outcomes)):
        await watched(
            f"A-{index:08d}",
            index % 18,
            NOMINAL_WORK,
            at + timedelta(seconds=6 * index),
        )
    return recent


@pytest.mark.asyncio
async def test_the_strip_carries_the_last_parts_newest_first() -> None:
    """§3.7's strip, and the reason it carries serials: the string on this screen is the
    one the diagnostics stack answers `/parts/{serial}` for."""
    line, clock, _nodes = await build_running_line()
    snapshot = line_snapshot(line, clock, await _strip_with(GOOD, REJECT))

    assert [part["serial"] for part in snapshot["parts"]] == [
        "A-00000001",
        "A-00000000",
    ]
    assert snapshot["parts"][0]["disposition"] == "reject"
    assert snapshot["parts"][0]["reason"] == "gap"
    # A good part carries no reason, and an empty string is how that is said -- the same
    # convention `PartState.reason` uses, so the strip and `part_dispositions` say the
    # same word about the same part.
    assert snapshot["parts"][1]["reason"] == ""


@pytest.mark.asyncio
async def test_only_a_reject_offers_an_image_and_it_is_the_one_stored(
    tmp_path: Path,
) -> None:
    """§3.4 gives only rejects an image. The strip carries a path rather than the bytes,
    because a reject's PNG is ~110 kB and a frame goes out twice a second."""
    line, clock, _nodes = await build_running_line()
    recent = await _strip_with(GOOD, REJECT)
    snapshot = line_snapshot(line, clock, recent)

    assert snapshot["parts"][0]["image_url"] == "/parts/A-00000001/image"
    assert snapshot["parts"][1]["image_url"] is None

    with TestClient(_panel(line, clock, recent, tmp_path).app) as client:
        response = client.get("/parts/A-00000001/image")
        assert response.status_code == 200
        assert response.content == REJECT.image
        # A good part, a serial this run never saw, and a reject that has fallen off the
        # strip are all answered the same way, and none of them is a blank 200.
        assert client.get("/parts/A-00000000/image").status_code == 404
        assert client.get("/parts/A-99999999/image").status_code == 404


@pytest.mark.asyncio
async def test_the_strip_forgets_rather_than_growing_with_the_run() -> None:
    """A run inspects ~19,800 parts and rebuilds 33 h of history on every boot at 700x.
    An unbounded strip would hold every reject image of all of it in a process that is
    otherwise flat, so the bound is the design and not a display detail."""
    keep = Settings().hmi_recent_parts
    recent = await _strip_with(*[REJECT] * (keep + 3))

    views = recent.views()
    assert len(views) == keep
    # The three that fell off took their images with them, which is the whole point.
    assert recent.image("A-00000000") is None
    assert recent.image(views[0]["serial"]) == REJECT.image


# --- §3.7's fault-injection panel ----------------------------------------------------


def test_every_injection_from_the_panel_reaches_the_ground_truth_log(
    tmp_path: Path,
) -> None:
    """**§3.5's rule, and the one that makes the panel legitimate at all.**

    M2b declined a hold button on the grounds that fault injection without a ground-truth
    log would be this milestone's job done without its evidence. The log exists now, so
    the button is allowed -- and the condition attached to it was that every injection
    from the panel writes to the log exactly as a scripted one does. A fault the plant is
    running that the log does not know about is one no evaluation can ever account for.

    Asserted against the bytes on the volume rather than against a call: `open_log` and
    `Injector` both write through the same file, and reading it back is the only
    representation of "it was recorded" that a later milestone will have.
    """
    line, clock, _nodes = asyncio.run(build_running_line())
    panel = _panel(line, clock, RecentParts(1), tmp_path)

    with TestClient(panel.app) as client:
        response = client.post(
            "/faults",
            json={
                "kind": "joining_force_drift",
                "params": {"newtons": -250.0, "ramp_seconds": 60.0},
                "duration_seconds": 900.0,
            },
        )

    assert response.status_code == 201
    body = response.json()
    assert body["kind"] == "joining_force_drift"
    assert body["params"] == {"newtons": -250.0, "ramp_seconds": 60.0}

    record = one(_injections(panel.log_path))
    # `source` is what tells a later milestone this fault had nothing scripted behind it:
    # it carries no consequences, because nobody wrote down what should follow from one
    # chosen at a keyboard, and a claim invented here would be the log deciding what the
    # plant was going to do.
    # The literal, for the reason `test_ground_truth` spells out: this is the wire
    # format, and an assertion written against the constant moves with it.
    assert record["source"] == "operator"
    assert record["kind"] == "joining_force_drift"
    assert record["consequences"] == []
    assert record["params"] == {"newtons": -250.0, "ramp_seconds": 60.0}
    # Simulated instants, not the wall clock the button was pressed at -- the same clock
    # `state_changes` and `inspection_results` are read on (§4.2).
    assert record["at"] == body["at"]
    assert datetime.fromisoformat(cast(str, record["until"])) - datetime.fromisoformat(
        cast(str, record["at"])
    ) == timedelta(seconds=900)


def test_a_fault_the_panel_injects_is_one_the_line_is_running(tmp_path: Path) -> None:
    """The other half of the same rule. A log entry with no fault behind it describes a
    run that did not happen, which is worse than no entry at all.

    The `FaultSet` asserted on is the one the four stations were built with, so this is
    "the plant is running it" rather than "the injector remembered it" -- a set rebuilt
    instead of appended to would reach nothing, and `test_alarms` is where the same
    injection is followed all the way to a number S2 publishes.
    """
    settings = Settings()
    clock = new_clock(settings)
    faults = FaultSet((), clock.history_start)
    line, clock, _nodes = asyncio.run(
        build_running_line(settings, faults=faults, clock=clock)
    )
    panel = _panel(line, clock, RecentParts(1), tmp_path, faults=faults)

    with TestClient(panel.app) as client:
        assert (
            client.post(
                "/faults",
                json={
                    "kind": "optics_fouling",
                    "params": {"factor": 0.5},
                    "duration_seconds": None,
                },
            ).status_code
            == 201
        )

    injected = one(faults.faults)
    assert injected.kind is FaultKind.OPTICS_FOULING
    # Not repaired, because the panel said so rather than because it forgot to ask.
    assert injected.until is None
    assert faults.active_at(clock.now())


def test_a_fault_the_plant_does_not_take_is_refused_and_not_recorded(
    tmp_path: Path,
) -> None:
    """Both halves, and the second is the one that matters.

    `Fault.__post_init__` refuses a parameter the kind does not read, a missing magnitude
    and a scope a kind requires -- and it refuses them *before* anything is written, so a
    rejected injection leaves no record. A log carrying faults the plant never ran would
    make every part record after it unattributable, which is the same worthlessness §3.6
    exists to prevent from the other direction.

    400 rather than 500: an operator typed something this plant does not take, and the
    panel has to be able to say which.
    """
    line, clock, _nodes = asyncio.run(build_running_line())
    panel = _panel(line, clock, RecentParts(1), tmp_path)

    with TestClient(panel.app) as client:
        refused = [
            # A kind §3.5 does not have.
            {"kind": "gremlins", "params": {"factor": 0.5}, "duration_seconds": None},
            # A parameter this kind does not read -- the failure `Fault.__post_init__`
            # refuses rather than ignores, because a magnitude that changes nothing is
            # an injection with no consequence anywhere.
            {
                "kind": "optics_fouling",
                "params": {"newtons": -400.0},
                "duration_seconds": None,
            },
            # Carrier wear with no carrier: a fault that applies to every part is a
            # line-wide drift, which is a different fault with a different diagnosis.
            {
                "kind": "carrier_wear",
                "params": {"factor": 3.0},
                "duration_seconds": None,
            },
        ]
        for body in refused:
            assert client.post("/faults", json=body).status_code == 400, body

    assert _injections(panel.log_path) == []
    assert panel.faults.faults == ()


def test_the_panel_is_offered_exactly_the_kinds_the_plant_has(tmp_path: Path) -> None:
    """The vocabulary is served rather than written into the bundle.

    §3.5 has seven kinds and each takes its own numbers; a screen carrying its own copy
    offers a parameter `Fault.__post_init__` refuses, and the operator finds out by
    pressing the button. This is also what keeps the eighth kind, whenever there is one,
    from needing a frontend change to be injectable.
    """
    line, clock, _nodes = asyncio.run(build_running_line())

    with TestClient(_panel(line, clock, RecentParts(1), tmp_path).app) as client:
        offered = client.get("/faults").json()

    assert [view["kind"] for view in offered] == [str(kind) for kind in FaultKind]
    for view in offered:
        parameters = parameters_for(FaultKind(view["kind"]))
        assert view["parameters"] == list(parameters.names)
        assert view["scope"] == parameters.scope
        assert view["scope_required"] == parameters.scope_required


# --- §3.7's acknowledge button --------------------------------------------------------


def test_the_acknowledge_button_answers_a_sequence_no_alarm_has(
    tmp_path: Path,
) -> None:
    """The screen draws a frame twice a second, so a button pressed against the frame
    before this one is a race rather than a fault -- and so is one pressed on an alarm
    whose operator has already been. 404 says which, and changes nothing.

    What acknowledging actually *does* to the line is `test_alarms`', because it is a
    claim about the line and not about the endpoint.
    """
    line, clock, _nodes = asyncio.run(build_running_line())

    with TestClient(_panel(line, clock, RecentParts(1), tmp_path).app) as client:
        assert client.post("/alarms/0/acknowledge").status_code == 404


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
