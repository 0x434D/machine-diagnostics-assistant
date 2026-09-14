using System.Diagnostics;
using Gateway.Ingest;
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
public sealed class HistoryBackfill
{
    /// <summary>
    /// Pre-flight F1, re-measured on the pinned interpreter (measurements/r1-probe.txt):
    /// read_raw_history returns at most this many values, with no exception and no bad
    /// StatusCode. A window that comes back on this number cannot be distinguished from one
    /// that was truncated, so it is treated as truncated.
    /// </summary>
    public const int SilentTruncationCeiling = 10_000;

    private readonly HistoryReadCall _read;
    private readonly AddressSpace _space;
    private readonly SignalPolicy _policy;
    private readonly Func<IngestRecord, Task> _onRecord;
    private readonly GatewayOptions _options;

    public double Progress { get; private set; }

    public HistoryBackfill(
        HistoryReadCall read, AddressSpace space, SignalPolicy policy,
        Func<IngestRecord, Task> onRecord, GatewayOptions options)
    {
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

    public async Task<BackfillReport> RunAsync(DateTime from, DateTime to, CancellationToken ct)
    {
        var streams = DiscoveredStreams();
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
    /// Every stream the discovered topology publishes, each with the page size §5.1's policy
    /// gives it. Nothing here names a signal: a literal set would silently cover fewer streams
    /// than the plant has the moment one of them is renamed, and the ledger would then report
    /// success over a stream nobody read. What discovery found is what gets a ledger row, so a
    /// stream that stopped being published is a missing row rather than a missing count.
    /// </summary>
    private List<BackfillStream> DiscoveredStreams()
    {
        // Anchored on the station that publishes the inspection event rather than on a
        // hardcoded S3, and loud rather than empty: a plant whose event stream this gateway
        // cannot find is one whose §3.4 history would be quietly absent from every answer.
        if (!_space.Stations.Any(station => station.EmitsEvents))
        {
            throw new InvalidOperationException(
                "no discovered station publishes events; there is nothing to backfill from");
        }

        var streams = new List<BackfillStream>();
        foreach (var station in _space.Stations)
        {
            foreach (var signal in station.Signals)
            {
                var key = new StreamKey(StreamOwner.Station, station.Code, signal.Name);
                var pageSize = _policy.For(signal.Name, signal.Type).PageSize;
                streams.Add(new BackfillStream(
                    $"{station.Code}.{signal.Name}",
                    (from, to, ct) =>
                        ReadVariablePagesAsync(signal.NodeId, key, pageSize, from, to, ct)));
            }

            if (station.EmitsEvents)
            {
                streams.Add(new BackfillStream(
                    $"{station.Code}.{Subscriptions.EventStream}",
                    (from, to, ct) => ReadEventPagesAsync(station, from, to, ct)));
            }
        }

        foreach (var buffer in _space.Buffers)
        {
            var signal = Subscriptions.BufferLevelSignal;
            var key = new StreamKey(StreamOwner.Buffer, buffer.Code, signal);
            var pageSize = _policy.For(signal, buffer.LevelType).PageSize;
            streams.Add(new BackfillStream(
                $"{buffer.Code}.{signal}",
                (from, to, ct) =>
                    ReadVariablePagesAsync(buffer.LevelNodeId, key, pageSize, from, to, ct)));
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
            into.Add(Checked(new WindowReport(
                from, to, stream.Name, outcome.Rows, outcome.Pages, outcome.DurationMs)));
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

    private async Task<PageOutcome> ReadEventPagesAsync(
        DiscoveredStation station, DateTime from, DateTime to, CancellationToken ct)
    {
        var node = station.NodeId;
        var details = new ExtensionObject(new ReadEventDetails
        {
            StartTime = from,
            EndTime = to,
            // The one page size the policy does not set. It is forced by image bytes against
            // the 4 MiB response limit rather than by anything the stream itself is: R4
            // measured a reject image at up to 110,486 B, so 25 all-reject events is ~2.7 MB.
            NumValuesPerNode = (uint)_options.HistoryEventPageSize,
            // The identical filter the live subscription uses. A second filter would be a
            // second statement of the field order, and the order is the decoding contract.
            Filter = Subscriptions.BuildInspectionFilter(),
        });

        return await ReadPagesAsync(
            node, details, _options.HistoryEventPageSize,
            async result =>
            {
                var data = (HistoryEvent)ExtensionObject.ToEncodeable(result);
                foreach (var entry in data.Events)
                {
                    await _onRecord(Subscriptions.ToEventRecord(
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

        try
        {
            do
            {
                var result = await _read(node, details, continuationPoint, false, ct)
                    .ConfigureAwait(false);
                ThrowIfBad(result.StatusCode, node);

                var pageRows = await decode(result.HistoryData).ConfigureAwait(false);
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
        finally
        {
            // Released on every exit path, cancellation included, or the server's
            // continuation-point pool leaks until it refuses further reads.
            if (continuationPoint is { Length: > 0 })
            {
                await ReleaseAsync(node, continuationPoint).ConfigureAwait(false);
            }
        }

        ct.ThrowIfCancellationRequested();

        return new PageOutcome(rows, pages, clock.ElapsedMilliseconds, verdict);
    }

    /// <summary>
    /// The guard F1 exists for. A window returning the ceiling exactly is indistinguishable
    /// from one that was silently truncated, so it is refused rather than trusted — the
    /// failure this design is shaped around is a confident short count, not slowness.
    /// </summary>
    private static WindowReport Checked(WindowReport window)
    {
        if (window.RowsReturned >= SilentTruncationCeiling)
        {
            throw new InvalidOperationException(
                $"window {window.From:O}..{window.To:O} for {window.Stream} returned "
                + $"{window.RowsReturned} values, at or above the {SilentTruncationCeiling} "
                + "ceiling where truncation is silent; shorten GATEWAY_BACKFILL_WINDOW");
        }

        return window;
    }

    private static void ThrowIfBad(StatusCode status, NodeId node)
    {
        if (StatusCode.IsBad(status))
        {
            throw new ServiceResultException(status.Code, $"HistoryRead on {node} returned {status}");
        }
    }

    private async Task ReleaseAsync(NodeId node, byte[] continuationPoint)
    {
        // An empty details object, as M1 measured working against this server, and
        // CancellationToken.None: the release is what the cancelled read owes the server.
        await _read(
            node,
            new ExtensionObject(new ReadRawModifiedDetails()),
            continuationPoint,
            release: true,
            CancellationToken.None).ConfigureAwait(false);
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
