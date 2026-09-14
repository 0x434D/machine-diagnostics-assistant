using System.Text.Json;
using System.Text.Json.Serialization;
using Opc.Ua;

namespace Gateway.Opc;

/// <summary>
/// How one signal is ingested: whether to subscribe at all, the absolute deadband to put on
/// the monitored item (null for none), and the history page size Task 10's backfill reads it
/// with.
/// </summary>
public sealed record SignalRule(bool Subscribe, double? Deadband, int PageSize);

/// <summary>
/// How one event type is read from history: the number of events one HistoryRead asks for.
///
/// <para>No <c>Subscribe</c> and no deadband. §3.4's event history has no off switch — the
/// policy keys on names the address space publishes and an event type is not a variable —
/// and a deadband compares magnitudes, which an event is not.</para>
/// </summary>
public sealed record EventRule(int PageSize);

/// <summary>
/// §5.1's per-signal ingest policy, mounted rather than compiled in (M2 design D3).
///
/// <para><b>It fails open, and that is the design.</b> The topology is discovered, so the
/// gateway will always meet signals this file does not name — that is what "point it at a
/// real plant" means. An unrecognised signal is therefore subscribed with no deadband and
/// the default page size. A policy that skipped it instead would lose a whole stream and
/// pass every test, which is the exact failure shape §5.1 was written against.</para>
///
/// <para>The file's <i>shape</i> fails the other way. Its keys are a closed, enumerated set,
/// so a key nobody recognises is a typo an operator must learn about at boot, not a setting
/// silently dropped. Open sets fail open; enumerated ones fail loud.</para>
/// </summary>
public sealed class SignalPolicy
{
    /// <summary>
    /// Used when neither the signal nor <c>defaults</c> names one. This is the only place a
    /// variable stream's page size comes from — the backfill asks the policy per signal, so a
    /// signal the file does not name is still paged at a stated number rather than at one the
    /// reader carries privately.
    /// </summary>
    public const int DefaultPageSize = 1_000;

    /// <summary>
    /// Used for an event type neither <c>events</c> nor <c>defaults.event_page_size</c>
    /// names. Two thousand rather than <see cref="DefaultPageSize"/>: an event type a plant
    /// publishes and this file does not name is one nobody has measured, and the one thing
    /// known about every event type §4.1 has is that a station emits at most a few per part —
    /// so a window at a 6 s takt holds hundreds, not thousands, and a page that does not
    /// cover a window is halved rather than read.
    /// </summary>
    public const int DefaultEventPageSize = 2_000;

    // Explicit property names throughout, so matching never depends on a casing option, and
    // one cached instance because a new one per parse rebuilds the reflection cache (CA1869).
    private static readonly JsonSerializerOptions ParseOptions = new()
    {
        PropertyNameCaseInsensitive = false,
    };

    private readonly IReadOnlyDictionary<string, Entry> _signals;
    private readonly IReadOnlyDictionary<string, EventEntry> _events;
    private readonly int _defaultPageSize;
    private readonly int _defaultEventPageSize;

    private SignalPolicy(
        IReadOnlyDictionary<string, Entry> signals,
        IReadOnlyDictionary<string, EventEntry> events,
        int defaultPageSize,
        int defaultEventPageSize)
    {
        _signals = signals;
        _events = events;
        _defaultPageSize = defaultPageSize;
        _defaultEventPageSize = defaultEventPageSize;
    }

    /// <summary>The signal names this policy names. Diagnostics only; ingest never asks.</summary>
    public IEnumerable<string> KnownSignals => _signals.Keys;

    /// <summary>The event types this policy names. Diagnostics only.</summary>
    public IEnumerable<string> KnownEventTypes => _events.Keys;

    /// <summary>
    /// Reads the mounted policy.
    /// </summary>
    /// <exception cref="FileNotFoundException">
    /// nothing is mounted at <paramref name="path"/>. A start-up failure on purpose: an
    /// operator who mounted the file wrong has to find out at boot, not from a deadband that
    /// quietly stopped applying.
    /// </exception>
    /// <exception cref="JsonException">the file is not a policy.</exception>
    public static SignalPolicy Load(string path)
    {
        if (!File.Exists(path))
        {
            throw new FileNotFoundException(
                $"no signal policy at '{path}'; mount one there or set GATEWAY_SIGNAL_POLICY",
                path);
        }

        return Parse(File.ReadAllText(path));
    }

    /// <exception cref="JsonException">
    /// the text is not JSON, is not an object, or carries a key or a number this policy has no
    /// meaning for.
    /// </exception>
    public static SignalPolicy Parse(string json)
    {
        var document = JsonSerializer.Deserialize<Document>(json, ParseOptions)
            ?? throw new JsonException("the signal policy is empty");

        var defaultPageSize = PageSize(document.Defaults?.PageSize, "defaults") ?? DefaultPageSize;
        var defaultEventPageSize =
            PageSize(document.Defaults?.EventPageSize, "defaults") ?? DefaultEventPageSize;

        var signals = new Dictionary<string, Entry>(StringComparer.Ordinal);
        foreach (var (name, entry) in
            document.Signals ?? new Dictionary<string, Entry>(StringComparer.Ordinal))
        {
            signals[name] = Validated(name, entry);
        }

        var events = new Dictionary<string, EventEntry>(StringComparer.Ordinal);
        foreach (var (name, entry) in
            document.Events ?? new Dictionary<string, EventEntry>(StringComparer.Ordinal))
        {
            PageSize(entry.PageSize, $"event type '{name}'");
            events[name] = entry;
        }

        return new SignalPolicy(signals, events, defaultPageSize, defaultEventPageSize);
    }

    /// <summary>
    /// The rule for one event type. Fails open exactly as <see cref="For"/> does, and for
    /// the same reason: a plant that publishes a type this file does not name is read at a
    /// stated default rather than not read.
    /// </summary>
    public EventRule ForEvent(string typeName)
    {
        ArgumentNullException.ThrowIfNull(typeName);

        return new EventRule(
            _events.TryGetValue(typeName, out var entry) && entry.PageSize is { } size
                ? size
                : _defaultEventPageSize);
    }

    /// <summary>
    /// The rule for one discovered stream. <paramref name="type"/> is the variable's OPC UA
    /// data type as browsed, not as guessed from its name.
    /// </summary>
    public SignalRule For(string signal, BuiltInType type)
    {
        ArgumentNullException.ThrowIfNull(signal);

        // The fail-open default. Not a fallback for an error case — it is the case, because a
        // discovered topology guarantees signals no file names.
        if (!_signals.TryGetValue(signal, out var entry))
        {
            return new SignalRule(Subscribe: true, Deadband: null, PageSize: _defaultPageSize);
        }

        return new SignalRule(
            Subscribe: entry.Subscribe,
            Deadband: IsNumeric(type) ? entry.Deadband : null,
            PageSize: entry.PageSize ?? _defaultPageSize);
    }

    /// <summary>
    /// A deadband compares magnitudes, so it means nothing on a string or a timestamp — and
    /// the server answers an absolute deadband on one with BadDeadbandFilterInvalid, which
    /// would leave that stream unmonitored. Dropped rather than refused: the policy names
    /// signals, the plant decides their types, and the failure of that pair should be extra
    /// rows rather than a missing stream.
    /// </summary>
    private static bool IsNumeric(BuiltInType type) => type
        is BuiltInType.SByte or BuiltInType.Byte
        or BuiltInType.Int16 or BuiltInType.UInt16
        or BuiltInType.Int32 or BuiltInType.UInt32
        or BuiltInType.Int64 or BuiltInType.UInt64
        or BuiltInType.Float or BuiltInType.Double;

    private static Entry Validated(string name, Entry entry)
    {
        if (entry.Deadband is { } deadband && (deadband < 0 || !double.IsFinite(deadband)))
        {
            throw new JsonException(
                $"signal '{name}' has deadband {deadband}; a deadband is a non-negative width");
        }

        PageSize(entry.PageSize, $"signal '{name}'");
        return entry;
    }

    private static int? PageSize(int? value, string where) =>
        value is null or > 0
            ? value
            : throw new JsonException($"{where} has page_size {value}; a page holds at least one row");

    // Deserialisation shapes. Disallow, not ignore: "deadbnd" would otherwise read as a signal
    // with no deadband, which is indistinguishable from one that is meant to have none.
    [JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
    private sealed record Document
    {
        [JsonPropertyName("_comment")]
        public string? Comment { get; init; }

        /// <summary>
        /// The derivation the five event page sizes share, kept once rather than repeated in
        /// each entry's <c>why</c>. Declared here because the key set is closed: an
        /// undeclared key is a typo an operator has to learn about at boot.
        /// </summary>
        [JsonPropertyName("_events_comment")]
        public string? EventsComment { get; init; }

        [JsonPropertyName("defaults")]
        public Defaults? Defaults { get; init; }

        [JsonPropertyName("signals")]
        public IReadOnlyDictionary<string, Entry>? Signals { get; init; }

        /// <summary>
        /// Keyed by event type browse name, not by station: what one page of a stream weighs
        /// is what its events carry, and a station's own key would have to be re-measured
        /// the day two stations swap which type they emit.
        /// </summary>
        [JsonPropertyName("events")]
        public IReadOnlyDictionary<string, EventEntry>? Events { get; init; }
    }

    [JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
    private sealed record Defaults
    {
        [JsonPropertyName("page_size")]
        public int? PageSize { get; init; }

        [JsonPropertyName("event_page_size")]
        public int? EventPageSize { get; init; }
    }

    [JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
    private sealed record EventEntry
    {
        [JsonPropertyName("page_size")]
        public int? PageSize { get; init; }

        /// <summary>Documentation, carried so the reason lives beside the number it explains.</summary>
        [JsonPropertyName("why")]
        public string? Why { get; init; }
    }

    [JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
    private sealed record Entry
    {
        [JsonPropertyName("deadband")]
        public double? Deadband { get; init; }

        [JsonPropertyName("page_size")]
        public int? PageSize { get; init; }

        /// <summary>
        /// The one way to keep a discovered variable stream out of storage: the live
        /// subscription does not monitor it and the history backfill does not read it. Both,
        /// because while only the first honoured this the key moved a stream from one write
        /// path to the other rather than switching it off. Default true, so omitting it agrees
        /// with what an unnamed signal gets — without it the fail-open default would be the
        /// only behaviour there is, and "unknown signals are subscribed" would say nothing.
        ///
        /// <para>Variable streams only. §3.4's event stream is not a signal name, so no key
        /// here reaches it.</para>
        /// </summary>
        [JsonPropertyName("subscribe")]
        public bool Subscribe { get; init; } = true;

        /// <summary>Documentation, carried so the reason lives beside the number it explains.</summary>
        [JsonPropertyName("why")]
        public string? Why { get; init; }
    }
}
