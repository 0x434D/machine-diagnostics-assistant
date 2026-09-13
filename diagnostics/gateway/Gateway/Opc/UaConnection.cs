using Microsoft.Extensions.Logging;
using Opc.Ua;
using Opc.Ua.Client;
using Opc.Ua.Configuration;

namespace Gateway.Opc;

public sealed partial class UaConnection : IAsyncDisposable
{
    // Exactly three RDNs. Utils.CompareDistinguishedName compares field count first, so a
    // fourth RDN fails before any value is looked at and presents as "no usable certificate".
    private const string GatewaySubjectName = "CN=edge-gateway, O=machine-agent, C=DE";

    private readonly GatewayOptions _options;
    private readonly ITelemetryContext _telemetry;
    private readonly ILogger<UaConnection> _logger;
    private ApplicationConfiguration? _configuration;
    private SessionReconnectHandler? _reconnectHandler;

    public ISession? Session { get; private set; }
    public event Action<string>? StateChanged;

    /// <summary>
    /// Raised when a dropped session has been re-established. §4.3's backfill closes the gap
    /// it has "after first boot, after a crash, after an outage — one mechanism, three
    /// situations", and this is how the second and third situations reach it.
    /// </summary>
    public event Action? Reconnected;

    public UaConnection(GatewayOptions options, ITelemetryContext telemetry)
    {
        _options = options;
        _telemetry = telemetry;
        _logger = telemetry.LoggerFactory.CreateLogger<UaConnection>();
    }

    public async Task<ApplicationConfiguration> BuildConfigurationAsync()
    {
        var application = new ApplicationInstance(_telemetry)
        {
            ApplicationName = "machine-agent edge gateway",
            ApplicationType = ApplicationType.Client,
        };

        // Built in code, not loaded from XML, so the store paths come from pki/.
        _configuration = await application
            .Build(_options.ApplicationUri, "urn:machine-agent:diagnostics:edge-gateway:product")
            .SetTransportQuotas(new TransportQuotas
            {
                OperationTimeout = 120_000,
                MaxByteStringLength = _options.MaxByteStringLength,
                MaxMessageSize = _options.MaxMessageSize,
                MaxBufferSize = 65_535,
            })
            .AsClient()
            .SetDefaultSessionTimeout(60_000)
            // AddSecurityConfigurationStores(appRoot:) would look under <appRoot>/own/certs,
            // which is UA-.NETStandard's own convention. pki-init writes certs/ and private/
            // directly under pki/edge-gateway, so the store is named explicitly instead of
            // asking the plant to reshape material the simulator also reads.
            .AddSecurityConfiguration(
                new CertificateIdentifierCollection
                {
                    new CertificateIdentifier
                    {
                        StoreType = CertificateStoreType.Directory,
                        StorePath = _options.OwnStoreRoot,
                        SubjectName = GatewaySubjectName,
                        // Required: the ApplicationCertificates setter validates the type and
                        // throws a NullReferenceException when it is left unset.
                        CertificateType = ObjectTypeIds.RsaSha256ApplicationCertificateType,
                    },
                },
                _options.PkiRoot,
                _options.RejectedStoreRoot)
            // The certificate is minted by pki-init, never here: letting .NET mint it would
            // derive the DNS SAN from the container hostname, which changes on every recreate
            // and breaks the pre-seeded trust §14 requires.
            .SetAutoAcceptUntrustedCertificates(false)
            .SetAddAppCertToTrustedStore(false)
            .SetRejectSHA1SignedCertificates(true)
            .SetRejectUnknownRevocationStatus(false)   // self-signed, no CRL distribution
            .CreateAsync()
            .ConfigureAwait(false);

        // Checked before the SDK looks, because when the SDK does not find a certificate it
        // mints one — and minting derives the DNS SAN from the container hostname, which
        // changes on every recreate and breaks the pre-seeded trust §14 requires. On a
        // read-only pki mount it fails as an IOException instead, which reads as a
        // permissions problem rather than as "pki-init has not run".
        if (!File.Exists(_options.OwnCertificatePath))
        {
            throw new InvalidOperationException(
                $"no application instance certificate at {_options.OwnCertificatePath}; did pki-init run?");
        }

        var ok = await application
            .CheckApplicationInstanceCertificatesAsync(silent: true)
            .ConfigureAwait(false);
        if (!ok)
        {
            throw new InvalidOperationException(
                $"no usable application instance certificate in {_options.OwnStoreRoot}; did pki-init run?");
        }

        return _configuration;
    }

    public async Task<ISession> ConnectAsync(CancellationToken ct)
    {
        var config = _configuration ?? await BuildConfigurationAsync().ConfigureAwait(false);
        StateChanged?.Invoke("connecting");

        var useSecurity = _options.SecurityMode != "None";
        var endpointDescription = await CoreClientUtils
            .SelectEndpointAsync(config, _options.EndpointUrl, useSecurity, _telemetry, ct)
            .ConfigureAwait(false)
            ?? throw new ServiceResultException(
                StatusCodes.BadNotConnected, $"no endpoint at {_options.EndpointUrl}");

        // SelectEndpointAsync returns the best endpoint it can, not the one that was asked
        // for: with useSecurity false against a server that offers Sign only, it hands back
        // the Sign endpoint and the session succeeds. For the R3 matrix that would report
        // "NoSecurity connects" while the connection was in fact signed — the one result in
        // the matrix that would be actively misleading. So the negotiated mode is checked
        // against the requested one rather than assumed.
        var requested = useSecurity ? MessageSecurityMode.Sign : MessageSecurityMode.None;
        if (endpointDescription.SecurityMode != requested)
        {
            throw new ServiceResultException(
                StatusCodes.BadSecurityModeRejected,
                $"requested SecurityMode {requested}, but {_options.EndpointUrl} offers only "
                + $"{endpointDescription.SecurityMode}");
        }

        var endpoint = new ConfiguredEndpoint(
            null, endpointDescription, EndpointConfiguration.Create(config));

        var factory = new DefaultSessionFactory(_telemetry);
        var session = await factory.CreateAsync(
            config,
            endpoint,
            updateBeforeConnect: false,
            // checkDomain compares the endpoint URL's host against the server certificate's
            // DNS SANs. Kept on deliberately: switching it off would hide exactly the SAN
            // mismatch §12 warns about.
            checkDomain: true,
            sessionName: "machine-agent-gateway",
            sessionTimeout: 60_000,
            identity: new UserIdentity(),   // anonymous; §10.5 defers user auth
            preferredLocales: null,
            ct).ConfigureAwait(false);

        session.KeepAliveInterval = 5_000;
        session.DeleteSubscriptionsOnClose = false;
        // False, deliberately. Transferring looks like the helpful setting and is the wrong
        // one here: a plant that restarted republishes its whole catch-up, and a transferred
        // subscription delivers it through the live path at 700x — measured at 84,000 rows
        // arriving while Clock.Phase still read "catchup". §4.3 orders backfill before
        // subscribe for exactly that reason, and the ordering has to hold on reconnect too,
        // not only at boot. The transfer also fails here anyway, as BadInvalidState
        // "subscriptionId N is already created", after which the SDK recreates a subscription
        // this process no longer has a handle to — so it cannot be torn down either.
        session.TransferSubscriptionsOnReconnect = false;
        session.KeepAlive += OnKeepAlive;

        _reconnectHandler = new SessionReconnectHandler(_telemetry, true, 30_000);
        Session = session;
        StateChanged?.Invoke("live");
        LogSessionEstablished(session.SessionName);
        return session;
    }

    [LoggerMessage(Level = LogLevel.Information, Message = "session established {SessionName}")]
    private partial void LogSessionEstablished(string sessionName);

    private void OnKeepAlive(ISession session, KeepAliveEventArgs e)
    {
        if (Session is null || !Session.Equals(session) || !ServiceResult.IsBad(e.Status))
        {
            return;
        }

        StateChanged?.Invoke("disconnected");
        var state = _reconnectHandler!.BeginReconnect(session, 2_000, OnReconnectComplete);
        if (state == SessionReconnectHandler.ReconnectState.Triggered)
        {
            e.CancelKeepAlive = true;
        }
    }

    private void OnReconnectComplete(object? sender, EventArgs e)
    {
        if (!ReferenceEquals(sender, _reconnectHandler) || _reconnectHandler!.Session is null)
        {
            return;
        }

        if (!ReferenceEquals(Session, _reconnectHandler.Session))
        {
            var old = Session;
            Session = _reconnectHandler.Session;
            Utils.SilentDispose(old);
        }

        // A new or reactivated session means the plant may have been away. Backfill closes
        // whatever gap exists — one mechanism, three situations (§4.3). The SDK transfers the
        // subscription, so live data resumes by itself; what the plant produced while it was
        // away exists only in its history, and nothing else would go and get it.
        StateChanged?.Invoke("backfilling");
        Reconnected?.Invoke();
    }

    /// <summary>
    /// §4.3: the phase says whether the plant has finished building its history. Backfilling
    /// mid-catch-up would read a history still being written, and subscribing mid-catch-up
    /// delivers it through the live path — measured at ~275 records/second.
    ///
    /// Only two values exist, catchup and live. §4.1 names a third, booting, but the plant is
    /// already in catchup at the boot instant, so there is nothing to wait for and no handler
    /// is written for a phase that cannot occur.
    /// </summary>
    public async Task<string> ReadPhaseAsync(NodeId phaseNode, CancellationToken ct)
    {
        var value = await Session!.ReadValueAsync(phaseNode, ct).ConfigureAwait(false);
        return value.Value as string ?? "unknown";
    }

    public async ValueTask DisposeAsync()
    {
        if (Session is not null)
        {
            Session.KeepAlive -= OnKeepAlive;
            await Session.CloseAsync().ConfigureAwait(false);
            Session.Dispose();
        }

        _reconnectHandler?.Dispose();
    }
}
