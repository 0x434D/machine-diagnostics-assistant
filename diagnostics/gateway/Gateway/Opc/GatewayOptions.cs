namespace Gateway.Opc;

/// <summary>
/// Every gateway setting, resolved from environment variables with defaults.
/// Spec §10.3: every number is configuration.
/// </summary>
public sealed record GatewayOptions
{
    public const string DefaultApplicationUri = "urn:machine-agent:diagnostics:edge-gateway";
    public const string DefaultEndpointUrl = "opc.tcp://line-simulator:4840/plant";
    public const string DefaultPkiRoot = "/pki";

    /// <summary>
    /// Deliberately outside the PKI root. pki/ is bind-mounted read-only — it holds the only
    /// key material in the system — and the stack writes refused certificates here, so a
    /// rejected store inside it turns any refusal into an IOException.
    /// </summary>
    public const string DefaultRejectedStoreRoot = "/rejected";

    /// <summary>Sign, never None. §4.6 — the boundary is authenticated, not merely narrow.</summary>
    public const string DefaultSecurityMode = "Sign";

    // R4 measured img_p99 at 110,419 B for a reject image (measurements/r4-image-sizes.txt).
    // 4 MiB leaves ~38x headroom; the 64 KiB buffer below is deliberately smaller than a
    // single image, so chunking is exercised on every reject rather than provisioned for.
    public const int DefaultMaxByteStringLength = 4 * 1024 * 1024;
    public const int DefaultMaxMessageSize = 4 * 1024 * 1024;

    /// <summary>Bounds the one-shot connect probe so the R3 matrix cannot hang on a dead name.</summary>
    public const int DefaultConnectTimeoutSeconds = 30;

    /// <summary>On a volume, so the queue outlives the container it buffers for (§5.1).</summary>
    public const string DefaultQueuePath = "/queue/gateway.db";

    public required string ApplicationUri { get; init; }
    public required string EndpointUrl { get; init; }
    public required string SecurityMode { get; init; }
    public required string PkiRoot { get; init; }
    public int MaxByteStringLength { get; init; } = DefaultMaxByteStringLength;
    public int MaxMessageSize { get; init; } = DefaultMaxMessageSize;
    public int ConnectTimeoutSeconds { get; init; } = DefaultConnectTimeoutSeconds;
    public string QueuePath { get; init; } = DefaultQueuePath;

    /// <summary>Empty until Postgres exists for this deployment; the queue then simply fills.</summary>
    public string PostgresConnectionString { get; init; } = "";
    public int DrainBatchSize { get; init; } = 200;
    public int DrainIdleMs { get; init; } = 250;
    public int DrainRetryMs { get; init; } = 2_000;

    /// <summary>
    /// One backfill window. At a 6 s takt this is 600 values per signal — sixteen times under
    /// the 10,000 ceiling F1 measured, so a window can never silently truncate.
    /// </summary>
    public TimeSpan BackfillWindow { get; init; } = TimeSpan.FromHours(1);
    public int HistoryPageSize { get; init; } = 1_000;

    /// <summary>
    /// The event stream needs its own, far smaller page because its rows carry images.
    /// R4 measured a reject image at up to 110,486 B, so a worst-case page of 25 all-reject
    /// events is ~2.7 MB against the 4 MiB response limit. At the plan's shared page size of
    /// 1,000 the response exceeds that limit and the read fails as BadEncodingLimitsExceeded
    /// — measured against the live plant, not predicted.
    /// </summary>
    public int HistoryEventPageSize { get; init; } = 25;

    /// <summary>
    /// The floor for subdividing an event window. At a 6 s takt a 30 s window holds ~5 parts,
    /// well under any page size, so reaching this floor means something other than volume is
    /// wrong and the run should fail rather than lose rows quietly.
    /// </summary>
    public TimeSpan MinimumBackfillWindow { get; init; } = TimeSpan.FromSeconds(30);

    /// <summary>How far back to reach on a first boot, when storage holds nothing.</summary>
    public TimeSpan HistoryDepth { get; init; } = TimeSpan.FromHours(18);
    public int PublishingIntervalMs { get; init; } = 250;
    public int SamplingIntervalMs { get; init; } = 250;
    public uint QueueSize { get; init; } = 100;
    public uint EventQueueSize { get; init; } = 200;

    /// <summary>Absolute deadband on TaktTime, in seconds.</summary>
    public double TaktDeadband { get; init; } = 0.05;

    public string OwnStoreRoot => Path.Join(PkiRoot, "edge-gateway");
    public string TrustedStoreRoot => Path.Join(PkiRoot, "trusted");
    public string RejectedStoreRoot { get; init; } = DefaultRejectedStoreRoot;
    public string OwnCertificatePath => Path.Join(OwnStoreRoot, "certs", "edge-gateway.der");

    public static GatewayOptions Default() =>
        FromEnvironment(new Dictionary<string, string?>(StringComparer.Ordinal));

    public static GatewayOptions FromProcessEnvironment()
    {
        var environment = new Dictionary<string, string?>(StringComparer.Ordinal);
        foreach (System.Collections.DictionaryEntry entry in Environment.GetEnvironmentVariables())
        {
            environment[(string)entry.Key] = entry.Value as string;
        }

        return FromEnvironment(environment);
    }

    /// <exception cref="ArgumentException">GATEWAY_SECURITY_MODE is neither Sign nor None.</exception>
    public static GatewayOptions FromEnvironment(IReadOnlyDictionary<string, string?> environment)
    {
        ArgumentNullException.ThrowIfNull(environment);

        var securityMode = Read(environment, "GATEWAY_SECURITY_MODE", DefaultSecurityMode);
        if (securityMode is not ("Sign" or "None"))
        {
            throw new ArgumentException(
                $"GATEWAY_SECURITY_MODE must be Sign or None, not '{securityMode}'",
                nameof(environment));
        }

        return new GatewayOptions
        {
            ApplicationUri = Read(environment, "GATEWAY_APPLICATION_URI", DefaultApplicationUri),
            EndpointUrl = Read(environment, "GATEWAY_ENDPOINT_URL", DefaultEndpointUrl),
            SecurityMode = securityMode,
            PkiRoot = Read(environment, "GATEWAY_PKI_ROOT", DefaultPkiRoot),
            RejectedStoreRoot = Read(
                environment, "GATEWAY_REJECTED_STORE_ROOT", DefaultRejectedStoreRoot),
            QueuePath = Read(environment, "GATEWAY_QUEUE_PATH", DefaultQueuePath),
            PostgresConnectionString = Read(environment, "GATEWAY_POSTGRES", ""),
            DrainBatchSize = ReadInt(environment, "GATEWAY_DRAIN_BATCH_SIZE", 200),
            HistoryEventPageSize = ReadInt(environment, "GATEWAY_HISTORY_EVENT_PAGE_SIZE", 25),
            MaxByteStringLength = ReadInt(
                environment, "GATEWAY_MAX_BYTE_STRING_LENGTH", DefaultMaxByteStringLength),
            MaxMessageSize = ReadInt(
                environment, "GATEWAY_MAX_MESSAGE_SIZE", DefaultMaxMessageSize),
            ConnectTimeoutSeconds = ReadInt(
                environment, "GATEWAY_CONNECT_TIMEOUT_SECONDS", DefaultConnectTimeoutSeconds),
        };
    }

    private static string Read(
        IReadOnlyDictionary<string, string?> environment, string key, string fallback) =>
        environment.TryGetValue(key, out var value) && !string.IsNullOrWhiteSpace(value)
            ? value
            : fallback;

    private static int ReadInt(
        IReadOnlyDictionary<string, string?> environment, string key, int fallback) =>
        int.TryParse(Read(environment, key, string.Empty), out var value) ? value : fallback;
}
