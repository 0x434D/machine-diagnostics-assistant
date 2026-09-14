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

    /// <summary>
    /// Mounted, not compiled in: §5.1's deadbands are per signal and per plant, and a rebuild
    /// is the wrong unit of change for a number an engineer tunes against jitter they measured.
    /// </summary>
    public const string DefaultSignalPolicyPath = "/config/signals.json";

    public required string ApplicationUri { get; init; }
    public required string EndpointUrl { get; init; }
    public required string SecurityMode { get; init; }
    public required string PkiRoot { get; init; }
    public int MaxByteStringLength { get; init; } = DefaultMaxByteStringLength;
    public int MaxMessageSize { get; init; } = DefaultMaxMessageSize;
    public int ConnectTimeoutSeconds { get; init; } = DefaultConnectTimeoutSeconds;
    public string QueuePath { get; init; } = DefaultQueuePath;
    public string SignalPolicyPath { get; init; } = DefaultSignalPolicyPath;

    /// <summary>Empty until Postgres exists for this deployment; the queue then simply fills.</summary>
    public string PostgresConnectionString { get; init; } = "";
    public int DrainBatchSize { get; init; } = 200;
    public int DrainIdleMs { get; init; } = 250;
    public int DrainRetryMs { get; init; } = 2_000;

    /// <summary>How often to re-read Clock.Phase while the plant is still catching up.</summary>
    public int PhasePollMs { get; init; } = 1_000;

    /// <summary>
    /// One backfill window, and the unit the reconciliation ledger records.
    ///
    /// <para>It is not a guarantee against truncation and never was. An hour holds ~600 values
    /// of a station signal at a 6 s takt, but ~1,200 of a buffer <c>Level</c> — measured
    /// against the live plant, above the page the policy gives it, and truncated silently
    /// every window until <see cref="HistoryBackfill.ClassifyPage"/> was the thing deciding.
    /// What this number actually governs is how much work a window that cannot be believed
    /// costs before it is halved.</para>
    /// </summary>
    public TimeSpan BackfillWindow { get; init; } = TimeSpan.FromHours(1);

    /// <summary>
    /// How far subdivision may go, for any stream. At a 6 s takt a 30 s window holds ~5 parts
    /// and ~10 buffer moves, far under any page size, so reaching this floor means something
    /// other than volume is wrong and the run fails rather than storing a short window quietly.
    ///
    /// <para>Measured: against a one-hour window S3's event stream halves to 1 m 52.5 s, which
    /// leaves two halvings of headroom and no more — at the page of 25 its images force. The
    /// four event types M2b adds carry none and are paged in the thousands, so their windows
    /// do not halve at all; a stream that does is answered by shortening
    /// <see cref="BackfillWindow"/> or by its own page_size, never by lowering this. The floor
    /// is what stops subdivision from hiding a defect that is not volume.</para>
    /// </summary>
    public TimeSpan MinimumBackfillWindow { get; init; } = TimeSpan.FromSeconds(30);

    /// <summary>
    /// How far back to reach on a first boot, when storage holds nothing.
    ///
    /// 33 h, not the 18 h §3.2 states. The plant lane probed 366 day-boundaries: the worst
    /// case is a boot at 05:00 *inside* a running night shift, where the last completed one
    /// started 33 h earlier — 24 h of day-gap plus a 9 h autumn fall-back night, supremum
    /// 1 day 8:59:59.999999 on 2026-10-25. At 18 h the flagship question has no data for
    /// exactly the shift it asks about, which is the failure this depth exists to prevent.
    /// </summary>
    public TimeSpan HistoryDepth { get; init; } = TimeSpan.FromHours(33);
    public int PublishingIntervalMs { get; init; } = 250;
    public int SamplingIntervalMs { get; init; } = 250;
    public uint QueueSize { get; init; } = 100;
    public uint EventQueueSize { get; init; } = 200;

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
            SignalPolicyPath = Read(
                environment, "GATEWAY_SIGNAL_POLICY", DefaultSignalPolicyPath),
            PostgresConnectionString = Read(environment, "GATEWAY_POSTGRES", ""),
            DrainBatchSize = ReadInt(environment, "GATEWAY_DRAIN_BATCH_SIZE", 200),
            BackfillWindow = TimeSpan.FromMinutes(
                ReadWindowLength(environment, "GATEWAY_BACKFILL_WINDOW_MINUTES", 60)),
            MinimumBackfillWindow = TimeSpan.FromSeconds(
                ReadWindowLength(environment, "GATEWAY_MINIMUM_BACKFILL_WINDOW_SECONDS", 30)),
            HistoryDepth = TimeSpan.FromHours(
                ReadInt(environment, "GATEWAY_HISTORY_DEPTH_HOURS", 33)),
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

    /// <summary>
    /// A window length, refused unless it is a positive whole number.
    ///
    /// <para>Zero is the one value in this file that cannot make progress rather than merely
    /// being wrong: <c>HistoryBackfill.RunAsync</c> walks its windows with
    /// <c>start = start.Add(BackfillWindow)</c>, so a zero-length window never advances and the
    /// backfill spins for ever, accumulating ledger entries — a hang with no error, which is
    /// worse than every misconfiguration this file can otherwise produce. Negative is the same
    /// loop running backwards.</para>
    ///
    /// <para>Unparseable is refused too, and unlike the other keys here. The value is a
    /// length of time that governs whether a truncated stream can be subdivided at all; a typo
    /// silently becoming the default is the shape §5.1's policy keys were made to fail on.</para>
    /// </summary>
    /// <exception cref="ArgumentException">the value is set and is not a positive integer.</exception>
    private static int ReadWindowLength(
        IReadOnlyDictionary<string, string?> environment, string key, int fallback)
    {
        var raw = Read(environment, key, string.Empty);
        if (raw.Length == 0)
        {
            return fallback;
        }

        return int.TryParse(raw, out var value) && value > 0
            ? value
            : throw new ArgumentException(
                $"{key} is '{raw}'; a backfill window must be a whole number greater than zero, "
                + "and a window of zero never advances",
                nameof(environment));
    }
}
