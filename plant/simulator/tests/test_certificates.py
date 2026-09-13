import ipaddress
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import ExtendedKeyUsageOID
from simulator.pki import PARTIES, gen_party

SERVER_URI = "urn:machine-agent:plant:line-simulator"


@pytest.fixture
def pki(tmp_path: Path) -> Path:
    for party in PARTIES.values():
        gen_party(party, tmp_path)
    return tmp_path


def _cert(root: Path, name: str) -> x509.Certificate:
    return x509.load_der_x509_certificate(
        (root / name / "certs" / f"{name}.der").read_bytes()
    )


def test_server_uri_san_equals_application_uri(pki: Path) -> None:
    """asyncua raises BadCertificateUriInvalid unless ApplicationUri is a URI SAN."""
    san = (
        _cert(pki, "line-simulator")
        .extensions.get_extension_for_class(x509.SubjectAlternativeName)
        .value
    )
    assert san.get_values_for_type(x509.UniformResourceIdentifier) == [SERVER_URI]


def test_server_dns_sans_cover_every_name_a_client_may_dial(pki: Path) -> None:
    """The DNS SAN must cover the host component of the endpoint URL, or .NET's
    checkDomain fails as BadCertificateHostNameInvalid. Three clients, three names."""
    san = (
        _cert(pki, "line-simulator")
        .extensions.get_extension_for_class(x509.SubjectAlternativeName)
        .value
    )
    assert set(san.get_values_for_type(x509.DNSName)) == {"line-simulator", "localhost"}
    assert ipaddress.ip_address("127.0.0.1") in san.get_values_for_type(x509.IPAddress)


def test_roles_are_distinct(pki: Path) -> None:
    """asyncua checks EXT_KEY_USAGE against the peer's expected role."""
    server = (
        _cert(pki, "line-simulator")
        .extensions.get_extension_for_class(x509.ExtendedKeyUsage)
        .value
    )
    client = (
        _cert(pki, "edge-gateway")
        .extensions.get_extension_for_class(x509.ExtendedKeyUsage)
        .value
    )
    assert ExtendedKeyUsageOID.SERVER_AUTH in server
    assert ExtendedKeyUsageOID.CLIENT_AUTH in client


def test_key_usage_satisfies_asyncua_validator(pki: Path) -> None:
    """CertificateValidatorOptions.KEY_USAGE requires all four of these."""
    ku = (
        _cert(pki, "line-simulator")
        .extensions.get_extension_for_class(x509.KeyUsage)
        .value
    )
    assert ku.digital_signature and ku.content_commitment
    assert ku.key_encipherment and ku.data_encipherment


def test_subject_matches_what_task_7_configures(pki: Path) -> None:
    """UA-.NETStandard resolves its own-store certificate via Utils.CompareDistinguishedName,
    whose first test is field count. Task 7 configures each party's subjectName as exactly
    "CN=<name>, O=machine-agent, C=DE" (three RDNs) — a fourth (e.g. a second CN carrying
    the ApplicationUri) fails that comparison before the handshake starts."""
    for name in ("line-simulator", "edge-gateway"):
        subject = _cert(pki, name).subject
        rdns = [attr for rdn in subject.rdns for attr in rdn]
        assert [attr.rfc4514_attribute_name for attr in rdns] == ["CN", "O", "C"]
        assert [attr.value for attr in rdns] == [name, "machine-agent", "DE"]


def test_both_parties_land_in_the_shared_trust_dir(pki: Path) -> None:
    assert (pki / "trusted" / "certs" / "line-simulator.der").exists()
    assert (pki / "trusted" / "certs" / "edge-gateway.der").exists()


def test_gateway_gets_a_pfx_for_the_dotnet_store(pki: Path) -> None:
    """A pfx carrying the certificate but no private key is useless to the Directory
    store, which reads it as the gateway's own identity, key included."""
    pfx_file = pki / "edge-gateway" / "private" / "edge-gateway.pfx"
    assert pfx_file.stat().st_size > 0
    key, cert, _ = pkcs12.load_key_and_certificates(
        pfx_file.read_bytes(), password=None
    )
    assert key is not None
    assert cert is not None


def test_regeneration_is_idempotent(pki: Path) -> None:
    """docker compose up runs pki-init every time; it must not churn the identity, or
    §14's 'survives a restart without re-trusting by hand' fails — for the key backing
    the certificate as much as for the certificate itself, and for the copy every peer's
    trust store was already seeded with."""
    cert_file = pki / "line-simulator" / "certs" / "line-simulator.der"
    key_file = pki / "line-simulator" / "private" / "line-simulator.pem"
    trusted_file = pki / "trusted" / "certs" / "line-simulator.der"
    cert_before = cert_file.read_bytes()
    key_before = key_file.read_bytes()
    trusted_before = trusted_file.read_bytes()

    gen_party(PARTIES["line-simulator"], pki)

    assert cert_file.read_bytes() == cert_before
    assert key_file.read_bytes() == key_before
    assert trusted_file.read_bytes() == trusted_before


def test_a_deleted_pfx_is_reminted_without_touching_the_identity(pki: Path) -> None:
    """A freshness check keyed on key.pem and cert.der alone treats a missing pfx as
    nothing to do, prints "ready", and leaves Task 6's expected file absent. Deleting the
    pfx and regenerating must restore it from the existing keypair, not the freshness
    check's blind spot, and must not rotate the identity that peer trust stores hold."""
    cert_file = pki / "edge-gateway" / "certs" / "edge-gateway.der"
    key_file = pki / "edge-gateway" / "private" / "edge-gateway.pem"
    pfx_file = pki / "edge-gateway" / "private" / "edge-gateway.pfx"
    cert_before = cert_file.read_bytes()
    key_before = key_file.read_bytes()

    pfx_file.unlink()
    gen_party(PARTIES["edge-gateway"], pki)

    assert pfx_file.stat().st_size > 0
    assert cert_file.read_bytes() == cert_before
    assert key_file.read_bytes() == key_before
    key, _, _ = pkcs12.load_key_and_certificates(pfx_file.read_bytes(), password=None)
    assert key is not None
