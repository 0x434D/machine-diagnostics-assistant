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

from simulator import status
from simulator.address_space import AddressSpace, build_address_space
from simulator.clock import SimulatedClock
from simulator.config import ClockConfig, Settings
from simulator.historian import Ledger, attach_historian
from simulator.inspection_client import InspectionClient
from simulator.station_s3 import generate_history, run_live

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
    the certificates in the shared trust store, with S3's address space built.

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
    space = await build_address_space(server, idx)
    return server, space


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

    async with (
        server,
        httpx.AsyncClient(timeout=settings.inspection_timeout_seconds) as http,
    ):
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
        # see it (§4.5 keeps the clock off the wire); started afterwards, the publisher
        # left `python -m simulator.status` answering "does not exist -- is the simulator
        # running?" for two and a half minutes while it was running.
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(
                status.publish(status.STATUS_FILE, clock, settings, ledger)
            )
            history_end = await generate_history(
                space,
                clock,
                settings,
                InspectionClient(settings, http).produce,
                ledger,
                storage,
            )
            catchup_wall = time.monotonic() - started
            _log(
                "catchup.done",
                takt=ledger.takt,
                part_count=ledger.part_count,
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

            # A second inspection client with a distinguishing seed, mirroring
            # run_live's own `settings.seed ^ 1`: without it, changing the configured
            # history depth would change how many defect draws catch-up consumes and so
            # shift what live production produces for the same seed (§3.6).
            tasks.create_task(
                run_live(
                    space,
                    clock,
                    settings,
                    InspectionClient(settings, http, seed=settings.seed ^ 1).produce,
                    ledger,
                    start_index=ledger.events,
                    start_ts=history_end,
                )
            )


if __name__ == "__main__":
    asyncio.run(main())
