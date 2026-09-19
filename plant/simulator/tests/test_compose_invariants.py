"""Two of CLAUDE.md's invariants, as assertions rather than prose.

Compose is the first artefact in the repository that makes them checkable at all, so
they live here rather than arriving as a retrofit once something has already violated
one. Parsed as YAML, never matched as strings: a commented-out `field-net` satisfies a
grep, and a vacuous check is the defect this project keeps finding in its own work.
"""

import os
from pathlib import Path
from typing import cast
from urllib.parse import urlparse

import pytest
from _compose_yaml import load as _load
from _compose_yaml import services as _services
from simulator.config import Settings
from simulator.pki import PARTIES

REPO = Path(__file__).resolve().parents[3]
PLANT_COMPOSE = REPO / "plant" / "compose.yml"
MAKEFILE = REPO / "Makefile"
# The env file Compose and the simulator share. `.env` itself is gitignored, so the
# example is the only committed copy and the only one a test can hold still.
PLANT_ENV_EXAMPLE = REPO / "plant" / ".env.example"

# One Compose file per stack, at the stack's own root. Globbed rather than listed, so a
# third stack is covered the day it appears; every test below asserts this list is
# non-empty first, so the day the glob stops matching is a failure and not a green run.
COMPOSE_FILES = sorted(REPO.glob("*/compose.yml"))

BOUNDARY_CONTAINERS = {"line-simulator", "edge-gateway"}


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


def _build_spec(compose_dir: Path, build: dict[str, object]) -> tuple[str, str, str]:
    """One Compose `build:` block as (context, Dockerfile, PACKAGE), both paths written
    from the repository root.

    Compose resolves `context` against the directory its own file sits in and
    `dockerfile` against that context, so one image is spelled three different ways
    across this repository -- `.` from plant/, `..` from diagnostics/, and a
    repository-root pair in the Makefile. Resolving before comparing is what makes them
    agree or disagree honestly rather than textually.
    """
    context = (compose_dir / str(build["context"])).resolve()
    dockerfile = (context / str(build.get("dockerfile") or "Dockerfile")).resolve()
    args = cast(dict[str, object], build.get("args") or {})
    return (
        os.path.relpath(context, REPO),
        os.path.relpath(dockerfile, REPO),
        str(args.get("PACKAGE", "")),
    )


def _makefile_images() -> dict[str, tuple[str, str, str]]:
    """The Makefile's `IMAGES` list, keyed by image name, in `_build_spec`'s shape."""
    lines = MAKEFILE.read_text().splitlines()
    rows: list[str] = []
    for index, line in enumerate(lines):
        if not line.startswith("IMAGES :="):
            continue
        block = [line.split(":=", 1)[1]]
        while block[-1].rstrip().endswith("\\"):
            index += 1
            block.append(lines[index])
        rows = " ".join(part.strip().removesuffix("\\") for part in block).split()
        break
    assert rows, "the Makefile has no IMAGES list; this test now proves nothing"

    images: dict[str, tuple[str, str, str]] = {}
    for row in rows:
        name, context, dockerfile, *package = row.split(":")
        assert name not in images, f"{name} has two rows in the Makefile's IMAGES"
        images[name] = (
            os.path.normpath(context),
            os.path.normpath(dockerfile),
            package[0] if package else "",
        )
    return images


def test_every_image_the_stacks_build_has_a_row_in_the_makefiles_image_list() -> None:
    """`make images` builds what `make scan-images` scans, and that scan is the whole of
    the vulnerability answer for the containers -- so an image with no row in the
    Makefile is an image nothing scans, and nothing goes red to say so.

    Not hypothetical. `diagnostics/Dockerfile` moved to a repository-root context when
    `knowledge/` became a workspace member; Compose followed and the Makefile did not,
    and the MCP server and the issuer then arrived with no row at all. What was counting
    the images was a comment, and a comment counts nothing.

    Keyed on (context, Dockerfile, PACKAGE) rather than on the service name, because
    `pki-init` and `line-simulator` are one image under two commands, and skipping the
    services with no `build:` at all is what leaves out the Postgres the diagnostics
    stack pulls by digest.
    """
    assert COMPOSE_FILES, "no compose.yml found; every test in this file is vacuous"

    built: dict[tuple[str, str, str], set[str]] = {}
    for path in COMPOSE_FILES:
        for name, service in _services(_load(path)).items():
            build = service.get("build")
            if build is None:
                continue
            assert isinstance(build, dict), (
                f"{name} uses Compose's short `build:` form, which this test cannot "
                "resolve; write the long form or teach it the short one"
            )
            built.setdefault(_build_spec(path.parent, build), set()).add(name)

    assert built, (
        "no service in either stack declares a build; this test proves nothing"
    )
    declared = _makefile_images()

    unscanned = set(built) - set(declared.values())
    assert not unscanned, (
        "built by a Compose service and absent from the Makefile's IMAGES, so nothing "
        "builds or scans it: "
        + "; ".join(f"{spec} for {sorted(built[spec])}" for spec in sorted(unscanned))
    )

    phantom = set(declared.values()) - set(built)
    assert not phantom, (
        "in the Makefile's IMAGES and built by no Compose service, so `make images` "
        "spends time on something neither stack runs: "
        + "; ".join(str(spec) for spec in sorted(phantom))
    )


def test_only_the_simulator_and_the_gateway_ever_join_field_net() -> None:
    """The one claim the whole two-stack split exists to support: exactly two
    containers meet on `field-net`, and OPC UA is the only thing that crosses.

    Asserted as equality, not as "nothing outside the pair". Both stacks' Compose files
    exist now, so the weaker form is no longer the only one that can be true -- and it
    only ever caught a third joiner. Equality catches the other direction too: an
    edge-gateway that quietly *left* field-net is a diagnostics stack that has stopped
    reading the plant, which every test downstream of it would report as no data rather
    than as a broken boundary.
    """
    assert COMPOSE_FILES, f"no compose files under {REPO} -- this test proves nothing"

    members = {
        name
        for path in COMPOSE_FILES
        for name, service in _services(_load(path)).items()
        if "field-net" in _networks_of(service)
    }
    assert members == BOUNDARY_CONTAINERS, (
        f"field-net holds {members or 'nothing'}; CLAUDE.md says exactly "
        f"{BOUNDARY_CONTAINERS}. A third joiner is an architecture change -- the "
        "inspection service above all, which carries the ground-truth side channel -- "
        "and a missing one is the boundary this repository is built around going quiet"
    )


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


def test_the_ground_truth_volume_is_mounted_only_by_the_plants_simulator() -> None:
    """**The volume `test_no_stack_mounts_a_volume_another_stack_owns` was written for.**

    Its docstring names this case: ground truth becomes a stored artefact in M2, and the
    moment a diagnostics service mounts it every evaluation number in the project is
    worthless -- the analysis would be graded against a file it could have read.

    This is the positive half, and it is the half that is checkable today. The negative
    one only fails once someone actually writes the mount; this one fails the moment the
    volume stops being declared, stops being mounted by the simulator, or starts being
    mounted by anything else -- the inspection service above all, which already carries
    the truth side channel and is the container a reader would reach for first.
    """
    plant = _load(PLANT_COMPOSE)
    assert "plant-ground-truth" in cast(dict[str, object], plant.get("volumes") or {})

    mounted_by = {
        name
        for path in COMPOSE_FILES
        for name, service in _services(_load(path)).items()
        if "plant-ground-truth" in _mount_sources(service)
    }
    assert mounted_by == {"line-simulator"}, (
        f"plant-ground-truth is mounted by {mounted_by or 'nothing'}; §3.6 gives it to "
        "the one container that writes it and to nothing else"
    )


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


def test_the_env_file_compose_and_settings_share_loads_into_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`plant/.env.example` says "Copy to plant/.env and edit" on its first line, and
    doing that used to abort `make check` during collection.

    Compose reads that file for interpolation and the simulator reads it for its own
    settings, so it carries keys that are not this object's: HOST_UID and HOST_GID
    decide the uid every plant container runs as, and PLANT_HMI_PORT is the host port
    the screen is published on rather than `hmi_server_port`. Under pydantic-settings'
    default `extra="forbid"` all three raise "Extra inputs are not permitted", so
    `Settings()` failed at `conftest.py` import and the whole plant suite failed to
    collect -- for a developer following the documented setup, and for the scheduled
    authenticity job, which writes the same two keys the same way.

    Asserted against the shipped example rather than a list restated here: the file is
    the documentation, and a key added to it that breaks the simulator should fail
    here rather than on the next person's laptop.
    """
    (tmp_path / ".env").write_text(PLANT_ENV_EXAMPLE.read_text())
    # chdir rather than Settings(_env_file=...): `env_file=".env"` is relative, so the
    # working directory IS the mechanism in production, and an override would test a
    # path the plant never takes.
    monkeypatch.chdir(tmp_path)

    settings = Settings()

    # Not vacuous: the file's own PLANT_ values have to arrive, or "it loaded" would
    # also be true of a Settings that ignored the file entirely.
    assert settings.takt_seconds == 6.0
    assert settings.hmi_server_port == 8200

    # And the three keys that used to break it are genuinely in the file, so this test
    # cannot quietly stop covering them.
    shipped = PLANT_ENV_EXAMPLE.read_text()
    for key in ("HOST_UID=", "HOST_GID=", "PLANT_HMI_PORT="):
        assert key in shipped, f"{key} left .env.example; this test now proves less"
