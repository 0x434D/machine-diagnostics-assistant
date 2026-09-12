import ipaddress
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.x509.oid import ExtendedKeyUsageOID
from simulator.pki import PARTIES, gen_party

SERVER_URI = "urn:machine-agent:plant:line-simulator"
CLIENT_URI = "urn:machine-agent:diagnostics:edge-gateway"


@pytest.fixture
def pki(tmp_path: Path) -> Path:
    for party in PARTIES.values():
        gen_party(party, tmp_path)
    return tmp_path


def _cert(root: Path, name: str) -> x509.Certificate:
    return x509.load_der_x509_certificate((root / name / "cert.der").read_bytes())


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


def test_both_parties_land_in_the_shared_trust_dir(pki: Path) -> None:
    assert (pki / "trusted" / "line-simulator.der").exists()
    assert (pki / "trusted" / "edge-gateway.der").exists()


def test_gateway_gets_a_pfx_for_the_dotnet_store(pki: Path) -> None:
    assert (pki / "edge-gateway" / "cert.pfx").stat().st_size > 0


def test_regeneration_is_idempotent(pki: Path) -> None:
    """docker compose up runs pki-init every time; it must not churn the identity,
    or §14's 'survives a restart without re-trusting by hand' fails."""
    before = (pki / "line-simulator" / "cert.der").read_bytes()
    gen_party(PARTIES["line-simulator"], pki)
    assert (pki / "line-simulator" / "cert.der").read_bytes() == before
