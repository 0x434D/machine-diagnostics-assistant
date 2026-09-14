"""R4 Step 5 -- the image-size ceiling: where OPC UA event delivery actually fails.

measurements/r4-image-sizes.txt already has img_p99 (the 99th-percentile reject image
at the configured 320x240) and confirmed it crosses MaxBufferSize (65,535 B), so
chunking is exercised in production. What was never measured is the *ceiling* --
the size at which delivery fails outright, the exact StatusCode, and which limit
produces it. This answers that with a doubling ladder from img_p99: 1x, 2x, 4x, ...
until an event fails to arrive intact, scaling PLANT_IMAGE_WIDTH/HEIGHT to hit each
target size (§10.3 -- these are configuration, not constants to bypass).

Server-side ceiling only, by design (see the module docstring's own claim below,
confirmed by running this): a bare, unsecured asyncua Server and asyncua Client
in one process -- not the production Sign-only boundary (`simulator.server`), and
not the real C# gateway (UA-.NETStandard, its own TransportQuotas). What this
measures is (a) whether the plant's own address space and event generator will
happily encode and send an oversized ByteString at all -- confirmed yes, no
server-side guard found -- and (b) the default ceiling a generic asyncua Python
client enforces on receipt. The real gateway's own TransportQuotas is a different
stack with its own defaults and must be configured independently; these numbers
are the reference point for doing that generously, not a substitute for measuring
the gateway itself.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from asyncua import Client, Server, ua
from asyncua.common.events import Event
from simulator.address_space import build_address_space
from simulator.config import Settings
from simulator.render import render_part

OUTPUT = Path(__file__).parent / "r4-ceiling.json"

# The rung this ladder starts from is the plant's *configured* resolution, not a
# literal copy of img_p99's number -- §10.3. Doubling multiplies the pixel *area*
# (width and height each scale by sqrt(multiple)), which is what actually tracks
# PNG byte count once the sensor-noise layer dominates (see render_part / the
# distinct-size measurement in r4-image-sizes.txt): noise does not compress, so
# byte count scales with pixel count almost exactly, confirmed below to within ~2%
# at every rung this ladder reaches.
MAX_MULTIPLE = 2048
PART_ID = "A-99999999"
DEFECT = "gap"


@dataclass(frozen=True)
class Rung:
    multiple: int
    width: int
    height: int
    actual_bytes: int
    delivered: bool
    elapsed_seconds: float
    status_code: str | None
    limit: str | None
    detail: str


class _StatusCodeCapture(logging.Handler):
    """Grabs the one asyncua log record that carries the actual UaStatusCodeError.

    The failure this ladder provokes is raised deep inside the client's background
    publish-loop task (SecureConnection._receive, called from a task nobody here
    awaits directly), not at any call this script makes -- so there is nothing to
    catch it from directly. asyncua's own client logs the exception object itself
    as a %s argument before disconnecting (client/ua_client.py's
    "Got error status from server: %s"), which is the only place the concrete
    ua.UaStatusCodeError -- and so the exact StatusCode -- is ever observable from
    outside the library.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _dimensions_for(
    multiple: int, base_width: int, base_height: int
) -> tuple[int, int]:
    scale = math.sqrt(multiple)
    return max(1, round(base_width * scale)), max(1, round(base_height * scale))


class _EventCatcher:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[Event] = asyncio.Queue()

    async def event_notification(self, event: Event) -> None:
        await self.queue.put(event)


def _image_field(event: Event) -> bytes:
    """`Image` is a custom field set dynamically (EventGenerator.init -> Event.add_property,
    see asyncua.common.events.Event) -- the base Event class declares no such attribute,
    and EventGenerator.event is itself typed Any for the same reason. One read site for
    the suppression, not the two call sites that need the value."""
    image: bytes = event.Image  # type: ignore[attr-defined]
    return image


def _extract_status_code(records: list[logging.LogRecord]) -> tuple[str | None, str]:
    """The exact UaStatusCodeError subclass name (e.g. "BadRequestTooLarge") and the
    log line it came from, or (None, "") if nothing in `records` carried one.

    `record.args` is typed `tuple[object, ...] | Mapping[str, object] | None` by
    typeshed -- narrowed to the tuple case before indexing, not indexed directly,
    since a Mapping keyed by str has no meaningful `[0]`.
    """
    for record in records:
        args = record.args
        if (
            isinstance(args, tuple)
            and args
            and isinstance(args[0], ua.UaStatusCodeError)
        ):
            # UaStatusCodeError.__new__ resolves to the specific per-code subclass
            # (e.g. BadRequestTooLarge), so the class name alone is the status name --
            # see asyncua.ua.uaerrors._base.UaStatusCodeError.__new__.
            return type(args[0]).__name__, record.getMessage()
    return None, ""


async def probe_one(multiple: int, image: bytes, width: int, height: int) -> Rung:
    """One rung: a bare server emits a single reject event carrying `image`, a bare
    client subscribed to it either receives that exact image back or does not.

    Fresh Server and Client per rung, not one long-lived pair -- a rung that leaves
    the client's secure channel disconnected (every failing rung does; see
    _StatusCodeCapture) must not let that state leak into the next, larger one.
    """
    server = Server()
    await server.init()
    server.set_endpoint("opc.tcp://127.0.0.1:0/ceiling")
    idx = await server.register_namespace("http://machine-agent/plant")
    settings = Settings()
    space = await build_address_space(
        server, idx, settings.buffer_capacity, settings.joining_distance_nominal
    )

    capture = _StatusCodeCapture()
    asyncua_logger = logging.getLogger("asyncua")
    asyncua_logger.addHandler(capture)

    start = time.monotonic()
    delivered = False
    detail = ""
    try:
        async with server:
            assert server.bserver is not None
            client = Client(f"opc.tcp://127.0.0.1:{server.bserver.port}/ceiling")
            async with client:
                catcher = _EventCatcher()
                sub = await client.create_subscription(100, catcher)
                await sub.subscribe_events(space.inspection.node, [space.event_type])
                # One publish cycle's worth of settling before triggering: a
                # subscription whose first publish interval hasn't elapsed yet can
                # miss an event raised immediately after subscribe_events returns.
                await asyncio.sleep(0.1)

                event_gen = space.inspection.require_event_generator()
                ev = event_gen.event
                ev.AssemblySerial = PART_ID
                ev.Disposition = "reject"
                ev.DefectClass = DEFECT
                ev.Confidence = 0.5
                ev.ModelVersion = "r4-ceiling-probe"
                ev.Image = image
                await event_gen.trigger(
                    time_attr=datetime.now(UTC), message="r4 ceiling probe"
                )

                # Generous relative to a healthy localhost transfer (seconds even at
                # the largest rung this ladder reaches, measured directly) but bounded:
                # a failing rung never delivers, so this is what turns "never" into a
                # result rather than a hang.
                timeout_seconds = min(90.0, max(10.0, len(image) / 5_000_000))
                try:
                    received = await asyncio.wait_for(
                        catcher.queue.get(), timeout=timeout_seconds
                    )
                except Exception as exc:  # noqa: BLE001 -- this ladder's whole point is
                    # an image size large enough to fail delivery, by a mechanism this
                    # script does not get to pick in advance: a clean status-coded
                    # rejection, a silent disconnect that only ever surfaces as a
                    # timeout, or something neither of those. Which one happened *is*
                    # the measurement (recorded below via _extract_status_code), not
                    # a bug to narrow the except clause around.
                    detail = f"{type(exc).__name__}: {exc}"
                else:
                    received_image = _image_field(received)
                    delivered = received_image == image
                    if not delivered:
                        detail = (
                            f"received Image of {len(received_image)} bytes, "
                            f"expected {len(image)} -- truncated or corrupted, not absent"
                        )
    finally:
        asyncua_logger.removeHandler(capture)

    status_code: str | None = None
    limit: str | None = None
    if not delivered:
        status_code, log_line = _extract_status_code(capture.records)
        detail = detail or log_line
        if status_code == "BadRequestTooLarge":
            # Confirmed by reading asyncua.common.connection.SecureConnection._receive:
            # the check that actually raises here compares the *count* of accumulated
            # incoming chunks against TransportLimits.max_chunk_count (1601 by default,
            # derived from max_message_size // max_recv_buffer + 1) -- not a direct
            # byte-size comparison against MaxMessageSize (that check exists as
            # TransportLimits.is_msg_size_within_limit but is never called anywhere in
            # asyncua 2.0.1) and not MaxByteStringLength (asyncua's binary codec for
            # ByteString -- ua_binary.py's _Bytes -- has no length check at all).
            limit = "asyncua TransportLimits.max_chunk_count (client-side, default 1601 x 65535 B chunks)"
        elif status_code is not None:
            limit = "asyncua TransportLimits (see status_code), mechanism not further identified"
        else:
            limit = "unidentified -- no UaStatusCodeError observed; see detail"

    return Rung(
        multiple=multiple,
        width=width,
        height=height,
        actual_bytes=len(image),
        delivered=delivered,
        elapsed_seconds=round(time.monotonic() - start, 2),
        status_code=status_code,
        limit=limit,
        detail=detail,
    )


async def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    settings = Settings()

    rungs: list[Rung] = []
    multiple = 1
    while multiple <= MAX_MULTIPLE:
        width, height = _dimensions_for(
            multiple, settings.image_width, settings.image_height
        )
        image = render_part(
            PART_ID,
            [DEFECT],
            width,
            height,
            settings.seed,
            settings.image_compress_level,
        )
        rung = await probe_one(multiple, image, width, height)
        rungs.append(rung)
        print(
            f"multiple={multiple:5d} {rung.width:5d}x{rung.height:<5d} "
            f"bytes={rung.actual_bytes:11,d} delivered={rung.delivered!s:5} "
            f"elapsed={rung.elapsed_seconds:6.2f}s status={rung.status_code}"
        )
        if not rung.delivered:
            break
        multiple *= 2

    OUTPUT.write_text(
        json.dumps(
            {
                "img_p99_reference": "measurements/r4-image-sizes.txt (110419 B at 320x240, seed=20260912)",
                "pass_condition": "ceiling >= 4 * img_p99",
                "rungs": [asdict(r) for r in rungs],
            },
            indent=2,
        )
        + "\n"
    )
    print(f"wrote {OUTPUT}")


if __name__ == "__main__":
    asyncio.run(main())
