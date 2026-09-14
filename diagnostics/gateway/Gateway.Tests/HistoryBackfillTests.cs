using System.Text.Json;
using Gateway.Ingest;
using Gateway.Opc;
using Opc.Ua;

namespace Gateway.Tests;

/// <summary>
/// The three defects M1 measured are all "the server answered, the answer was short, and
/// nothing said so". These drive the real reader against a server that behaves the way
/// asyncua was measured to behave — full page, no continuation point, no bad status — which
/// is the only way to tell a guard that runs from a guard that is merely written down.
/// </summary>
public sealed class HistoryBackfillTests
{
    private static readonly DateTime Ts = new(2026, 9, 13, 0, 0, 0, DateTimeKind.Utc);

    private const ushort PlantNamespace = 2;

    [Fact]
    public void AFullFinalPageWithNoContinuationPointIsTreatedAsTruncated()
    {
        // M1's defect, restated at the new width. A client paging smaller than the server's
        // cap receives one page and no continuation point, and stops early believing the
        // window complete -- 96% of event history lost on a run that reported success.
        Assert.Equal(
            PageVerdict.Truncated,
            HistoryBackfill.ClassifyPage(rowsReturned: 1000, pageSize: 1000, hasContinuation: false));
    }

    [Fact]
    public void AShortFinalPageWithNoContinuationPointIsComplete()
    {
        Assert.Equal(
            PageVerdict.Complete,
            HistoryBackfill.ClassifyPage(rowsReturned: 431, pageSize: 1000, hasContinuation: false));
    }

    [Fact]
    public void AFullPageThatOffersMoreIsNotTruncated()
    {
        // Paging that works must not look like the failure: a server that says "there is more"
        // is answering the question the missing continuation point leaves open.
        Assert.Equal(
            PageVerdict.Complete,
            HistoryBackfill.ClassifyPage(rowsReturned: 1000, pageSize: 1000, hasContinuation: true));
    }

    [Fact]
    public void AWindowThatKeepsComingBackFullIsSubdividedUntilItDoesNot()
    {
        // Halving, not retrying: a retry at the same width returns the same truncated page
        // forever.
        var windows = HistoryBackfill.Subdivide(Ts, Ts.AddHours(1), TimeSpan.FromSeconds(30));

        Assert.Equal(2, windows.Count);
        Assert.Equal(TimeSpan.FromMinutes(30), windows[0].To - windows[0].From);
        Assert.Equal(windows[0].To, windows[1].From);
        Assert.Equal(Ts.AddHours(1), windows[1].To);
    }

    [Fact]
    public void SubdividingPastTheFloorFailsRatherThanLosingRows()
    {
        // MinimumBackfillWindow is 30 s, ~5 parts at a 6 s takt. Reaching it means something
        // other than volume is wrong, and the run must fail rather than quietly store a short
        // window.
        var exception = Assert.Throws<InvalidOperationException>(() =>
            HistoryBackfill.Subdivide(Ts, Ts.AddSeconds(30), TimeSpan.FromSeconds(30)));

        Assert.Contains("floor", exception.Message, StringComparison.Ordinal);
    }

    [Fact]
    public async Task NoVariableRowIsLostToAServerThatNeverOffersAContinuationPoint()
    {
        // The guard lived in the event path, and read_node_history has the identical shape:
        // variables were safe only while their page size happened to equal the server's cap.
        // Every stream below returns more rows than its page holds, and none of them is ever
        // told there is more.
        var plant = new FakeHistorian();
        var taktTime = plant.Variable("S3.TaktTime", Minutes(0, 6, 12, 18, 24, 30, 36, 42, 48, 54));
        var state = plant.Variable("S3.State", Minutes(0, 30));
        var level = plant.Variable("B2_3.Level", Minutes(0, 10, 20, 30, 40, 50));
        var inspection = plant.Events("S3", Minutes(0, 12, 24, 36, 48));

        var written = new List<IngestRecord>();
        var report = await RunAsync(plant, written, new AddressSpace(
            Stations:
            [
                new DiscoveredStation(
                    "S3", "Inspection", inspection,
                    [
                        new DiscoveredSignal("TaktTime", taktTime, BuiltInType.Double),
                        new DiscoveredSignal("State", state, BuiltInType.String),
                    ],
                    EmitsEvents: true),
            ],
            Buffers:
            [
                new DiscoveredBuffer("B2_3", level, BuiltInType.UInt32, "S2", "S3", Capacity: 5),
            ],
            PhaseNodeId: null));

        Assert.Equal(plant.SourceTimestamps(taktTime), DeliveredFor(written, taktTime));
        Assert.Equal(plant.SourceTimestamps(state), DeliveredFor(written, state));
        Assert.Equal(plant.SourceTimestamps(level), DeliveredFor(written, level));
        Assert.Equal(plant.SourceTimestamps(inspection), DeliveredFor(written, inspection));

        // The ledger's claim has to agree with what was delivered, or the reconciliation it
        // feeds compares one lie against another.
        Assert.Equal(10, RowsFor(report, "S3.TaktTime"));
        Assert.Equal(2, RowsFor(report, "S3.State"));
        Assert.Equal(6, RowsFor(report, "B2_3.Level"));
        Assert.Equal(5, RowsFor(report, "S3.InspectionResult"));
    }

    [Fact]
    public async Task EveryDiscoveredStreamGetsItsOwnLedgerRowsNamedByStationCode()
    {
        // R1's ledger is per stream, and backfill_windows is UNIQUE (from_ts, to_ts, stream):
        // four stations writing a bare "TaktTime" overwrite each other, and the reconciliation
        // then reports one station's numbers for all four. Station CODE, not browse name --
        // two identifiers for one station means two rows for one station.
        var plant = new FakeHistorian();
        var stations = new List<DiscoveredStation>();
        foreach (var code in new[] { "S1", "S2", "S3" })
        {
            stations.Add(new DiscoveredStation(
                code, code + "_Something", plant.Events(code, Minutes(1)),
                [
                    new DiscoveredSignal("TaktTime", plant.Variable($"{code}.TaktTime", Minutes(1)),
                        BuiltInType.Double),
                ],
                EmitsEvents: code == "S3"));
        }

        var written = new List<IngestRecord>();
        var report = await RunAsync(plant, written, new AddressSpace(
            stations,
            [new DiscoveredBuffer("B1_2", plant.Variable("B1_2.Level", Minutes(1)),
                BuiltInType.UInt32, "S1", "S2", Capacity: 5)],
            PhaseNodeId: null));

        Assert.Equal(
            ["B1_2.Level", "S1.TaktTime", "S2.TaktTime", "S3.InspectionResult", "S3.TaktTime"],
            report.Windows.Select(window => window.Stream).Distinct().Order(StringComparer.Ordinal));
    }

    [Fact]
    public async Task AVariableStreamIsPagedAtItsPolicySizeAndTheEventStreamAtItsOwn()
    {
        // One mechanism for a variable's page size -- the mounted policy -- and one exception,
        // the event stream, whose page is forced by image bytes against the response limit
        // rather than by anything the signal is.
        var plant = new FakeHistorian();
        var takt = plant.Variable("S3.TaktTime", Minutes(1));
        var force = plant.Variable("S3.JoiningForcePeak", Minutes(1));
        var events = plant.Events("S3", Minutes(1));

        await RunAsync(
            plant,
            [],
            new AddressSpace(
                [
                    new DiscoveredStation(
                        "S3", "Inspection", events,
                        [
                            new DiscoveredSignal("TaktTime", takt, BuiltInType.Double),
                            new DiscoveredSignal("JoiningForcePeak", force, BuiltInType.Double),
                        ],
                        EmitsEvents: true),
                ],
                [],
                PhaseNodeId: null),
            policy: """{ "defaults": { "page_size": 40 }, "signals": { "TaktTime": { "page_size": 7 } } }""");

        Assert.Equal([7u], plant.PageSizesRequestedFor(takt));
        Assert.Equal([40u], plant.PageSizesRequestedFor(force));
        Assert.Equal([(uint)EventPageSize], plant.PageSizesRequestedFor(events));
    }

    [Fact]
    public async Task AStreamThatIsAlwaysFullIsFailedRatherThanStoredShort()
    {
        // Subdividing forever is the other way to lose rows quietly. A stream whose every
        // window comes back full is not a volume problem, and the run stops.
        var plant = new FakeHistorian { AlwaysFull = true };
        var events = plant.Events("S3", Minutes(1));

        var exception = await Assert.ThrowsAsync<InvalidOperationException>(() => RunAsync(
            plant,
            [],
            new AddressSpace(
                [
                    new DiscoveredStation(
                        "S3", "Inspection", events,
                        [new DiscoveredSignal("TaktTime", plant.Variable("S3.TaktTime", Minutes(1)),
                            BuiltInType.Double)],
                        EmitsEvents: true),
                ],
                [],
                PhaseNodeId: null)));

        Assert.Contains("floor", exception.Message, StringComparison.Ordinal);
    }

    [Fact]
    public async Task APlantThatPublishesNoEventsIsRefusedRatherThanBackfilledWithoutThem()
    {
        // The loudness a `Where` over a discovered list loses: an event stream this gateway
        // cannot find would otherwise produce a clean backfill and a ledger covering
        // everything except §3.4's history.
        var plant = new FakeHistorian();

        await Assert.ThrowsAsync<InvalidOperationException>(() => RunAsync(
            plant,
            [],
            new AddressSpace(
                [
                    new DiscoveredStation(
                        "S1", "Feeding", new NodeId("S1", PlantNamespace),
                        [new DiscoveredSignal("TaktTime", plant.Variable("S1.TaktTime", Minutes(1)),
                            BuiltInType.Double)],
                        EmitsEvents: false),
                ],
                [],
                PhaseNodeId: null)));
    }

    private const int EventPageSize = 3;

    private static DateTime[] Minutes(params int[] offsets) =>
        [.. offsets.Select(offset => Ts.AddMinutes(offset))];

    private static Task<BackfillReport> RunAsync(
        FakeHistorian plant, List<IngestRecord> written, AddressSpace space,
        string policy = """{ "defaults": { "page_size": 4 } }""")
    {
        var options = GatewayOptions.Default() with
        {
            BackfillWindow = TimeSpan.FromHours(1),
            MinimumBackfillWindow = TimeSpan.FromMinutes(1),
            HistoryEventPageSize = EventPageSize,
        };

        var backfill = new HistoryBackfill(
            plant.Read,
            space,
            SignalPolicy.Parse(policy),
            record =>
            {
                written.Add(record);
                return Task.CompletedTask;
            },
            options);

        return backfill.RunAsync(Ts, Ts.AddHours(1), CancellationToken.None);
    }

    /// <summary>Distinct SourceTimestamps that reached the writer for one node.</summary>
    private static IEnumerable<DateTime> DeliveredFor(
        IReadOnlyList<IngestRecord> written, NodeId node) =>
        written
            .Where(record => record.NodeId == node.ToString())
            .Select(record => record.SourceTs)
            .Distinct()
            .Order();

    private static int RowsFor(BackfillReport report, string stream) =>
        report.Windows.Where(w => w.Stream == stream).Sum(w => w.RowsReturned);

    /// <summary>
    /// A historian that behaves the way asyncua was measured to: it caps each answer at the
    /// page size the client asked for, and never offers a continuation point, because it only
    /// emits one when a result exceeds its own cap.
    /// </summary>
    private sealed class FakeHistorian
    {
        private readonly Dictionary<NodeId, DateTime[]> _rows = [];
        private readonly Dictionary<NodeId, List<uint>> _pageSizes = [];
        private readonly HashSet<NodeId> _eventNodes = [];

        /// <summary>Answers every window full, whatever it is asked for.</summary>
        public bool AlwaysFull { get; init; }

        public NodeId Variable(string name, DateTime[] sourceTimestamps)
        {
            var node = new NodeId(name, PlantNamespace);
            _rows[node] = sourceTimestamps;
            return node;
        }

        public NodeId Events(string station, DateTime[] sourceTimestamps)
        {
            var node = new NodeId(station, PlantNamespace);
            _rows[node] = sourceTimestamps;
            _eventNodes.Add(node);
            return node;
        }

        public IEnumerable<DateTime> SourceTimestamps(NodeId node) => _rows[node].Order();

        public IEnumerable<uint> PageSizesRequestedFor(NodeId node) =>
            _pageSizes[node].Distinct();

        public Task<HistoryReadResult> Read(
            NodeId node, ExtensionObject details, byte[]? continuationPoint, bool release,
            CancellationToken ct)
        {
            if (release)
            {
                return Task.FromResult(new HistoryReadResult());
            }

            var (from, to, pageSize) = ExtensionObject.ToEncodeable(details) switch
            {
                ReadRawModifiedDetails raw => (raw.StartTime, raw.EndTime, raw.NumValuesPerNode),
                ReadEventDetails e => (e.StartTime, e.EndTime, e.NumValuesPerNode),
                var other => throw new InvalidOperationException($"unexpected details {other}"),
            };

            if (!_pageSizes.TryGetValue(node, out var seen))
            {
                _pageSizes[node] = seen = [];
            }

            seen.Add(pageSize);

            var inWindow = AlwaysFull
                ? [.. Enumerable.Range(0, (int)pageSize).Select(i => from.AddTicks(i))]
                : _rows[node].Where(ts => ts >= from && ts < to).Order().Take((int)pageSize).ToList();

            return Task.FromResult(new HistoryReadResult
            {
                StatusCode = StatusCodes.Good,
                ContinuationPoint = null,
                HistoryData = _eventNodes.Contains(node) ? EventPage(inWindow) : DataPage(inWindow),
            });
        }

        private static ExtensionObject DataPage(IReadOnlyList<DateTime> sourceTimestamps)
        {
            var values = new DataValueCollection();
            foreach (var ts in sourceTimestamps)
            {
                values.Add(new DataValue
                {
                    Value = 6.02,
                    SourceTimestamp = ts,
                    ServerTimestamp = ts,
                });
            }

            return new ExtensionObject(new HistoryData { DataValues = values });
        }

        private static ExtensionObject EventPage(IReadOnlyList<DateTime> sourceTimestamps)
        {
            var events = new HistoryEventFieldListCollection();
            foreach (var ts in sourceTimestamps)
            {
                // Positional, in Subscriptions.InspectionEventFields' order -- that order is
                // the wire format, and a page decoded against a different one would be a
                // different test than the one the live subscription passes.
                events.Add(new HistoryEventFieldList
                {
                    EventFields =
                    [
                        new Variant(ts),
                        new Variant($"A-{ts:HHmmss}"),
                        new Variant("good"),
                        new Variant(string.Empty),
                        new Variant(0.99),
                        new Variant("m-1"),
                        new Variant(Array.Empty<byte>()),
                    ],
                });
            }

            return new ExtensionObject(new HistoryEvent { Events = events });
        }
    }
}
