using System.Diagnostics;
using System.Runtime.ExceptionServices;
using Gateway.Ingest;
using Microsoft.Extensions.Logging;
using Opc.Ua;

namespace Gateway.Opc;

public sealed record WindowReport(
    DateTime From, DateTime To, string Stream, int RowsReturned, int Pages, long DurationMs);

public sealed record BackfillReport(IReadOnlyList<WindowReport> Windows)
{
    public int RowsReturned => Windows.Sum(window => window.RowsReturned);
    public int Pages => Windows.Sum(window => window.Pages);
    public long DurationMs => Windows.Sum(window => window.DurationMs);
}

/// <summary>A half-open read window, <c>[From, To)</c>.</summary>
public sealed record BackfillWindow(DateTime From, DateTime To);

/// <summary>
/// What one page of history proves about the window it came from.
/// <c>Truncated</c> does not mean rows are known to be missing — it means the answer cannot
/// be distinguished from one that lost rows, which for this system is the same thing.
/// </summary>
public enum PageVerdict
{
    Complete,
    Truncated,
}

/// <summary>
/// One HistoryRead against one node: the details, one continuation point in and one out.
///
/// <para>Taken as a delegate rather than as a session because paging, the truncation verdict
/// and the subdivision are the whole of what this class is for, and through M1 the only way
/// to reach any of them was a live server — which is how a guard that covered events and not
/// variables shipped green. <see cref="Through"/> is the live one.</para>
/// </summary>
public delegate Task<HistoryReadResult> HistoryReadCall(
    NodeId node, ExtensionObject details, byte[]? continuationPoint, bool release,
    CancellationToken ct);

/// <summary>
/// Pulls history in bounded windows. §4.3: subscriptions are for live data, HistoryRead is
/// for the past, and the gateway sets its own pace because it is pull-based.
/// </summary>
public sealed partial class HistoryBackfill
{
    /// <summary>
    /// Pre-flight F1, re-measured on the pinned interpreter (measurements/r1-probe.txt):
    /// <c>read_raw_history</c> returns at most this many values <i>per call</i>, with no
    /// exception and no bad StatusCode. It is a property of one read, not of a window: a
    /// window legitimately spanning ten full pages of 1,000 is not truncated, and refusing it
    /// would be refusing paging that works.
    /// </summary>
    public const int SilentTruncationCeiling = 10_000;

    private readonly HistoryReadCall _read;
    private readonly AddressSpace _space;
    private readonly SignalPolicy _policy;
    private readonly Func<IngestRecord, Task> _onRecord;
    private readonly GatewayOptions _options;
    private readonly ILogger<HistoryBackfill> _logger;

    public double Progress { get; private set; }

    public HistoryBackfill(
        HistoryReadCall read, AddressSpace space, SignalPolicy policy,
        Func<IngestRecord, Task> onRecord, GatewayOptions options,
        ILogger<HistoryBackfill> logger)
    {
        _logger = logger;
        _read = read;
        _space = space;
        _policy = policy;
        _onRecord = onRecord;
        _options = options;
    }

    /// <summary>
    /// The live call. The session is resolved per read, not captured: a reconnect replaces the
    /// session object and disposes the old one, and this class outlives that — holding the
    /// original meant every backfill after the first outage read from a disposed session,
    /// returned nothing, and reported the gap closed.
    /// </summary>
    public static HistoryReadCall Through(Func<ISession> session)
    {
        ArgumentNullException.ThrowIfNull(session);

        return async (node, details, continuationPoint, release, ct) =>
        {
            var response = await session().HistoryReadAsync(
                null,
                details,
                TimestampsToReturn.Both,   // §4.2 needs both
                release,
                [new HistoryReadValueId { NodeId = node, ContinuationPoint = continuationPoint }],
                ct).ConfigureAwait(false);

            // One request, one result. A server that answers with none would otherwise surface
            // as an IndexOutOfRangeException naming neither the node nor the read.
            return response.Results.Count == 1
                ? response.Results[0]
                : throw new ServiceResultException(
                    StatusCodes.BadUnexpectedError,
                    $"HistoryRead on {node} returned {response.Results.Count} results for one node");
        };
    }

    /// <summary>
    /// Whether a page can be believed to be the end of its window.
    ///
    /// <para>M1's measured defect, and the reason this is a named function rather than a
    /// condition inside one reader: asyncua's history_sql caps its SQL at the <i>client's</i>
    /// page size and emits a continuation point only when the result exceeds the
    /// <i>server's</i> cap, so a client paging below that cap is never told there is more.
    /// One page came back, no continuation point, and the run reported success over 4% of the
    /// event history. Variables have the identical shape and were safe only because their page
    /// size happened to equal the server's cap.</para>
    /// </summary>
    public static PageVerdict ClassifyPage(int rowsReturned, int pageSize, bool hasContinuation) =>
        !hasContinuation && rowsReturned >= pageSize ? PageVerdict.Truncated : PageVerdict.Complete;

    /// <summary>
    /// The two halves of a window that cannot be believed. Halving, not retrying: a retry at
    /// the same width returns the same truncated page forever.
    /// </summary>
    /// <exception cref="InvalidOperationException">
    /// the window is already at or below <paramref name="floor"/>. At a 6 s takt a 30 s window
    /// holds ~5 parts, well under any page size, so reaching the floor means something other
    /// than volume is wrong and the run must fail rather than store a short window quietly.
    /// </exception>
    public static IReadOnlyList<BackfillWindow> Subdivide(
        DateTime from, DateTime to, TimeSpan floor)
    {
        var span = to - from;
        if (span <= floor)
        {
            throw new InvalidOperationException(
                $"window {from:O}..{to:O} spans {span} and filled its page with no continuation "
                + $"point; halving it would cross the {floor} floor, so rows would be lost "
                + "silently. Something other than volume is wrong with this stream");
        }

        var middle = from.Add(span / 2);
        return [new BackfillWindow(from, middle), new BackfillWindow(middle, to)];
    }

    /// <param name="streamsReadBefore">
    /// Every stream this gateway's ledger already names. A stream that was read once and is
    /// not discovered now is a stream that stopped being published, and it must stop the run
    /// rather than shrink it — see <see cref="DiscoveredStreams"/>.
    /// </param>
    public async Task<BackfillReport> RunAsync(
        DateTime from, DateTime to, IReadOnlySet<string> streamsReadBefore, CancellationToken ct)
    {
        var streams = DiscoveredStreams(streamsReadBefore);
        var windows = new List<WindowReport>();
        var total = (to - from).TotalSeconds;

        for (var start = from; start < to; start = start.Add(_options.BackfillWindow))
        {
            var end = start.Add(_options.BackfillWindow);
            if (end > to)
            {
                end = to;
            }

            foreach (var stream in streams)
            {
                await ReadWindowAsync(windows, stream, start, end, ct).ConfigureAwait(false);
            }

            Progress = total <= 0 ? 1.0 : (start - from).TotalSeconds / total;
        }

        Progress = 1.0;
        return new BackfillReport(windows);
    }

    /// <summary>
    /// Every stream the discovered topology publishes and §5.1's policy subscribes, each with
    /// the page size that policy gives it. Nothing here names a signal: a literal set would
    /// silently cover fewer streams than the plant has the moment one of them is renamed.
    ///
    /// <para><b>A shorter list than last time is a failure, not a smaller run.</b> Discovery
    /// alone cannot tell "this plant has 25 streams" from "this plant had 26 and one stopped
    /// publishing" — and with nothing asserting on the count, the second case backfilled 25
    /// streams, wrote 25 ledger rows, and answered <c>/reconcile</c> with 25 rows all green.
    /// A green run over missing data, which is the shape this whole task exists to refuse.
    /// The ledger is the only record of what this plant published before, so it is what the
    /// discovered set is held against.</para>
    ///
    /// <para>It says nothing on a first run against an empty ledger, because then there is
    /// genuinely nothing to compare with; that case is the topology's to be right about.</para>
    ///
    /// <para>That guard is held against what the plant <i>publishes</i>, not against what this
    /// run reads, because the two are no longer the same list. A stream the policy switched off
    /// is still discovered; counting it as vanished would turn the operator's own decision into
    /// a boot failure the next time the gateway started.</para>
    /// </summary>
    private List<BackfillStream> DiscoveredStreams(IReadOnlySet<string> streamsReadBefore)
    {
        ArgumentNullException.ThrowIfNull(streamsReadBefore);

        // Anchored on the station that publishes the inspection event rather than on a
        // hardcoded S3, and loud rather than empty: a plant whose event stream this gateway
        // cannot find is one whose §3.4 history would be quietly absent from every answer.
        if (!_space.Stations.Any(station => station.EmitsEvents))
        {
            throw new InvalidOperationException(
                "no discovered station publishes events; there is nothing to backfill from");
        }

        var streams = new List<BackfillStream>();
        var discovered = new List<string>();
        var skipped = new List<string>();
        foreach (var station in _space.Stations)
        {
            foreach (var signal in station.Signals)
            {
                var name = $"{station.Code}.{signal.Name}";
                discovered.Add(name);

                // The policy's one deliberate loss, honoured here as well as in the live
                // subscription. While only the subscription read it, `subscribe: false` moved
                // a stream from "stored live" to "stored by the backfill instead" -- every
                // window read, every row written, and a ledger row claiming a stream the
                // operator had been told was off. Three comments said otherwise.
                var rule = _policy.For(signal.Name, signal.Type);
                if (!rule.Subscribe)
                {
                    skipped.Add(name);
                    continue;
                }

                var key = new StreamKey(StreamOwner.Station, station.Code, signal.Name);
                streams.Add(new BackfillStream(
                    name,
                    (from, to, ct) =>
                        ReadVariablePagesAsync(signal.NodeId, key, rule.PageSize, from, to, ct)));
            }

            if (station.EmitsEvents)
            {
                // The policy sets the page size and nothing else. §3.4's event history has no
                // off switch -- the policy's `subscribe` key names variables and an event
                // type is not one -- the same asymmetry the live subscription has, stated
                // because the alternative is two files that look like they disagree.
                var name = $"{station.Code}.{Subscriptions.EventStream}";
                discovered.Add(name);

                var spec = EventStreamSpec.For(station);
                var pageSize = EventPageSize(spec);
                streams.Add(new BackfillStream(
                    name,
                    (from, to, ct) =>
                        ReadEventPagesAsync(station, spec, pageSize, from, to, ct)));
            }
        }

        foreach (var buffer in _space.Buffers)
        {
            var signal = Subscriptions.BufferLevelSignal;
            var name = $"{buffer.Code}.{signal}";
            discovered.Add(name);

            var rule = _policy.For(signal, buffer.LevelType);
            if (!rule.Subscribe)
            {
                skipped.Add(name);
                continue;
            }

            var key = new StreamKey(StreamOwner.Buffer, buffer.Code, signal);
            streams.Add(new BackfillStream(
                name,
                (from, to, ct) =>
                    ReadVariablePagesAsync(
                        buffer.LevelNodeId, key, rule.PageSize, from, to, ct)));
        }

        var vanished = streamsReadBefore
            .Except(discovered, StringComparer.Ordinal)
            .Order(StringComparer.Ordinal)
            .ToList();
        if (vanished.Count > 0)
        {
            // The ledger stores the name this method built when the window was read, so
            // renaming a constant that feeds it renames nothing already written:
            // Subscriptions.EventStream went "InspectionResult" -> "Events" in M2b, and
            // against a ledger written before that, every boot found S3.InspectionResult
            // missing and threw, for ever. The guard is right to be fatal — it cannot tell
            // the two cases apart — but "no longer published" alone sends the operator to a
            // plant that is fine, so the message carries the evidence that can: a name on the
            // same owner that has never been in the ledger, appearing on the boot another
            // left it. Evidence, not a verdict — a station can gain one stream and lose a
            // different one in the same change, and only a reader who knows what the two
            // carry can tell that from a rename. Streams the owner published all along
            // distinguish nothing and are left out.
            var owners = vanished
                .Select(name => name.Split('.', 2)[0])
                .ToHashSet(StringComparer.Ordinal);
            var appeared = discovered
                .Where(name => owners.Contains(name.Split('.', 2)[0])
                    && !streamsReadBefore.Contains(name))
                .Order(StringComparer.Ordinal)
                .ToList();

            throw new InvalidOperationException(
                $"{vanished.Count} stream(s) this gateway has backfilled before are no longer "
                + $"published and would be missing from the ledger silently: "
                + $"{string.Join(", ", vanished)}. {discovered.Count} streams were discovered. "
                + "The same stations or buffers published for the first time on this boot: "
                + $"{(appeared.Count > 0 ? string.Join(", ", appeared) : "nothing")}. "
                + "If one of those is the vanished stream under a new name, this is a rename "
                + "in this gateway rather than a plant that stopped publishing: "
                + "backfill_windows.stream holds the name this gateway built when the window "
                + "was read, so renaming the constant renames nothing already written — "
                + "update those ledger rows to the new name, or restore the old one. A "
                + "station can also gain one stream and lose another, so compare what they "
                + "carry before deciding. With nothing newly published, the plant has "
                + "stopped.");
        }

        // Said out loud for the same reason the subscription says it: a discovered stream that
        // stops being stored is indistinguishable from the loss every other guard in this file
        // exists to prevent, unless something names it.
        if (skipped.Count > 0)
        {
            LogSkippedStreams(_logger, skipped.Count, string.Join(", ", skipped));
        }

        return streams;
    }

    /// <summary>
    /// Reads a window, halving it whenever the result cannot be trusted, until every part comes
    /// back short of its page. Each part that does is its own ledger row: the ledger's
    /// duplicate model is "one page boundary per page beyond the first, per window", and a row
    /// that aggregated several real windows would account for boundaries that are not there.
    ///
    /// <para>Measured against the live plant: every one-hour event window returned exactly the
    /// page size — 25 rows where ~600 existed — losing 96% of the event history while the run
    /// reported success. The F1 ceiling guard could not see it, because 25 is nowhere near
    /// 10,000.</para>
    /// </summary>
    private async Task ReadWindowAsync(
        List<WindowReport> into, BackfillStream stream, DateTime from, DateTime to,
        CancellationToken ct)
    {
        var outcome = await stream.Read(from, to, ct).ConfigureAwait(false);
        if (outcome.Verdict is PageVerdict.Complete)
        {
            into.Add(new WindowReport(
                from, to, stream.Name, outcome.Rows, outcome.Pages, outcome.DurationMs));
            return;
        }

        // The truncated attempt's rows were already handed to the writer and are not dropped:
        // the halves re-read the same range and the upsert absorbs the overlap. They are left
        // out of the ledger because the ledger's claim is about windows that were believed.
        foreach (var part in Subdivide(from, to, _options.MinimumBackfillWindow))
        {
            await ReadWindowAsync(into, stream, part.From, part.To, ct).ConfigureAwait(false);
        }
    }

    private async Task<PageOutcome> ReadVariablePagesAsync(
        NodeId node, StreamKey key, int pageSize, DateTime from, DateTime to, CancellationToken ct)
    {
        var details = new ExtensionObject(new ReadRawModifiedDetails
        {
            IsReadModified = false,
            StartTime = from,
            EndTime = to,
            // Push the limit into the request. Left at 0 the server materialises the whole
            // remaining range per page and slices it in memory.
            NumValuesPerNode = (uint)pageSize,
            ReturnBounds = false,
        });

        return await ReadPagesAsync(
            node, details, pageSize,
            async result =>
            {
                var data = (HistoryData)ExtensionObject.ToEncodeable(result);
                foreach (var value in data.DataValues)
                {
                    await _onRecord(Subscriptions.ToDataChangeRecord(key, node.ToString(), value))
                        .ConfigureAwait(false);
                }

                return data.DataValues.Count;
            },
            ct).ConfigureAwait(false);
    }

    /// <summary>
    /// How many events one read of this stream asks for: the smallest page any type on it is
    /// given.
    ///
    /// <para>The smallest, because one read covers the whole stream — asyncua historises
    /// events per emitting node, not per type — so a station carrying an imaged type beside
    /// an unimaged one is bounded by the imaged one's bytes. Today that is S3 alone, at 25;
    /// S1's two types share a page because neither carries bytes.</para>
    /// </summary>
    private int EventPageSize(EventStreamSpec stream) =>
        stream.Types.Min(type => _policy.ForEvent(type.TypeName).PageSize);

    private async Task<PageOutcome> ReadEventPagesAsync(
        DiscoveredStation station, EventStreamSpec stream, int pageSize, DateTime from,
        DateTime to, CancellationToken ct)
    {
        var node = station.NodeId;
        var details = new ExtensionObject(new ReadEventDetails
        {
            StartTime = from,
            EndTime = to,
            NumValuesPerNode = (uint)pageSize,
            // The identical filter the live subscription uses. A second filter would be a
            // second statement of the field order, and the order is the decoding contract.
            Filter = stream.BuildFilter(),
        });

        return await ReadPagesAsync(
            node, details, pageSize,
            async result =>
            {
                var data = (HistoryEvent)ExtensionObject.ToEncodeable(result);
                foreach (var entry in data.Events)
                {
                    await _onRecord(stream.Decode(
                        station.Code, node.ToString(), entry.EventFields)).ConfigureAwait(false);
                }

                return data.Events.Count;
            },
            ct).ConfigureAwait(false);
    }

    /// <summary>
    /// The one pager, for variables and for events. Two would be two page sizes, two
    /// continuation-point lifetimes and two chances for only one of them to be guarded.
    /// </summary>
    private async Task<PageOutcome> ReadPagesAsync(
        NodeId node,
        ExtensionObject details,
        int pageSize,
        Func<ExtensionObject, Task<int>> decode,
        CancellationToken ct)
    {
        var clock = Stopwatch.StartNew();
        byte[]? continuationPoint = null;
        var rows = 0;
        var pages = 0;
        var verdict = PageVerdict.Complete;
        Exception? failure = null;

        try
        {
            do
            {
                var result = await _read(node, details, continuationPoint, false, ct)
                    .ConfigureAwait(false);
                ThrowIfBad(result.StatusCode, node);

                var pageRows = await decode(result.HistoryData).ConfigureAwait(false);
                CheckCeiling(pageRows, node);
                rows += pageRows;
                continuationPoint = result.ContinuationPoint;
                pages++;

                // Per page, not once at the end: a page that carries a continuation point is
                // complete because more is coming, and the verdict that counts is the last
                // one — which is the page the loop below stops on.
                verdict = ClassifyPage(
                    pageRows, pageSize, continuationPoint is { Length: > 0 });
            }
            while (continuationPoint is { Length: > 0 } && !ct.IsCancellationRequested);
        }
        catch (Exception e)
        {
            // Held, not handled. Nothing here can recover from it; it is caught only so the
            // release below cannot take its place on the way out, and it is rethrown with its
            // stack intact. An OperationCanceledException among them: that one is the shutdown
            // signal, and a release that fails because the session is already gone must not be
            // what the caller sees instead of it.
            failure = e;
        }

        // The loop's other way out. It stops on the cancellation flag between pages rather
        // than on a thrown exception, so nothing was raised, `failure` stayed null, the filter
        // below did not match, and a release that threw took the shutdown path's place after
        // all — the same defect one exit path over. Cancellation is a failure of this read;
        // it is recorded as one here so there is exactly one way out of this method.
        if (failure is null && ct.IsCancellationRequested)
        {
            failure = new OperationCanceledException(ct);
        }

        if (continuationPoint is { Length: > 0 })
        {
            // Released on every exit path, cancellation included, or the server's
            // continuation-point pool leaks until it refuses further reads.
            try
            {
                await ReleaseAsync(node, details, continuationPoint).ConfigureAwait(false);
            }
            catch (Exception releaseFailure) when (failure is not null)
            {
                // The read already failed, so the session this point lives on is the likeliest
                // reason this did too. Logged rather than thrown, and never swallowed on the
                // path where the read succeeded — there, a leaked point is the only symptom
                // there will be until the pool refuses.
                LogReleaseFailed(_logger, node.ToString(), releaseFailure);
            }
        }

        if (failure is not null)
        {
            ExceptionDispatchInfo.Capture(failure).Throw();
        }

        return new PageOutcome(rows, pages, clock.ElapsedMilliseconds, verdict);
    }

    /// <summary>
    /// The guard F1 exists for, at the level F1 measured it: one read. A call returning the
    /// ceiling cannot be distinguished from one the server cut off there, and
    /// <see cref="ClassifyPage"/> cannot see it because the client asked for more than the
    /// ceiling. The fix is the stream's page_size in the signal policy, which is the number
    /// that decides how much one call asks for.
    /// </summary>
    private static void CheckCeiling(int pageRows, NodeId node)
    {
        if (pageRows >= SilentTruncationCeiling)
        {
            throw new InvalidOperationException(
                $"one HistoryRead on {node} returned {pageRows} values, at or above the "
                + $"{SilentTruncationCeiling} ceiling where truncation is silent; lower that "
                + "stream's page_size in the signal policy");
        }
    }

    [LoggerMessage(
        Level = LogLevel.Error,
        Message = "the history read on {Node} failed and its continuation point could not be "
            + "released; the server holds it until its pool recycles")]
    private static partial void LogReleaseFailed(ILogger logger, string node, Exception failure);

    [LoggerMessage(
        Level = LogLevel.Warning,
        Message = "the signal policy skips {Count} discovered stream(s), whose history will "
            + "not be backfilled and which get no ledger row: {Streams}")]
    private static partial void LogSkippedStreams(ILogger logger, int count, string streams);

    private static void ThrowIfBad(StatusCode status, NodeId node)
    {
        if (StatusCode.IsBad(status))
        {
            throw new ServiceResultException(status.Code, $"HistoryRead on {node} returned {status}");
        }
    }

    private async Task ReleaseAsync(
        NodeId node, ExtensionObject details, byte[] continuationPoint)
    {
        // The window's own details, not a blank ReadRawModifiedDetails: a point handed out by
        // an event read is released against ReadEventDetails, and M1's blank object described
        // the wrong read for the one stream whose pages are small enough to have several.
        // CancellationToken.None, because the release is what a cancelled read owes the server.
        await _read(node, details, continuationPoint, release: true, CancellationToken.None)
            .ConfigureAwait(false);
    }

    /// <summary>One page-reading pass over one window of one stream.</summary>
    private sealed record PageOutcome(int Rows, int Pages, long DurationMs, PageVerdict Verdict);

    /// <summary>
    /// One stream as the backfill reads it: the name the ledger keys on, and how to read a
    /// window of it. The name is <c>&lt;station or buffer code&gt;.&lt;signal&gt;</c> — the
    /// code, never the browse name, because §5.2's tables are keyed on the code and two
    /// identifiers for one station means two rows for one station.
    /// </summary>
    private sealed record BackfillStream(
        string Name, Func<DateTime, DateTime, CancellationToken, Task<PageOutcome>> Read);
}
