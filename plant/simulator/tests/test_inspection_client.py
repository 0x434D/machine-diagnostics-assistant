import json
from datetime import UTC, datetime

import httpx
import pytest
from simulator.config import Settings
from simulator.inspection_client import DEFECT_CLASSES, InspectionClient

SIM_TS = datetime(2026, 9, 12, 6, 0, 0, tzinfo=UTC)
NOMINAL_WORK = Settings().press_nominal_work
"""A part pressed at every nominal, so nothing here scales the `gap` propensity.

Every test in this file is about the side channel, the vocabulary or the seed, and a
part joined at nominal is the one whose defect draw none of those has to reason about.
`test_scenarios` is where a press that did less work changes the answer.
"""


def _ok_inspect_response(disposition: str, defect_class: str | None) -> httpx.Response:
    """What the inspection service answers: six independent per-class scores keyed by
    name, and the verdict's own confidence beside them (§3.4)."""
    return httpx.Response(
        200,
        json={
            "disposition": disposition,
            "defect_class": defect_class,
            "confidence": 0.88,
            "confidences": {
                name: 0.71 if name == defect_class else 0.05 for name in DEFECT_CLASSES
            },
            "model_version": "simulated-1",
        },
    )


@pytest.mark.asyncio
async def test_only_rejects_carry_an_image() -> None:
    """§3.4: only rejected parts carry their image into the OPC UA event. Good parts
    get a result without one -- which is what keeps images inside the single
    permitted channel rather than requiring a second."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/truth/"):
            return httpx.Response(200, json={"status": "ok"})
        reject = b"A-00000007" in request.content
        return _ok_inspect_response(
            "reject" if reject else "good", "gap" if reject else None
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = InspectionClient(
            Settings(reject_rate=1.0, image_width=64, image_height=64), http
        )
        rejected = await client.produce("A-00000007", 3, NOMINAL_WORK, SIM_TS)
        good = await client.produce("A-00000008", 3, NOMINAL_WORK, SIM_TS)

    assert rejected.image is not None
    assert rejected.image.startswith(b"\x89PNG")
    assert good.image is None


@pytest.mark.asyncio
async def test_truth_never_appears_in_the_inspect_request() -> None:
    """§3.4/§4.5: the classifier resolves truth through the side channel, never
    through the request -- checked here against the request's actual field set, not
    a substring scan a field rename would defeat."""
    bodies: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        # Matched on the path, not a substring of the full URL: the default
        # inspection_url's host is "inspection-service", which itself contains
        # "/inspect" right after the scheme's "//" and would false-match the
        # truth-channel request too.
        if request.url.path == "/inspect":
            bodies.append(request.content)
            return _ok_inspect_response("good", None)
        return httpx.Response(200, json={"status": "ok"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await InspectionClient(
            Settings(reject_rate=1.0, image_width=64, image_height=64), http
        ).produce("A-1", 3, NOMINAL_WORK, SIM_TS)

    assert bodies
    for body in bodies:
        assert set(json.loads(body)) == {"part_id", "image_b64", "carrier_id"}


@pytest.mark.asyncio
async def test_defect_draw_does_not_depend_on_prior_calls() -> None:
    """F4: a single client's defect decision for a given part_id must not depend on
    how many other parts were produced first. Before this fix, one shared
    `random.Random` advanced across every call, so changing the configured history
    depth (which changes how many catch-up calls precede live production) would have
    silently shifted what live production draws for the same seed.

    This is the property that lets `server.main` build one client for both phases
    rather than M1's two."""
    seen: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/truth/"):
            seen.append(request.content)
            return httpx.Response(200, json={"status": "ok"})
        return _ok_inspect_response("good", None)

    settings = Settings(reject_rate=0.5, image_width=64, image_height=64)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        fresh = InspectionClient(settings, http)
        await fresh.produce("A-00000099", 3, NOMINAL_WORK, SIM_TS)
        fresh_truth = seen[-1]

        warmed = InspectionClient(settings, http)
        for i in range(50):
            await warmed.produce(f"A-{i:08d}", i % 18, NOMINAL_WORK, SIM_TS)
        await warmed.produce("A-00000099", 3, NOMINAL_WORK, SIM_TS)
        warmed_truth = seen[-1]

    assert fresh_truth == warmed_truth


@pytest.mark.asyncio
async def test_constructor_seed_overrides_settings_seed_for_the_defect_draw() -> None:
    """The verdict for a part must be a function of (seed, part_id) and of nothing
    else -- so the seed has to be one of the two things that decide it, rather than a
    parameter the client accepts and ignores. The test above asserts the part_id half
    of that claim; this one asserts the seed half."""
    truth_bodies: dict[int, bytes] = {}

    def make_handler(seed: int) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.startswith("/truth/"):
                truth_bodies[seed] = request.content
                return httpx.Response(200, json={"status": "ok"})
            return _ok_inspect_response("reject", "gap")

        return httpx.MockTransport(handler)

    settings = Settings(reject_rate=1.0, image_width=64, image_height=64)
    for seed in (settings.seed, settings.seed ^ 1):
        async with httpx.AsyncClient(transport=make_handler(seed)) as http:
            await InspectionClient(settings, http, seed=seed).produce(
                "A-00000005", 3, NOMINAL_WORK, SIM_TS
            )

    assert truth_bodies[settings.seed] != truth_bodies[settings.seed ^ 1]


@pytest.mark.asyncio
async def test_the_client_refuses_a_vector_keyed_by_classes_it_does_not_know() -> None:
    """The defect vocabulary exists twice in this repository -- once per uv workspace,
    because §10.7 forbids the two packages importing each other -- and the event
    carries the scores as an array, so their order is decided by one of the two copies.
    A response scored against the other copy must fail here rather than be silently
    ordered against the wrong names: every entry after the first disagreement would
    then describe the wrong defect class, with nothing raised anywhere downstream.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/truth/"):
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(
            200,
            json={
                "disposition": "good",
                "defect_class": None,
                "confidence": 0.9,
                # A seventh class, which is exactly how the two copies drift.
                "confidences": {name: 0.05 for name in [*DEFECT_CLASSES, "burr"]},
                "model_version": "simulated-1",
            },
        )

    settings = Settings(reject_rate=0.0, image_width=64, image_height=64)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(ValueError, match="drifted"):
            await InspectionClient(settings, http).produce(
                "A-00000001", 3, NOMINAL_WORK, SIM_TS
            )


def test_the_plant_configures_no_verdict_error_rate_of_its_own() -> None:
    """D7: false accepts and false rejects belong to the classifier, not to the
    simulator's noise model.

    §3.5 lists both under the line's permanent noise floor, which is where a knob for
    them would naturally be added here; §3.4 assigns them to the vision system, and D7
    settles it that way because a real `ModelClassifier` has error rates emergently --
    so a plant that also drew them would double-count them the day one drops in, and
    every evaluation number taken before that day would be wrong by the second draw.

    This is the mechanical half of a decision that is otherwise only prose. The
    simulator declares which parts are *actually* defective (`reject_rate`) and nothing
    about which ones the classifier gets right; the two rates live in
    `inspection.config.Settings`.
    """
    knobs = {
        name
        for name in Settings.model_fields
        if "false_accept" in name or "false_reject" in name
    }
    assert knobs == set(), (
        f"{sorted(knobs)} is the classifier's to configure (D7); the plant declares "
        "the truth and never the verdict"
    )
