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

    /// <summary>
    /// One page-reading pass. <c>MayBeTruncated</c> is the whole point: the final page came
    /// back full to its limit and the server offered no continuation point, which is
    /// indistinguishable from a window that had more rows and did not say so.
    ///
    /// asyncua's history_sql caps its SQL at the *client's* page size and only emits a
    /// continuation point when the result exceeds the *server's* cap, so whenever the client
    /// pages smaller than that cap the point is structurally always null — for variables as
    /// well as events. Variables here page at exactly asyncua's default cap, which is the only
    /// reason they were not silently truncated too; lowering HistoryPageSize reopens it.
    /// </summary>
    private sealed record PageOutcome(int Rows, int Pages, long DurationMs, bool MayBeTruncated);

    private readonly ISession _session;
    private readonly AddressSpace _space;
    private readonly Func<IngestRecord, Task> _onRecord;
    private readonly GatewayOptions _options;

    public double Progress { get; private set; }

    public HistoryBackfill(
        ISession session, AddressSpace space, Func<IngestRecord, Task> onRecord,
        GatewayOptions options)
    {
        _session = session;
        _space = space;
        _onRecord = onRecord;
        _options = options;
    }

    public async Task<BackfillReport> RunAsync(DateTime from, DateTime to, CancellationToken ct)
    {
        var windows = new List<WindowReport>();
        var total = (to - from).TotalSeconds;

        for (var start = from; start < to; start = start.Add(_options.BackfillWindow))
        {
            var end = start.Add(_options.BackfillWindow);
            if (end > to)
            {
                end = to;
            }

            windows.Add(await ReadWindowAsync(
                "TaktTime", start, end,
                (a, b, token) => ReadVariablePagesAsync(_space.TaktNodeId, "TaktTime", a, b, token),
                ct).ConfigureAwait(false));

            windows.Add(await ReadWindowAsync(
                "PartCount", start, end,
                (a, b, token) => ReadVariablePagesAsync(_space.PartCountNodeId, "PartCount", a, b, token),
                ct).ConfigureAwait(false));

            windows.Add(await ReadWindowAsync(
                "InspectionResult", start, end, ReadEventPagesAsync, ct).ConfigureAwait(false));

            Progress = total <= 0 ? 1.0 : (start - from).TotalSeconds / total;
        }

        Progress = 1.0;
        return new BackfillReport(windows);
    }

    /// <summary>
    /// Reads a window, halving it whenever the result cannot be trusted, until every part comes
    /// back short of its page.
    ///
    /// Measured against the live plant: every one-hour event window returned exactly the page
    /// size — 25 rows where ~600 existed — losing 96% of the event history while the run
    /// reported success. The F1 ceiling guard could not see it, because 25 is nowhere near
    /// 10,000. This is that guard at the level the failure actually happens.
    /// </summary>
    private async Task<WindowReport> ReadWindowAsync(
        string stream,
        DateTime from,
        DateTime to,
        Func<DateTime, DateTime, CancellationToken, Task<PageOutcome>> readPages,
        CancellationToken ct)
    {
        var outcome = await readPages(from, to, ct).ConfigureAwait(false);
        if (!outcome.MayBeTruncated)
        {
            return Checked(
                new WindowReport(from, to, stream, outcome.Rows, outcome.Pages, outcome.DurationMs));
        }

        var span = to - from;
        if (span <= _options.MinimumBackfillWindow)
        {
            throw new InvalidOperationException(
                $"{stream} window {from:O}..{to:O} filled its page with no continuation point "
                + "and cannot be subdivided further; rows would be lost silently");
        }

        var middle = from.Add(span / 2);
        var first = await ReadWindowAsync(stream, from, middle, readPages, ct).ConfigureAwait(false);
        var second = await ReadWindowAsync(stream, middle, to, readPages, ct).ConfigureAwait(false);

        return new WindowReport(
            from, to, stream,
            first.RowsReturned + second.RowsReturned,
            first.Pages + second.Pages,
            first.DurationMs + second.DurationMs);
    }

    private async Task<PageOutcome> ReadVariablePagesAsync(
        NodeId node, string signal, DateTime from, DateTime to, CancellationToken ct)
    {
        var details = new ReadRawModifiedDetails
        {
            IsReadModified = false,
            StartTime = from,
            EndTime = to,
            // Push the limit into the request. Left at 0 the server materialises the whole
            // remaining range per page and slices it in memory.
            NumValuesPerNode = (uint)_options.HistoryPageSize,
            ReturnBounds = false,
        };

        return await ReadPagesAsync(
            node, details, _options.HistoryPageSize,
            async result =>
            {
                var data = (HistoryData)ExtensionObject.ToEncodeable(result);
                foreach (var value in data.DataValues)
                {
                    await _onRecord(Subscriptions.ToDataChangeRecord(signal, node.ToString(), value))
                        .ConfigureAwait(false);
                }

                return data.DataValues.Count;
            },
            ct).ConfigureAwait(false);
    }

    private async Task<PageOutcome> ReadEventPagesAsync(
        DateTime from, DateTime to, CancellationToken ct)
    {
        var node = _space.S3NodeId;
        var details = new ReadEventDetails
        {
            StartTime = from,
            EndTime = to,
            NumValuesPerNode = (uint)_options.HistoryEventPageSize,
            // The identical filter the live subscription uses. A second filter would be a
            // second statement of the field order, and the order is the decoding contract.
            Filter = Subscriptions.BuildInspectionFilter(),
        };

        return await ReadPagesAsync(
            node, details, _options.HistoryEventPageSize,
            async result =>
            {
                var data = (HistoryEvent)ExtensionObject.ToEncodeable(result);
                foreach (var entry in data.Events)
                {
                    await _onRecord(Subscriptions.ToEventRecord(node.ToString(), entry.EventFields))
                        .ConfigureAwait(false);
                }

                return data.Events.Count;
            },
            ct).ConfigureAwait(false);
    }

    private async Task<PageOutcome> ReadPagesAsync(
        NodeId node,
        object details,
        int pageSize,
        Func<ExtensionObject, Task<int>> decode,
        CancellationToken ct)
    {
        var clock = Stopwatch.StartNew();
        byte[]? continuationPoint = null;
        var rows = 0;
        var pages = 0;
        var lastPageRows = 0;

        try
        {
            do
            {
                var response = await _session.HistoryReadAsync(
                    null,
                    new ExtensionObject(details),
                    TimestampsToReturn.Both,   // §4.2 needs both
                    false,
                    [new HistoryReadValueId { NodeId = node, ContinuationPoint = continuationPoint }],
                    ct).ConfigureAwait(false);

                var result = response.Results[0];
                ThrowIfBad(result.StatusCode, node);

                lastPageRows = await decode(result.HistoryData).ConfigureAwait(false);
                rows += lastPageRows;
                continuationPoint = result.ContinuationPoint;
                pages++;
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

        // A window that legitimately spans several pages ends on a short one, so paging that
        // works is not caught here.
        return new PageOutcome(rows, pages, clock.ElapsedMilliseconds, lastPageRows >= pageSize);
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
        await _session.HistoryReadAsync(
            null,
            new ExtensionObject(new ReadRawModifiedDetails()),
            TimestampsToReturn.Both,
            releaseContinuationPoints: true,
            [new HistoryReadValueId { NodeId = node, ContinuationPoint = continuationPoint }],
            CancellationToken.None).ConfigureAwait(false);
    }
}
