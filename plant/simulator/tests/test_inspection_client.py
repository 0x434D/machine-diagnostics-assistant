import json
from datetime import UTC, datetime

import httpx
import pytest
from simulator.config import Settings
from simulator.inspection_client import InspectionClient

SIM_TS = datetime(2026, 9, 12, 6, 0, 0, tzinfo=UTC)


def _ok_inspect_response(disposition: str, defect_class: str | None) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "disposition": disposition,
            "defect_class": defect_class,
            "confidence": 0.88,
            "confidences": {},
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
        rejected = await client.produce("A-00000007", SIM_TS)
        good = await client.produce("A-00000008", SIM_TS)

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
        ).produce("A-1", SIM_TS)

    assert bodies
    for body in bodies:
        assert set(json.loads(body)) == {"part_id", "image_b64", "carrier_id"}


@pytest.mark.asyncio
async def test_defect_draw_does_not_depend_on_prior_calls() -> None:
    """F4: a single client's defect decision for a given part_id must not depend on
    how many other parts were produced first. Before this fix, one shared
    `random.Random` advanced across every call, so changing the configured history
    depth (which changes how many catch-up calls precede live production) would have
    silently shifted what live production draws for the same seed -- exactly the
    coupling station_s3.run_live's own `settings.seed ^ 1` exists to prevent for
    TaktTime jitter."""
    seen: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/truth/"):
            seen.append(request.content)
            return httpx.Response(200, json={"status": "ok"})
        return _ok_inspect_response("good", None)

    settings = Settings(reject_rate=0.5, image_width=64, image_height=64)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        fresh = InspectionClient(settings, http)
        await fresh.produce("A-00000099", SIM_TS)
        fresh_truth = seen[-1]

        warmed = InspectionClient(settings, http)
        for i in range(50):
            await warmed.produce(f"A-{i:08d}", SIM_TS)
        await warmed.produce("A-00000099", SIM_TS)
        warmed_truth = seen[-1]

    assert fresh_truth == warmed_truth


@pytest.mark.asyncio
async def test_constructor_seed_overrides_settings_seed_for_the_defect_draw() -> None:
    """The `seed` constructor parameter is how a caller gives catch-up and live
    phases independent defect streams (mirroring station_s3.run_live's
    `settings.seed ^ 1`); confirm it actually changes the draw rather than being
    silently ignored."""
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
                "A-00000005", SIM_TS
            )

    assert truth_bodies[settings.seed] != truth_bodies[settings.seed ^ 1]
