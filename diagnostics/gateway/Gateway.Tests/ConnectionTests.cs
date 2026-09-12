using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using Gateway.Opc;

namespace Gateway.Tests;

public sealed class ConnectionTests : IDisposable
{
    private const string ApplicationUri = "urn:machine-agent:diagnostics:edge-gateway";

    private readonly string _pkiRoot =
        Path.Join(Path.GetTempPath(), "gateway-tests-" + Path.GetRandomFileName());

    public void Dispose()
    {
        if (Directory.Exists(_pkiRoot))
        {
            Directory.Delete(_pkiRoot, recursive: true);
        }
    }

    /// <summary>
    /// Generates the one file the inspector reads, in the layout pki-init produces.
    /// Hermetic on purpose: pki/ is generated and gitignored, so a test that read it
    /// would pass here and fail in CI.
    /// </summary>
    private string WriteGatewayCertificate(string uriSan)
    {
        var certificates = Path.Join(_pkiRoot, "edge-gateway", "certs");
        Directory.CreateDirectory(certificates);

        using var key = RSA.Create(2048);
        var request = new CertificateRequest(
            "CN=edge-gateway, O=machine-agent, C=DE", key,
            HashAlgorithmName.SHA256, RSASignaturePadding.Pkcs1);

        var subjectAlternativeNames = new SubjectAlternativeNameBuilder();
        subjectAlternativeNames.AddUri(new Uri(uriSan));
        request.CertificateExtensions.Add(subjectAlternativeNames.Build());

        using var certificate = request.CreateSelfSigned(
            DateTimeOffset.UtcNow.AddDays(-1), DateTimeOffset.UtcNow.AddDays(365));

        var path = Path.Join(certificates, "edge-gateway.der");
        File.WriteAllBytes(path, certificate.Export(X509ContentType.Cert));
        return path;
    }

    [Fact]
    public void ApplicationUriMustMatchTheCertificateUriSan()
    {
        // The OPC UA stack rejects a session as BadCertificateUriInvalid unless the
        // configured ApplicationUri and the certificate's URI SAN are byte-identical.
        WriteGatewayCertificate(ApplicationUri);

        var options = GatewayOptions.FromEnvironment(new Dictionary<string, string?>
        {
            ["GATEWAY_APPLICATION_URI"] = ApplicationUri,
            ["GATEWAY_PKI_ROOT"] = _pkiRoot,
            ["GATEWAY_ENDPOINT_URL"] = "opc.tcp://line-simulator:4840/plant",
        });

        Assert.Equal(options.ApplicationUri, CertificateInspector.UriSan(options.OwnCertificatePath));
    }

    [Fact]
    public void AMismatchedUriSanIsDetectedRatherThanIgnored()
    {
        WriteGatewayCertificate("urn:machine-agent:diagnostics:something-else");

        var options = GatewayOptions.FromEnvironment(new Dictionary<string, string?>
        {
            ["GATEWAY_APPLICATION_URI"] = ApplicationUri,
            ["GATEWAY_PKI_ROOT"] = _pkiRoot,
            ["GATEWAY_ENDPOINT_URL"] = "opc.tcp://line-simulator:4840/plant",
        });

        Assert.NotEqual(options.ApplicationUri, CertificateInspector.UriSan(options.OwnCertificatePath));
    }

    [Fact]
    public void SecurityModeIsSignAndNeverNone()
    {
        Assert.Equal("Sign", GatewayOptions.Default().SecurityMode);
    }

    [Fact]
    public void StoreRootsFollowThePkiInitLayout()
    {
        var options = GatewayOptions.FromEnvironment(new Dictionary<string, string?>
        {
            ["GATEWAY_PKI_ROOT"] = _pkiRoot,
        });

        Assert.Equal(Path.Join(_pkiRoot, "edge-gateway"), options.OwnStoreRoot);
        Assert.Equal(Path.Join(_pkiRoot, "trusted"), options.TrustedStoreRoot);
        Assert.Equal(Path.Join(_pkiRoot, "rejected"), options.RejectedStoreRoot);
    }
}
