"""Application instance certificates for the OPC UA boundary (§4.6).

asyncua ships setup_self_signed_certificate(), but it emits exactly one DNS SAN.
The boundary needs three names on one certificate (see the plan's "one-name
boundary"), so this calls the lower-level generator with a SAN list.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from pathlib import Path

from asyncua.crypto.cert_gen import (
    dump_private_key_as_pem,
    generate_private_key,
    generate_self_signed_app_certificate,
)
from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, pkcs12
from cryptography.x509.oid import ExtendedKeyUsageOID

VALID_DAYS = 825  # under the 825-day maximum most validators accept


@dataclass(frozen=True)
class Party:
    """One identity on the OPC UA boundary: an application instance certificate to mint."""

    name: str
    app_uri: str
    dns_names: tuple[str, ...]
    ip_addresses: tuple[str, ...] = ()
    server_auth: bool = False
    client_auth: bool = False
    want_pfx: bool = False
    subject: dict[str, str] = field(default_factory=dict)


PARTIES: dict[str, Party] = {
    "line-simulator": Party(
        name="line-simulator",
        app_uri="urn:machine-agent:plant:line-simulator",
        # Every host component any client may dial, on one certificate:
        #   gateway on field-net -> line-simulator
        #   host via published port -> localhost / 127.0.0.1
        dns_names=("line-simulator", "localhost"),
        ip_addresses=("127.0.0.1",),
        server_auth=True,
        subject={
            "commonName": "line-simulator",
            "organizationName": "machine-agent",
            "countryName": "DE",
        },
    ),
    "edge-gateway": Party(
        name="edge-gateway",
        app_uri="urn:machine-agent:diagnostics:edge-gateway",
        dns_names=("edge-gateway",),
        client_auth=True,
        want_pfx=True,
        subject={
            "commonName": "edge-gateway",
            "organizationName": "machine-agent",
            "countryName": "DE",
        },
    ),
}


def gen_party(party: Party, out_root: Path) -> None:
    """Generate this party's keypair if absent and publish its public cert to trusted/.

    Assumes `out_root` is writable and its parent exists; creates `out_root/<party.name>`
    and `out_root/trusted` if missing.

    Idempotent: an existing key and certificate are left untouched, so re-running
    pki-init on every `docker compose up` does not invalidate established trust.
    """
    out = out_root / party.name
    out.mkdir(parents=True, exist_ok=True)
    trusted = out_root / "trusted"
    trusted.mkdir(parents=True, exist_ok=True)

    key_file, cert_file = out / "key.pem", out / "cert.der"

    if key_file.exists() and cert_file.exists():
        cert = x509.load_der_x509_certificate(cert_file.read_bytes())
    else:
        key = generate_private_key()
        sans: list[x509.GeneralName] = [x509.UniformResourceIdentifier(party.app_uri)]
        sans += [x509.DNSName(n) for n in party.dns_names]
        sans += [x509.IPAddress(ipaddress.ip_address(a)) for a in party.ip_addresses]

        usages: list[x509.ObjectIdentifier] = []
        if party.server_auth:
            usages.append(ExtendedKeyUsageOID.SERVER_AUTH)
        if party.client_auth:
            usages.append(ExtendedKeyUsageOID.CLIENT_AUTH)

        cert = generate_self_signed_app_certificate(
            key, party.app_uri, party.subject, sans, extended=usages, days=VALID_DAYS
        )
        key_file.write_bytes(dump_private_key_as_pem(key))
        key_file.chmod(0o600)
        cert_file.write_bytes(cert.public_bytes(encoding=Encoding.DER))

        if party.want_pfx:
            # UA-.NETStandard's Directory store reads own/private/*.pfx. Minting the
            # certificate here rather than letting CheckApplicationInstanceCertificatesAsync
            # do it keeps the DNS SAN stable: .NET would derive it from the container
            # hostname, which changes on every recreate and breaks pre-seeded trust.
            (out / "cert.pfx").write_bytes(
                pkcs12.serialize_key_and_certificates(
                    name=party.name.encode(),
                    key=key,
                    cert=cert,
                    cas=None,
                    encryption_algorithm=NoEncryption(),
                )
            )

    (trusted / f"{party.name}.der").write_bytes(
        cert.public_bytes(encoding=Encoding.DER)
    )


def main() -> None:
    """Generate (or leave untouched) both parties' certificate material under sys.argv[1].

    Assumes it is run once per `pki-init` container start; writes under the given root
    (defaulting to `/pki`, the container mount point) and never removes existing material.
    """
    import sys

    root = Path(sys.argv[1] if len(sys.argv) > 1 else "/pki")
    for party in PARTIES.values():
        gen_party(party, root)
        print(f"pki: {party.name} ready ({', '.join(party.dns_names)})")


if __name__ == "__main__":
    main()
