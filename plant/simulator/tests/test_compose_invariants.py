"""Two of CLAUDE.md's invariants, as assertions rather than prose.

Compose is the first artefact in the repository that makes them checkable at all, so
they live here rather than arriving as a retrofit once something has already violated
one. Parsed as YAML, never matched as strings: a commented-out `field-net` satisfies a
grep, and a vacuous check is the defect this project keeps finding in its own work.
"""

from pathlib import Path
from typing import cast
from urllib.parse import urlparse

import yaml
from simulator.config import Settings
from simulator.pki import PARTIES

REPO = Path(__file__).resolve().parents[3]
PLANT_COMPOSE = REPO / "plant" / "compose.yml"

# One Compose file per stack, at the stack's own root. `diagnostics/compose.yml` does
# not exist yet (Task 7 brings it); every test below asserts this list is non-empty
# first, so the day the glob stops matching is a failure and not a green run.
COMPOSE_FILES = sorted(REPO.glob("*/compose.yml"))

BOUNDARY_CONTAINERS = {"line-simulator", "edge-gateway"}


def _load(path: Path) -> dict[str, object]:
    return cast(dict[str, object], yaml.safe_load(path.read_text()))


def _services(doc: dict[str, object]) -> dict[str, dict[str, object]]:
    return cast(dict[str, dict[str, object]], doc.get("services") or {})


def _networks_of(service: dict[str, object]) -> set[str]:
    """Compose accepts both the list and the mapping form; a test that only understood
    one would quietly stop seeing a service that switched to the other."""
    networks = service.get("networks")
    if isinstance(networks, dict):
        return set(networks)
    if isinstance(networks, list):
        return {str(n) for n in networks}
    return set()


def _mount_sources(service: dict[str, object]) -> list[str]:
    sources = []
    for mount in cast(list[object], service.get("volumes") or []):
        if isinstance(mount, str):
            sources.append(mount.split(":", 1)[0])
        elif isinstance(mount, dict):
            source = mount.get("source")
            if isinstance(source, str):
                sources.append(source)
    return sources


def test_only_the_simulator_and_the_gateway_ever_join_field_net() -> None:
    """The one claim the whole two-stack split exists to support: exactly two
    containers meet on `field-net`, and OPC UA is the only thing that crosses.

    Asserted as "nothing outside the pair", not "exactly the pair", because
    edge-gateway's Compose file arrives with Task 7. That is the half that is true
    today and it is the half that catches a third joiner -- the inspection service
    above all, which carries the ground-truth side channel and must stay on plant-net.
    """
    assert COMPOSE_FILES, f"no compose files under {REPO} -- this test proves nothing"

    members = {
        name
        for path in COMPOSE_FILES
        for name, service in _services(_load(path)).items()
        if "field-net" in _networks_of(service)
    }
    assert members - BOUNDARY_CONTAINERS == set(), (
        f"{members - BOUNDARY_CONTAINERS} joined field-net; only "
        f"{BOUNDARY_CONTAINERS} may, and adding a third is an architecture change"
    )
    assert "line-simulator" in members


def test_no_stack_mounts_a_volume_another_stack_owns() -> None:
    """No shared database, no shared volume -- the diagnostics stack answers from its
    own history, never from the plant's files.

    The volume this will actually catch is M2's: ground truth becomes a stored artefact
    then, and the moment a diagnostics service mounts it every evaluation number in the
    project is worthless. `pki/` is deliberately shared and deliberately not caught --
    it belongs to neither stack, sits outside both stack directories, and carries
    public certificates plus each party's own key.
    """
    assert COMPOSE_FILES, f"no compose files under {REPO} -- this test proves nothing"

    declared = {
        path: set(cast(dict[str, object], _load(path).get("volumes") or {}))
        for path in COMPOSE_FILES
    }
    for path in COMPOSE_FILES:
        foreign_volumes = {
            name for other, names in declared.items() if other != path for name in names
        }
        for service_name, service in _services(_load(path)).items():
            for source in _mount_sources(service):
                assert source not in foreign_volumes, (
                    f"{path.name}:{service_name} mounts {source!r}, a named volume "
                    "another stack declares"
                )
                for other in COMPOSE_FILES:
                    if other == path:
                        continue
                    resolved = (path.parent / source).resolve()
                    assert not resolved.is_relative_to(other.parent), (
                        f"{path.name}:{service_name} bind-mounts {source!r}, which "
                        f"reaches inside {other.parent.name}/"
                    )


def test_the_plants_history_volume_is_mounted_only_by_the_plant() -> None:
    """The positive half of the invariant above, which is checkable today rather than
    once a second Compose file exists."""
    plant = _load(PLANT_COMPOSE)
    assert "plant-history" in cast(dict[str, object], plant.get("volumes") or {})

    mounted_by = {
        name
        for path in COMPOSE_FILES
        for name, service in _services(_load(path)).items()
        if "plant-history" in _mount_sources(service)
    }
    assert mounted_by == {"line-simulator"}


def test_the_endpoint_is_one_name_and_one_port_everywhere() -> None:
    """The one-name boundary. Four independent mechanisms key off this single string --
    asyncua's bind address and discovery advertisement, the client's session
    validation, the certificate's URI SAN and its DNS SAN -- and changing it to satisfy
    one silently breaks another. This is the assertion that they are still one thing.
    """
    plant = _load(PLANT_COMPOSE)
    service = _services(plant)["line-simulator"]
    environment = cast(dict[str, str], service.get("environment") or {})
    url = urlparse(environment["PLANT_ENDPOINT_URL"])

    assert url.geturl() == Settings().endpoint_url, (
        "compose.yml restates simulator.config's default endpoint; they have drifted"
    )
    # The host component is the Compose service name, which is what Docker's embedded
    # DNS resolves for a peer on field-net.
    assert url.hostname == "line-simulator"
    assert url.hostname in PARTIES["line-simulator"].dns_names

    # "127.0.0.1:4840:4840" and "4840:4840" both end host:container.
    published = cast(list[str], service["ports"])
    assert len(published) == 1
    host_port, container_port = published[0].split(":")[-2:]
    assert host_port == container_port == str(url.port), (
        "the published port must be identical inside and out, or the host dials a "
        "port the server never advertises (BadTcpEndpointUrlInvalid)"
    )

    assert environment["PLANT_APPLICATION_URI"] == PARTIES["line-simulator"].app_uri
    assert Settings().application_uri == PARTIES["line-simulator"].app_uri
