"""Application instance certificates for the OPC UA boundary (§4.6).

asyncua ships setup_self_signed_certificate(), but it emits exactly one DNS SAN.
The boundary needs three names on one certificate (see the plan's "one-name
boundary"), so this calls the lower-level generator with a SAN list.

Layout under a root (e.g. `pki/`) follows UA-.NETStandard's DirectoryCertificateStore,
which is what the gateway's own and trusted stores expect and cannot be reconfigured to
read anything else: `<party>/certs/<party>.der`, `<party>/private/<party>.pem` (and
`.pfx` for a party that wants one), `trusted/certs/<party>.der`. asyncua is pointed at
these exact paths explicitly (Task 6), so it has no layout preference of its own.
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
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    load_pem_private_key,
    pkcs12,
)
from cryptography.x509.oid import ExtendedKeyUsageOID

VALID_DAYS = 825  # ~2.25 years: outlives this project's dev/demo lifetime, nothing more


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
        # No "commonName" here: the CN is party.name, supplied separately below. Task 7
        # configures UA-.NETStandard's own-certificate subjectName as this exact 3-RDN
        # string; a fourth RDN fails Utils.CompareDistinguishedName's field-count check
        # before the handshake starts.
        subject={"organizationName": "machine-agent", "countryName": "DE"},
    ),
    "edge-gateway": Party(
        name="edge-gateway",
        app_uri="urn:machine-agent:diagnostics:edge-gateway",
        dns_names=("edge-gateway",),
        client_auth=True,
        want_pfx=True,
        subject={"organizationName": "machine-agent", "countryName": "DE"},
    ),
}


def _write_pfx(
    pfx_file: Path, name: str, key: rsa.RSAPrivateKey, cert: x509.Certificate
) -> None:
    """Write a PKCS#12 bundle for UA-.NETStandard's Directory store and lock it down.

    No PKCS#12 password: the store reads a password-less pfx unless a
    CertificatePasswordProvider is configured, which Task 6 does not add.
    """
    pfx_file.write_bytes(
        pkcs12.serialize_key_and_certificates(
            name=name.encode(),
            key=key,
            cert=cert,
            cas=None,
            encryption_algorithm=NoEncryption(),
        )
    )
    pfx_file.chmod(0o600)  # contains the private key, same as key.pem


def _load_rsa_private_key(key_file: Path) -> rsa.RSAPrivateKey:
    """Load the RSA private key this module itself wrote to `key_file`.

    Raises TypeError if the file does not hold an RSA key — it always should, since
    gen_party only ever writes what generate_private_key() returns.
    """
    key = load_pem_private_key(key_file.read_bytes(), password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise TypeError(f"{key_file} does not hold an RSA private key")
    return key


def gen_party(party: Party, out_root: Path) -> None:
    """Generate this party's keypair and certificate if absent; publish the public cert to
    trusted/certs/.

    Assumes the process can create files under out_root; a permission error propagates
    uncaught.

    Idempotent: an established key and certificate are left untouched, so re-running
    pki-init on every `docker compose up` does not invalidate established trust. A pfx
    missing from an otherwise-established identity is re-minted from the existing keypair
    rather than treated as a reason to regenerate the identity — or, left unnoticed, as a
    reason to report the party "ready" while the file Task 6 expects does not exist.
    """
    certs_dir = out_root / party.name / "certs"
    private_dir = out_root / party.name / "private"
    trusted_certs = out_root / "trusted" / "certs"
    certs_dir.mkdir(parents=True, exist_ok=True)
    private_dir.mkdir(parents=True, exist_ok=True)
    trusted_certs.mkdir(parents=True, exist_ok=True)

    key_file = private_dir / f"{party.name}.pem"
    cert_file = certs_dir / f"{party.name}.der"
    pfx_file = private_dir / f"{party.name}.pfx"

    if key_file.exists() and cert_file.exists():
        cert = x509.load_der_x509_certificate(cert_file.read_bytes())
        if party.want_pfx and not pfx_file.exists():
            _write_pfx(pfx_file, party.name, _load_rsa_private_key(key_file), cert)
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

        # generate_self_signed_app_certificate unconditionally marks BasicConstraints
        # (ca=True, path_length=0) and KeyUsage.key_cert_sign on the leaf — it only gates
        # crl_sign and ExtendedKeyUsage on whether `extended` is empty, not these. asyncua's
        # own validator never inspects BasicConstraints, and a self-signed peer sits in a
        # .NET trust store by explicit trust on a one-element chain, where pathlen:0
        # violates nothing. Not fixed here: removing it means hand-building a
        # CertificateBuilder instead of this helper. Task 6's first successful handshake is
        # the proof.
        cert = generate_self_signed_app_certificate(
            key, party.name, party.subject, sans, extended=usages, days=VALID_DAYS
        )
        key_file.write_bytes(dump_private_key_as_pem(key))
        key_file.chmod(0o600)
        cert_file.write_bytes(cert.public_bytes(encoding=Encoding.DER))

        if party.want_pfx:
            _write_pfx(pfx_file, party.name, key, cert)

    (trusted_certs / f"{party.name}.der").write_bytes(
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
