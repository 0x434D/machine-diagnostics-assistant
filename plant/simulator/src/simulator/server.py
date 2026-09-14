"""The plant's single exposed port (§2.1, §4.6).

The endpoint URL is simultaneously a bind address, a discovery advertisement, a
session-validation token and a certificate constraint. One name, one port,
everywhere -- see the plan's "one-name boundary".
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import timedelta
from pathlib import Path

import httpx
from asyncua import Server, ua
from asyncua.crypto.truststore import TrustStore
from asyncua.crypto.validator import CertificateValidator, CertificateValidatorOptions

from simulator import hmi, status
from simulator.address_space import (
    BUFFERS,
    AddressSpace,
    build_address_space,
    publish_clock,
)
from simulator.buffers import Buffer
from simulator.carriers import CarrierPool
from simulator.clock import SimulatedClock
from simulator.config import ClockConfig, Settings
from simulator.historian import Ledger, LedgerWriter, attach_historian
from simulator.inspection_client import InspectionClient
from simulator.line import Line, run_catchup, run_live
from simulator.stations import (
    FeedingStation,
    InspectionStation,
    JoiningStation,
    OutfeedStation,
    ProduceFn,
    Station,
)

NAMESPACE = "http://machine-agent/plant"

# The mount point of the `plant-history` named volume in plant/compose.yml. Not a
# Settings field: nothing but this entry point ever names it, and the volume mount is
# the contract, not a knob.
HISTORY_DB = Path("/data/history.db")

log = logging.getLogger(__name__)


def _log(event: str, **fields: object) -> None:
    """One structured JSON object per line (docs/ENGINEERING.md §10).

    Built with json.dumps rather than a quoted format string, which produces invalid
    JSON the first time a field value contains a quote.
    """
    log.info(json.dumps({"event": event, **fields}))


async def build_server(settings: Settings) -> tuple[Server, AddressSpace]:
    """An unstarted OPC UA server on `settings.endpoint_url`, Sign-only, trusting only
    the certificates in the shared trust store, with §4.1's address space built.

    Assumes `settings.pki_root` holds the layout `simulator.pki` writes. Raises
    FileNotFoundError if the server's own certificate or key is missing from it.
    """
    pki = Path(settings.pki_root)

    server = Server()
    await server.init()

    # The host component of this URL is what asyncua binds to, what it advertises in
    # discovery, and what a client's session validation is checked against. It must
    # equal the Compose service name, and the port must be identical inside and out --
    # a published 14840:4840 would make the host dial a port the server never
    # advertises, which is §12's BadTcpEndpointUrlInvalid trap verbatim.
    # test_compose_invariants.py pins both halves against the Compose file.
    server.set_endpoint(settings.endpoint_url)
    await server.set_application_uri(settings.application_uri)
    server.set_server_name("machine-agent plant")

    # UA-.NETStandard's DirectoryCertificateStore layout, which the gateway's own store
    # cannot be configured away from; asyncua takes explicit paths, so it follows.
    await server.load_certificate(
        str(pki / "line-simulator" / "certs" / "line-simulator.der")
    )
    await server.load_private_key(
        str(pki / "line-simulator" / "private" / "line-simulator.pem")
    )

    # Sign only. No open endpoint, and no SignAndEncrypt (§10.5 defers it).
    server.set_security_policy([ua.SecurityPolicyType.Basic256Sha256_Sign])

    # Mutual trust from the shared, pre-seeded trust directory, so a restart does not
    # need re-trusting by hand (§14). The directory is `trusted/certs`, not `trusted`:
    # TrustStore globs each location non-recursively for *.der / *.pem.
    trust = TrustStore([pki / "trusted" / "certs"], [])
    # TrustStore.load carries no return annotation in asyncua 2.0.1 -- the package ships
    # py.typed, so mypy reads it as untyped rather than ignoring it. Nothing to fix on
    # this side; the alternative is loosening disallow_untyped_calls for this module,
    # which would also hide our own untyped calls.
    await trust.load()  # type: ignore[no-untyped-call]
    server.set_certificate_validator(
        CertificateValidator(
            CertificateValidatorOptions.TRUSTED_VALIDATION
            | CertificateValidatorOptions.PEER_CLIENT,
            trust,
        )
    )

    # asyncua rewrites the host of every advertised endpoint to the socket address it
    # sees the client arrive on. Inside Docker that is this container's IP on whichever
    # network the client reached it over -- never the one name the boundary is built
    # around -- so a client handed that endpoint dials something no certificate covers.
    #
    # Measured with this line removed, twice: every asyncua cell of the R3 matrix came
    # back identical, and so did the one that actually decides it -- UA-.NETStandard from
    # inside field-net, which establishes a session either way. So this changes nothing
    # for either client in the system, and the reason is specific rather than lucky:
    # InternalServer._mangle_endpoint_url reads the request's own EndpointUrl first and
    # returns before it consults the source IP, and both clients send one.
    #
    # Kept as a deliberate guard, not as insurance against the unknown: a GetEndpoints
    # request may legally omit EndpointUrl, and a client that does gets handed this
    # container's IP on whichever network it arrived over -- a name no certificate covers,
    # failing later and somewhere else. One line against a silent failure, with the
    # measurement recorded in measurements/r3-notes.txt §2 either way.
    server.set_match_discovery_client_ip(False)

    idx = await server.register_namespace(NAMESPACE)
    space = await build_address_space(
        server, idx, settings.buffer_capacity, settings.joining_distance_nominal
    )
    return server, space


def build_line(writer: LedgerWriter, settings: Settings, produce: ProduceFn) -> Line:
    """§3.1's line: four stations in line order, the three buffers between them, and
    one circulating carrier pool.

    The stations are listed rather than built from `STATION_SIGNALS`: each one is a
    different class and S3 takes the inspection call, so a table would be four rows of
    data and a four-way branch. Two guards make the spelling safe anyway --
    `LedgerWriter.station` raises for a code §4.1's tree does not carry, and
    `Station.__init__` raises for one the settings name no takt for -- and `Line`
    itself refuses a buffer whose published upstream/downstream disagree with where it
    is placed here.

    Every station is seeded from the same `settings.seed`; `Station.__init__` folds the
    station code in, so the four draw independently and reproducibly (§3.6).
    """
    stations: list[Station] = [
        FeedingStation(writer.station("S1_Feeding"), settings, settings.seed),
        JoiningStation(writer.station("S2_Joining"), settings, settings.seed),
        InspectionStation(
            writer.station("S3_Inspection"), settings, settings.seed, produce
        ),
        OutfeedStation(writer.station("S4_Outfeed"), settings, settings.seed),
    ]
    buffers = [
        Buffer(buffer_id, settings.buffer_capacity, upstream, downstream)
        for buffer_id, upstream, downstream in BUFFERS
    ]
    return Line(
        stations,
        buffers,
        CarrierPool(settings.carrier_count),
        timedelta(seconds=settings.state_transition_seconds),
    )


async def main() -> None:
    # WARNING at the root, INFO only for this module. asyncua logs the full decoded
    # PublishResult -- every notification, every field -- at INFO, which at catch-up
    # rates is several kB per part and rotates the container's whole 30 MB of retained
    # log away in seconds. Measured, not feared: the first run of this stack produced
    # 260 kB of it before the first `docker compose logs` returned.
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    log.setLevel(logging.INFO)
    settings = Settings()

    server, space = await build_server(settings)

    # From settings, not from ClockConfig.DEFAULT_HISTORY_DEPTH: the default already
    # comes from that constant (see config.py), and reading the constant here instead
    # would make PLANT_HISTORY_DEPTH_HOURS a variable that changes nothing.
    clock = SimulatedClock(
        ClockConfig(
            history_depth=timedelta(hours=settings.history_depth_hours),
            catchup_speed=settings.catchup_speed,
        )
    )
    ledger = Ledger()
    # Every boot generates its own 33 h of history, anchored to this boot's instant, so
    # anything a previous boot left behind is a second, overlapping simulated world in
    # the same tables. Found by running the stack twice: the second boot's
    # reconciliation counted 48,845 rows against a ledger of 19,801 and refused to
    # start, which is the right failure and the wrong cause. The plant does not record
    # history, it generates it. plant-history is a volume because the container
    # filesystem is read-only and this database passes 100 MB, not because anything is
    # meant to survive a boot -- the diagnostics stack answers from its own Postgres.
    HISTORY_DB.unlink(missing_ok=True)
    # The same argument, and the same volume. A status file a previous boot left behind
    # reports phase "live" and that boot's ledger while this one is still generating; only
    # written_wall gives it away, and simulator.status prints the file without comment.
    # Absent is honest, stale is not.
    status.STATUS_FILE.unlink(missing_ok=True)
    storage = await attach_historian(
        server,
        space,
        HISTORY_DB,
        settings.history_page_size,
        clock.history_start - timedelta(seconds=settings.takt_seconds),
        ledger,
    )
    writer = LedgerWriter(space, ledger)

    async with (
        server,
        httpx.AsyncClient(timeout=settings.inspection_timeout_seconds) as http,
    ):
        # One client for both phases: its verdict for a part is a pure function of
        # (seed, part_id), so catch-up and live cannot shift each other's draws
        # however many parts precede them (§3.6, and test_defect_draw_does_not_depend
        # _on_prior_calls). M1 built a second one with `seed ^ 1` because its two
        # generators were separate runs; the queue makes them one.
        line = build_line(writer, settings, InspectionClient(settings, http).produce)
        _log(
            "catchup.start",
            endpoint=settings.endpoint_url,
            depth_hours=settings.history_depth_hours,
            catchup_speed=settings.catchup_speed,
        )
        # The number R1 budgets at <=180 s, measured here rather than inferred from two
        # log timestamps -- the lines carry none of their own, since the container
        # runtime already stamps every one.
        started = time.monotonic()

        # A TaskGroup, not a bare create_task: a status writer that started failing would
        # otherwise raise into a task nobody awaits, and asyncio would report it as
        # "exception was never retrieved" while the plant carried on looking healthy.
        #
        # Opened before generation, not after it. Catch-up is the longest phase of a boot
        # and the one the demo exists to watch, and simulator.status is the only way to
        # see it without an OPC UA client at hand (Clock.Phase is on the wire per §4.1,
        # but nothing at a shell prompt speaks OPC UA); started afterwards, the publisher
        # left `python -m simulator.status` answering "does not exist -- is the simulator
        # running?" for two and a half minutes while it was running.
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(
                status.publish(status.STATUS_FILE, clock, settings, ledger, line)
            )
            # Same cadence as the status writer above, started alongside it rather
            # than on a second timer -- see address_space.publish_clock. This is
            # what lets §4.3's handshake (watch State -> read Clock.Phase -> ...)
            # actually see catch-up in progress rather than a stale first value.
            tasks.create_task(
                publish_clock(space, clock, settings.status_interval_seconds)
            )
            # §15's plant HMI, in this process rather than in a container of its own
            # (D5). Started here, alongside the two above and before generation, for
            # the same reason the status writer is: catch-up is the longest phase of a
            # boot and the one worth watching, and a screen that only came up
            # afterwards would show a blank page for the whole of it.
            #
            # Its own cadence is hmi_interval_seconds, not this one: the status file is
            # read by a human at a shell prompt and the screen is drawn continuously,
            # and one number cannot be right for both.
            tasks.create_task(hmi.serve(line, clock, settings))
            history_end = await run_catchup(line, writer, clock, settings, storage)
            catchup_wall = time.monotonic() - started
            _log(
                "catchup.done",
                # Per stream, not a total: a total that is short says only that
                # something was lost, and at 25 streams the next question is always
                # which one. run_catchup has already refused to return if these
                # disagree with the historian.
                rows=ledger.rows_by_name(),
                events=ledger.events,
                images=ledger.images,
                image_bytes=ledger.image_bytes,
                history_end=history_end.isoformat(),
                catchup_wall_seconds=round(catchup_wall, 1),
                # Simulated seconds generated per wall second -- what catch-up actually
                # achieved, against the catchup_speed the clock was configured with.
                achieved_multiple=round(
                    clock.history_depth.total_seconds() / catchup_wall, 1
                ),
            )

            # Generation finishes before the clock's own catch-up window closes, and
            # run_live emits nothing until it does. Sleeping the exact remainder rather
            # than polling the phase: the clock already knows when it flips, and the plan
            # asks for booting -> catchup -> live to be legible in the log rather than a
            # silence that reads the same as a hang.
            remaining = (
                clock.boot_wall + clock.catchup_duration - clock.wall
            ).total_seconds()
            if remaining > 0:
                await asyncio.sleep(remaining)
            _log(
                "live.start",
                simulated_now=clock.now().isoformat(),
                seam_seconds=round((clock.now() - history_end).total_seconds(), 1),
                takt_seconds=settings.takt_seconds,
            )

            # No start instant and no re-seeding: live continues the same queue, whose
            # next due cycle is the `history_end` logged above (see run_live).
            tasks.create_task(run_live(line, writer, clock, settings))


if __name__ == "__main__":
    asyncio.run(main())
