"""R3: endpoint URL vs Docker hostname. Every cell recorded with its exact StatusCode.

Four client positions x two security modes. The positions differ only in where the
client stands, because that is the variable the one-name boundary is sensitive to: the
same URL is a bind address, a discovery advertisement, a session-validation token and a
certificate constraint, and which of those bites depends on whether the client reaches
the server over field-net or through the published port.

Run from the plant workspace, which is where asyncua is resolvable:

    cd plant && uv run --frozen --package simulator python ../measurements/run_r3.py

`--probe` runs a single cell and is what the containerised rows re-enter this same file
to execute, so the client setup below has exactly one copy rather than one per position.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

from asyncua import Client, ua
from asyncua.crypto import security_policies

# Never from cwd: this is invoked from plant/ and re-entered from inside a container at
# a different path.
REPO = Path(__file__).resolve().parents[1]
MATRIX_FILE = REPO / "measurements" / "r3-matrix.json"

ENDPOINT = "opc.tcp://line-simulator:4840/plant"
GATEWAY_URI = "urn:machine-agent:diagnostics:edge-gateway"
GATEWAY_IMAGE = "machine-agent/edge-gateway:dev"
# What `docker compose -f plant/compose.yml build` names the simulator image: project
# name plus service name. Used only to borrow an image that already has asyncua and the
# PKI mount for the field-net row.
PLANT_IMAGE = "machine-agent-plant-line-simulator"

HOST_CELLS = (
    ("host-published-port", "opc.tcp://localhost:4840/plant"),
    ("host-etc-hosts", ENDPOINT),
)


async def try_connect(url: str, secure: bool, pki: Path) -> dict[str, object]:
    """One connection attempt, reported as a cell.

    Catches broadly on purpose, and this is the one place in the repository where that
    is right: the failure text is the measurement. A narrower except would record
    "failed" for the cases it did not name, which is exactly the information R3 exists
    to capture.
    """
    client = Client(url, timeout=10)
    try:
        if secure:
            # The server validates this against the client certificate's URI SAN; left
            # at asyncua's default it fails BadCertificateUriInvalid before trust is
            # ever consulted, and the cell would measure the wrong thing.
            client.application_uri = GATEWAY_URI
            await client.set_security(
                security_policies.SecurityPolicyBasic256Sha256,
                certificate=str(pki / "edge-gateway" / "certs" / "edge-gateway.der"),
                private_key=str(pki / "edge-gateway" / "private" / "edge-gateway.pem"),
                server_certificate=str(
                    pki / "line-simulator" / "certs" / "line-simulator.der"
                ),
                mode=ua.MessageSecurityMode.Sign,
            )
        async with client:
            await client.nodes.root.get_children()
    except Exception as exc:  # noqa: BLE001 -- the failure text IS the measurement
        return {"ok": False, "status": f"{type(exc).__name__}: {exc}"}
    return {"ok": True, "status": "Good"}


def _on_field_net(image: str, mounts: list[str], args: list[str]) -> dict[str, object]:
    """One containerised cell: `image` attached to field-net, reported as a cell.

    `--user` is not optional and is the reason this is one function rather than one per
    image. pki-init writes each party's private key 0600, owned by the uid that owns the
    bind-mounted pki/ (plant/.env.example, HOST_UID), so a container left on its image's
    own uid is refused its own key: measured as UnauthorizedAccessException on
    edge-gateway.pfx when the gateway row ran without it. That turns a cell red for a
    reason that has nothing to do with the boundary, which is the worst kind of
    measurement -- it looks like a finding.
    """
    out = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "field-net",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            *mounts,
            image,
            *args,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return {
        "ok": out.returncode == 0,
        "status": (out.stdout + out.stderr).strip()[-400:],
    }


def _image_exists(image: str) -> bool:
    return (
        subprocess.run(
            ["docker", "image", "inspect", image],
            capture_output=True,
            check=False,
        ).returncode
        == 0
    )


def gateway_cell(secure: bool) -> dict[str, object]:
    """Row 1: the real UA-.NETStandard client from inside field-net, which is the only
    client that matters in production. Pending, not failing, until Task 7's image and its
    --connect-test mode exist -- and the verdict counts this row the moment it does."""
    if not _image_exists(GATEWAY_IMAGE):
        return {
            "ok": False,
            "pending": True,
            "status": f"{GATEWAY_IMAGE} not built -- Task 7 closes this row",
        }
    return _on_field_net(
        GATEWAY_IMAGE,
        ["-v", f"{REPO / 'pki'}:/pki:ro"],
        ["--connect-test", ENDPOINT, "--security", "Sign" if secure else "None"],
    )


def field_net_cell(secure: bool) -> dict[str, object]:
    """Row 2: an asyncua client in a container on field-net, dialling the same name the
    gateway will. It does not substitute for row 1 -- UA-.NETStandard enforces
    checkDomain and a three-RDN subject comparison that asyncua does not -- but it does
    settle the parts that are the server's: that the listener is reachable on field-net
    under the service name at all, and that discovery hands back a URL the session
    accepts.

    Re-enters this same file's --probe rather than carrying a second copy of the client
    setup, which is why `measurements/` is mounted alongside `pki/`."""
    if not _image_exists(PLANT_IMAGE):
        return {
            "ok": False,
            "pending": True,
            "status": f"{PLANT_IMAGE} not built -- run docker compose build first",
        }
    return _on_field_net(
        PLANT_IMAGE,
        [
            "-v",
            f"{REPO / 'pki'}:/pki:ro",
            "-v",
            f"{REPO / 'measurements'}:/measurements:ro",
        ],
        [
            "python",
            "/measurements/run_r3.py",
            "--probe",
            ENDPOINT,
            "--pki",
            "/pki",
            *(["--secure"] if secure else []),
        ],
    )


async def run_matrix(pki: Path) -> dict[str, dict[str, object]]:
    matrix: dict[str, dict[str, object]] = {}
    for secure in (False, True):
        mode = "Sign" if secure else "NoSecurity"
        matrix[f"gateway-field-net/{mode}"] = gateway_cell(secure)
        matrix[f"asyncua-field-net/{mode}"] = field_net_cell(secure)
        for name, url in HOST_CELLS:
            matrix[f"{name}/{mode}"] = await try_connect(url, secure, pki)
    return matrix


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pki", type=Path, default=REPO / "pki")
    parser.add_argument(
        "--probe",
        metavar="URL",
        help="run one cell against URL and exit; used by the containerised rows",
    )
    parser.add_argument("--secure", action="store_true", help="--probe in Sign mode")
    args = parser.parse_args()

    if args.probe:
        cell = await try_connect(args.probe, args.secure, args.pki)
        print(cell["status"])
        return 0 if cell["ok"] else 1

    cells = await run_matrix(args.pki)

    # R3 passes when the boundary works from where it has to work, and "pending" is a
    # statement about this run rather than about the row: a row nobody could run is not a
    # failure, and the instant it becomes runnable it must count. Naming the field-net
    # rows here and filtering on pending -- rather than leaving the gateway row out of the
    # verdict -- is what stops this file reporting PASS on the day Task 7 builds the image
    # and its one row comes back BadCertificateHostNameInvalid.
    #
    # `bool(required)` because an empty requirement list makes all() vacuously true: if no
    # field-net client could be run at all, nothing about the boundary was measured and
    # that is not a pass either.
    pending = [name for name, cell in cells.items() if cell.get("pending")]
    required = [
        name
        for name in ("gateway-field-net/Sign", "asyncua-field-net/Sign")
        if not cells[name].get("pending")
    ]
    # Any one host path is enough. host-etc-hosts needs a `127.0.0.1 line-simulator` row
    # that no longer is a prerequisite -- host-published-port carries the claim (see
    # r3-notes.txt §1) -- so this must not be an all().
    host_sign = [name for name in cells if name.startswith("host-") and "Sign" in name]
    passed = (
        bool(required)
        and all(cells[name]["ok"] for name in required)
        and any(cells[name]["ok"] for name in host_sign)
    )

    document = {
        "verdict": "PASS" if passed else "FAIL",
        "pending": pending,
        "cells": cells,
    }
    MATRIX_FILE.write_text(json.dumps(document, indent=2) + "\n")
    json.dump(document, sys.stdout, indent=2)
    print()
    print(f"\nR3: {document['verdict']}", file=sys.stderr)
    if pending:
        print(f"R3 pending (not measured): {', '.join(pending)}", file=sys.stderr)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
