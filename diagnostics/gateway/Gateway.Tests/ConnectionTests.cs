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

    [Theory]
    [InlineData("GATEWAY_BACKFILL_WINDOW_MINUTES", "0")]
    [InlineData("GATEWAY_BACKFILL_WINDOW_MINUTES", "-1")]
    [InlineData("GATEWAY_BACKFILL_WINDOW_MINUTES", "sixty")]
    [InlineData("GATEWAY_MINIMUM_BACKFILL_WINDOW_SECONDS", "0")]
    [InlineData("GATEWAY_MINIMUM_BACKFILL_WINDOW_SECONDS", "-30")]
    public void AWindowLengthThatCannotMakeProgressIsRefusedAtBoot(string key, string value)
    {
        // A backfill window of zero is the one setting here that hangs rather than being
        // merely wrong: RunAsync steps its windows by BackfillWindow, so a zero-length one
        // never advances and the backfill spins for ever with nothing logged. Refused where a
        // missing signal policy is refused -- at boot, naming the value.
        var exception = Assert.Throws<ArgumentException>(() =>
            GatewayOptions.FromEnvironment(new Dictionary<string, string?>(StringComparer.Ordinal)
            {
                [key] = value,
            }));

        Assert.Contains(key, exception.Message, StringComparison.Ordinal);
        Assert.Contains(value, exception.Message, StringComparison.Ordinal);
    }

    [Fact]
    public void AWindowLengthThatIsNotSetKeepsItsDefault()
    {
        var options = GatewayOptions.Default();

        Assert.Equal(TimeSpan.FromHours(1), options.BackfillWindow);
        Assert.Equal(TimeSpan.FromSeconds(30), options.MinimumBackfillWindow);
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

        // Outside the PKI root on purpose: pki/ is mounted read-only and the stack writes
        // refused certificates to this store.
        Assert.Equal(GatewayOptions.DefaultRejectedStoreRoot, options.RejectedStoreRoot);
        Assert.DoesNotContain(_pkiRoot, options.RejectedStoreRoot, StringComparison.Ordinal);
    }
}
