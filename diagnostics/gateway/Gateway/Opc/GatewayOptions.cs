using System.Globalization;

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

    // The same three values diagnostics/auth/config.py defaults to. Restated rather than
    // shared because they cannot be shared across the language split; that they are equal is
    // asserted, from the fixture file both suites read.
    public const string DefaultTokenAudience = "machine-agent";
    public const string DefaultTokenIssuer = "https://issuer.test/machine-agent";
    public const string DefaultTokenAlgorithm = "RS256";
    public const string DefaultTokenRoleClaim = "role";

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

    /// <summary>
    /// What the <c>analysis</c> role of <c>005_m3_read_layer.sql</c> authenticates with.
    /// Empty leaves the role unable to log in, which is the right state for a deployment
    /// that runs no analysis service.
    ///
    /// <para>The migration creates that role deliberately without a password: a literal in
    /// an embedded SQL resource is a credential compiled into the binary and identical
    /// everywhere it is deployed. The gateway holds the only connection privileged enough
    /// to set one, and already owns applying the schema.</para>
    /// </summary>
    public string AnalysisRolePassword { get; init; } = "";
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

    // --- §10.5's identity settings ----------------------------------------------------------
    //
    // Read from AUTH_*, not GATEWAY_*, and that is the point rather than an inconsistency.
    // These are the *deployment's* identity settings: `diagnostics/auth` reads exactly these
    // names in the three Python services, and the gateway has to be talking about the same
    // issuer, the same audience and the same claim. A GATEWAY_AUDIENCE that an operator had
    // to keep equal to AUTH_AUDIENCE by hand is precisely the silent drift that having the
    // rule written twice already threatens, and the one thing the shared token fixtures
    // exist to stop.

    /// <summary>
    /// The issuer's public key, PEM-encoded. There is no default and the empty value fails
    /// closed: a gateway nobody configured trusts nothing and refuses everything, which is
    /// the only resting state that leaves §14's claim true where the setting was forgotten.
    ///
    /// <para>A key rather than a JWKS URL because M5 is deliberately slim and there is no
    /// issuer to fetch a key set from — <c>scripts/mint-token.py</c> stands where Zitadel
    /// will. When Zitadel arrives this is where its JWKS URL goes.</para>
    /// </summary>
    public string TokenPublicKey { get; init; } = "";

    /// <summary>§10.5's audience check. A token minted for another service must not open this one.</summary>
    public string TokenAudience { get; init; } = DefaultTokenAudience;

    /// <summary>The one issuer this application is a client of (§10.5).</summary>
    public string TokenIssuer { get; init; } = DefaultTokenIssuer;

    /// <summary>
    /// One algorithm, not a list. A list of exactly one is what refuses <c>alg: none</c> and
    /// the HMAC-with-the-public-key confusion; a deployment needing a second has changed
    /// issuers rather than gained a setting.
    /// </summary>
    public string TokenAlgorithm { get; init; } = DefaultTokenAlgorithm;

    /// <summary>
    /// Which claim carries §10.5's role. Configuration because it is the one part of the
    /// claim shape the issuer decides for us — Zitadel's default is a namespaced key.
    /// </summary>
    public string TokenRoleClaim { get; init; } = DefaultTokenRoleClaim;

    /// <summary>
    /// How much clock difference between the issuer and this gateway a token's <c>exp</c> is
    /// given.
    ///
    /// <para><b>Zero, and the default is a statement rather than a placeholder.</b> It is
    /// the same zero <c>diagnostics/auth/config.py</c> defaults to, for the same reason:
    /// every container in this deployment runs on one host and reads one kernel clock, so
    /// there is no skew to tolerate, and any non-zero default silently extends the life of
    /// every token ever issued by that much. The setting exists because the issuer will one
    /// day be somewhere else, and moving it then is configuration rather than a release —
    /// and because the two implementations have to be able to disagree about it out loud.
    /// </para>
    /// </summary>
    public TimeSpan TokenClockSkew { get; init; } = TimeSpan.Zero;

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
            AnalysisRolePassword = Read(environment, "GATEWAY_ANALYSIS_PASSWORD", ""),
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
            TokenPublicKey = Read(environment, "AUTH_PUBLIC_KEY", ""),
            TokenAudience = Read(environment, "AUTH_AUDIENCE", DefaultTokenAudience),
            TokenIssuer = Read(environment, "AUTH_ISSUER", DefaultTokenIssuer),
            TokenAlgorithm = Read(environment, "AUTH_ALGORITHM", DefaultTokenAlgorithm),
            TokenRoleClaim = Read(environment, "AUTH_ROLE_CLAIM", DefaultTokenRoleClaim),
            TokenClockSkew = TimeSpan.FromSeconds(ReadClockSkew(environment)),
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
    /// The clock skew, in seconds, refused unless it is a non-negative number.
    ///
    /// <para>Refused rather than defaulted, and unlike most of the keys above. It governs how
    /// long an expired token goes on being accepted, so a typo quietly becoming zero hides an
    /// operator's fix, and a negative value expires every token early for a reason nobody
    /// would look for. Unset is a different thing from mistyped and still takes the
    /// default.</para>
    /// </summary>
    /// <exception cref="ArgumentException">the value is set and is not a non-negative number.</exception>
    private static double ReadClockSkew(IReadOnlyDictionary<string, string?> environment)
    {
        const string Key = "AUTH_CLOCK_SKEW_SECONDS";
        var raw = Read(environment, Key, string.Empty);
        if (raw.Length == 0)
        {
            return 0.0;
        }

        return double.TryParse(raw, CultureInfo.InvariantCulture, out var value) && value >= 0
            ? value
            : throw new ArgumentException(
                $"{Key} is '{raw}'; clock skew is a non-negative number of seconds",
                nameof(environment));
    }

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
