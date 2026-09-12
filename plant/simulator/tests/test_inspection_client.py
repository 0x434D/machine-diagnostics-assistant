from datetime import UTC, datetime

import httpx
import pytest
from simulator.config import Settings
from simulator.inspection_client import InspectionClient

SIM_TS = datetime(2026, 9, 12, 6, 0, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_only_rejects_carry_an_image() -> None:
    """§3.4: only rejected parts carry their image into the OPC UA event. Good parts
    get a result without one -- which is what keeps images inside the single
    permitted channel rather than requiring a second."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/truth/"):
            return httpx.Response(200, json={"status": "ok"})
        reject = b"A-00000007" in request.content
        return httpx.Response(
            200,
            json={
                "disposition": "reject" if reject else "good",
                "defect_class": "gap" if reject else None,
                "confidence": 0.88,
                "confidences": {},
                "model_version": "simulated-1",
            },
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
    through the request -- checked here by scanning the serialised body."""
    bodies: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        # Matched on the path, not a substring of the full URL: the default
        # inspection_url's host is "inspection-service", which itself contains
        # "/inspect" right after the scheme's "//" and would false-match the
        # truth-channel request too.
        if request.url.path == "/inspect":
            bodies.append(request.content)
            return httpx.Response(
                200,
                json={
                    "disposition": "good",
                    "defect_class": None,
                    "confidence": 0.9,
                    "confidences": {},
                    "model_version": "simulated-1",
                },
            )
        return httpx.Response(200, json={"status": "ok"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await InspectionClient(
            Settings(reject_rate=1.0, image_width=64, image_height=64), http
        ).produce("A-1", SIM_TS)

    assert bodies
    for body in bodies:
        assert b"defect" not in body
        assert b"truth" not in body
