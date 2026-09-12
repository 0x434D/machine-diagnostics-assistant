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

            windows.Add(await ReadVariableWindowAsync(
                _space.TaktNodeId, "TaktTime", start, end, ct).ConfigureAwait(false));
            windows.Add(await ReadVariableWindowAsync(
                _space.PartCountNodeId, "PartCount", start, end, ct).ConfigureAwait(false));
            windows.Add(await ReadEventWindowAsync(start, end, ct).ConfigureAwait(false));

            Progress = total <= 0 ? 1.0 : (start - from).TotalSeconds / total;
        }

        Progress = 1.0;
        return new BackfillReport(windows);
    }

    private async Task<WindowReport> ReadVariableWindowAsync(
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

        var clock = Stopwatch.StartNew();
        byte[]? continuationPoint = null;
        var returned = 0;
        var pages = 0;

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

                var data = (HistoryData)ExtensionObject.ToEncodeable(result.HistoryData);
                foreach (var value in data.DataValues)
                {
                    await _onRecord(Subscriptions.ToDataChangeRecord(signal, node.ToString(), value))
                        .ConfigureAwait(false);
                    returned++;
                }

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
        return Checked(new WindowReport(from, to, signal, returned, pages, clock.ElapsedMilliseconds));
    }

    /// <summary>
    /// Reads one event window, subdividing it when the result cannot be trusted.
    ///
    /// asyncua returns a full page of event history with no continuation point, so a naive
    /// loop exits believing the window is complete. Measured against the live plant: every
    /// one-hour window returned exactly HistoryEventPageSize rows where ~600 existed, losing
    /// 96% of the event history and reporting success. A full page with nowhere to continue
    /// is indistinguishable from a truncated one, so it is treated as truncated and the
    /// window is halved until every part comes back short of its page.
    /// </summary>
    private async Task<WindowReport> ReadEventWindowAsync(
        DateTime from, DateTime to, CancellationToken ct)
    {
        var window = await ReadEventPageAsync(from, to, ct).ConfigureAwait(false);
        if (!IsSuspiciouslyFull(window))
        {
            return window;
        }

        var span = to - from;
        if (span <= _options.MinimumBackfillWindow)
        {
            throw new InvalidOperationException(
                $"event window {from:O}..{to:O} filled its {_options.HistoryEventPageSize}-row "
                + "page with no continuation point and cannot be subdivided further; rows "
                + "would be lost silently");
        }

        var middle = from.Add(span / 2);
        var first = await ReadEventWindowAsync(from, middle, ct).ConfigureAwait(false);
        var second = await ReadEventWindowAsync(middle, to, ct).ConfigureAwait(false);

        return new WindowReport(
            from, to, "InspectionResult",
            first.RowsReturned + second.RowsReturned,
            first.Pages + second.Pages,
            first.DurationMs + second.DurationMs);
    }

    /// <summary>
    /// A page filled to its limit with no continuation point proves nothing about whether
    /// more rows existed. The same shape as the F1 ceiling guard, one level down.
    /// </summary>
    private bool IsSuspiciouslyFull(WindowReport window) =>
        window.RowsReturned >= _options.HistoryEventPageSize;

    private async Task<WindowReport> ReadEventPageAsync(
        DateTime from, DateTime to, CancellationToken ct)
    {
        var details = new ReadEventDetails
        {
            StartTime = from,
            EndTime = to,
            NumValuesPerNode = (uint)_options.HistoryEventPageSize,
            // The identical filter the live subscription uses. A second filter would be a
            // second statement of the field order, and the order is the decoding contract.
            Filter = Subscriptions.BuildInspectionFilter(),
        };

        var clock = Stopwatch.StartNew();
        byte[]? continuationPoint = null;
        var returned = 0;
        var pages = 0;
        var node = _space.S3NodeId;

        try
        {
            do
            {
                var response = await _session.HistoryReadAsync(
                    null,
                    new ExtensionObject(details),
                    TimestampsToReturn.Both,
                    false,
                    [new HistoryReadValueId { NodeId = node, ContinuationPoint = continuationPoint }],
                    ct).ConfigureAwait(false);

                var result = response.Results[0];
                ThrowIfBad(result.StatusCode, node);

                var data = (HistoryEvent)ExtensionObject.ToEncodeable(result.HistoryData);
                foreach (var entry in data.Events)
                {
                    await _onRecord(Subscriptions.ToEventRecord(node.ToString(), entry.EventFields))
                        .ConfigureAwait(false);
                    returned++;
                }

                continuationPoint = result.ContinuationPoint;
                pages++;
            }
            while (continuationPoint is { Length: > 0 } && !ct.IsCancellationRequested);
        }
        finally
        {
            if (continuationPoint is { Length: > 0 })
            {
                await ReleaseAsync(node, continuationPoint).ConfigureAwait(false);
            }
        }

        ct.ThrowIfCancellationRequested();
        return Checked(new WindowReport(from, to, "InspectionResult", returned, pages, clock.ElapsedMilliseconds));
    }

    /// <summary>
    /// The guard F1 exists for. A window returning the ceiling exactly is indistinguishable
    /// from one that was silently truncated, so it is refused rather than trusted — the
    /// failure this whole design is shaped around is a confident short count, not slowness.
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
            throw new ServiceResultException(
                status.Code, $"HistoryRead on {node} returned {status}");
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
