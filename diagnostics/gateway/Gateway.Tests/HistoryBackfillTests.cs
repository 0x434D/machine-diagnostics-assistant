using Gateway.Ingest;
using Gateway.Opc;
using Microsoft.Extensions.Logging.Abstractions;
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

    [Fact]
    public async Task AStreamThatStoppedBeingPublishedStopsTheRunRatherThanShrinkingIt()
    {
        // The green run over missing data. Discovery alone cannot tell a plant with two
        // streams from a plant that had three and lost one: the backfill reads two, the ledger
        // holds two rows, /reconcile returns two rows all reconciled and verify-no-gaps exits
        // 0. The ledger is the only record of what this plant published before, so it is what
        // the discovered set is held against.
        var plant = new FakeHistorian();
        var space = new AddressSpace(
            [
                new DiscoveredStation(
                    "S3", "Inspection", plant.Events("S3", Minutes(1)),
                    [new DiscoveredSignal("TaktTime", plant.Variable("S3.TaktTime", Minutes(1)),
                        BuiltInType.Double)],
                    EmitsEvents: true),
            ],
            [],
            PhaseNodeId: null);

        var exception = await Assert.ThrowsAsync<InvalidOperationException>(() => RunAsync(
            plant, [], space,
            streamsReadBefore: new HashSet<string>(StringComparer.Ordinal)
            {
                "S3.TaktTime", "S3.InspectionResult", "S3.PartCount",
            }));

        Assert.Contains("S3.PartCount", exception.Message, StringComparison.Ordinal);
        Assert.DoesNotContain("S3.TaktTime", exception.Message, StringComparison.Ordinal);
    }

    [Fact]
    public async Task APolicyCanKeepADiscoveredStreamOutOfStorage()
    {
        // The one key in §5.1 whose whole purpose is to drop data deliberately, asserted
        // where the data would land. While only the live subscription honoured it,
        // `subscribe: false` did not switch a stream off -- it moved it to the other write
        // path: 33 windows read, every row written, and a ledger row standing behind a stream
        // the operator had been told was not stored. A test that asserted `rule.Subscribe` is
        // false passed throughout.
        var plant = new FakeHistorian();
        var level = plant.Variable("B2_3.Level", Minutes(0, 10, 20));
        var takt = plant.Variable("S3.TaktTime", Minutes(0, 10, 20));

        var written = new List<IngestRecord>();
        var report = await RunAsync(
            plant, written,
            new AddressSpace(
                [
                    new DiscoveredStation(
                        "S3", "Inspection", plant.Events("S3", Minutes(1)),
                        [new DiscoveredSignal("TaktTime", takt, BuiltInType.Double)],
                        EmitsEvents: true),
                ],
                [new DiscoveredBuffer("B2_3", level, BuiltInType.UInt32, "S2", "S3", Capacity: 5)],
                PhaseNodeId: null),
            policy: """{ "signals": { "Level": { "subscribe": false } } }""");

        Assert.Empty(DeliveredFor(written, level));
        Assert.DoesNotContain(report.Windows, window => window.Stream == "B2_3.Level");

        // And nothing else went with it: switching one stream off is not switching the run off.
        Assert.Equal(3, RowsFor(report, "S3.TaktTime"));
    }

    [Fact]
    public async Task AStreamTheOperatorSwitchedOffIsNotMistakenForOneThatVanished()
    {
        // The trap the fix above sets. The ledger names every stream this gateway has ever
        // backfilled, so the first boot after `subscribe: false` is added would meet a stream
        // in the ledger that this run does not read -- and the vanished guard, held against the
        // read list, would refuse to start a gateway that is doing exactly what it was told.
        // The guard is about what the plant publishes; the policy is about what is stored.
        var plant = new FakeHistorian();
        var level = plant.Variable("B2_3.Level", Minutes(0, 10, 20));

        var report = await RunAsync(
            plant, [],
            new AddressSpace(
                [
                    new DiscoveredStation(
                        "S3", "Inspection", plant.Events("S3", Minutes(1)),
                        [new DiscoveredSignal(
                            "TaktTime", plant.Variable("S3.TaktTime", Minutes(1)),
                            BuiltInType.Double)],
                        EmitsEvents: true),
                ],
                [new DiscoveredBuffer("B2_3", level, BuiltInType.UInt32, "S2", "S3", Capacity: 5)],
                PhaseNodeId: null),
            policy: """{ "signals": { "Level": { "subscribe": false } } }""",
            streamsReadBefore: new HashSet<string>(StringComparer.Ordinal)
            {
                "S3.TaktTime", "S3.InspectionResult", "B2_3.Level",
            });

        Assert.DoesNotContain(report.Windows, window => window.Stream == "B2_3.Level");
    }

    [Fact]
    public async Task AFirstRunAgainstAnEmptyLedgerAssertsNothingAboutTheStreamCount()
    {
        // The honest limit of the guard above, stated so it is not mistaken for coverage it
        // does not have: with no ledger there is nothing to have lost a stream against.
        var plant = new FakeHistorian();
        var report = await RunAsync(plant, [], new AddressSpace(
            [
                new DiscoveredStation(
                    "S3", "Inspection", plant.Events("S3", Minutes(1)),
                    [new DiscoveredSignal("TaktTime", plant.Variable("S3.TaktTime", Minutes(1)),
                        BuiltInType.Double)],
                    EmitsEvents: true),
            ],
            [],
            PhaseNodeId: null));

        Assert.Equal(2, report.Windows.Select(w => w.Stream).Distinct().Count());
    }

    [Fact]
    public async Task OneReadAtTheSilentTruncationCeilingIsRefused()
    {
        // F1's ceiling is a property of one read, not of a window: a client asking for more
        // than 10,000 gets 10,000 with no exception and no bad status, and ClassifyPage cannot
        // see it because the page is not full to what was asked for.
        var plant = new FakeHistorian { AlwaysFull = true };
        var exception = await Assert.ThrowsAsync<InvalidOperationException>(() => RunAsync(
            plant,
            [],
            new AddressSpace(
                [
                    new DiscoveredStation(
                        "S3", "Inspection", plant.Events("S3", Minutes(1)),
                        [new DiscoveredSignal(
                            "TaktTime", plant.Variable("S3.TaktTime", Minutes(1)),
                            BuiltInType.Double)],
                        EmitsEvents: true),
                ],
                [],
                PhaseNodeId: null),
            policy: """{ "defaults": { "page_size": 20000 } }"""));

        Assert.Contains("page_size", exception.Message, StringComparison.Ordinal);
    }

    [Fact]
    public async Task AWindowOfManyPagesIsNotRefusedForTotallingAboveTheCeiling()
    {
        // The other half of the same rule. F1's ceiling applied to a window total refuses
        // paging that works: eleven thousand rows across four pages, none of them near the
        // ceiling, is a window that was read completely.
        var plant = new FakeHistorian { OffersContinuation = true };
        var takt = plant.Variable("S3.TaktTime", Dense(11_000));

        var written = new List<IngestRecord>();
        var report = await RunAsync(
            plant, written,
            new AddressSpace(
                [
                    new DiscoveredStation(
                        "S3", "Inspection", plant.Events("S3", Minutes(1)),
                        [new DiscoveredSignal("TaktTime", takt, BuiltInType.Double)],
                        EmitsEvents: true),
                ],
                [],
                PhaseNodeId: null),
            policy: """{ "defaults": { "page_size": 3000 } }""");

        Assert.Equal(11_000, RowsFor(report, "S3.TaktTime"));
        Assert.Equal(1, report.Windows.Count(w => w.Stream == "S3.TaktTime"));
        Assert.Equal(4, report.Windows.Single(w => w.Stream == "S3.TaktTime").Pages);
    }

    [Fact]
    public async Task AFailedReleaseDoesNotReplaceTheFailureThatCausedIt()
    {
        // The continuation point is released in every exit path, so a release that throws
        // because the session is already gone used to become the exception the caller saw --
        // including when the original was an OperationCanceledException, which is the shutdown
        // signal CLAUDE.md says must survive.
        var plant = new FakeHistorian { OffersContinuation = true, FailAfterFirstPage = true };

        await Assert.ThrowsAsync<OperationCanceledException>(() => RunAsync(
            plant,
            [],
            new AddressSpace(
                [
                    new DiscoveredStation(
                        "S3", "Inspection", plant.Events("S3", Minutes(1)),
                        [new DiscoveredSignal(
                            // More rows than the page, so the first read hands back a
                            // continuation point and the second -- the one that dies -- is
                            // holding it.
                            "TaktTime",
                            plant.Variable("S3.TaktTime", Minutes(0, 10, 20, 30, 40, 50)),
                            BuiltInType.Double)],
                        EmitsEvents: true),
                ],
                [],
                PhaseNodeId: null)));

        Assert.True(plant.ReleaseAttempted, "the continuation point was never released");
    }

    [Fact]
    public async Task AFailedReleaseDoesNotReplaceACancellationTheLoopExitedOnCooperatively()
    {
        // The pager's other way out: it stops between pages on the cancellation flag, so
        // nothing is thrown and the "a release may only be swallowed when something already
        // failed" rule had nothing to match on -- the release's own exception became what the
        // caller saw, and the shutdown signal was gone.
        using var cancellation = new CancellationTokenSource();
        var plant = new FakeHistorian
        {
            OffersContinuation = true,
            FailRelease = true,
            CancelAfterFirstPage = cancellation,
        };

        await Assert.ThrowsAsync<OperationCanceledException>(() => RunAsync(
            plant,
            [],
            new AddressSpace(
                [
                    new DiscoveredStation(
                        "S3", "Inspection", plant.Events("S3", Minutes(1)),
                        [new DiscoveredSignal(
                            "TaktTime",
                            plant.Variable("S3.TaktTime", Minutes(0, 10, 20, 30, 40, 50)),
                            BuiltInType.Double)],
                        EmitsEvents: true),
                ],
                [],
                PhaseNodeId: null),
            ct: cancellation.Token));

        Assert.True(plant.ReleaseAttempted, "the continuation point was never released");
    }

    private const int EventPageSize = 3;

    private static DateTime[] Minutes(params int[] offsets) =>
        [.. offsets.Select(offset => Ts.AddMinutes(offset))];

    /// <summary>`count` timestamps spread evenly inside the one-hour window under test.</summary>
    private static DateTime[] Dense(int count) =>
        [.. Enumerable.Range(0, count).Select(i => Ts.AddMilliseconds(i * (3_600_000 / count)))];

    private static Task<BackfillReport> RunAsync(
        FakeHistorian plant, List<IngestRecord> written, AddressSpace space,
        string policy = """{ "defaults": { "page_size": 4 } }""",
        IReadOnlySet<string>? streamsReadBefore = null,
        CancellationToken ct = default)
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
            options,
            NullLogger<HistoryBackfill>.Instance);

        return backfill.RunAsync(
            Ts,
            Ts.AddHours(1),
            streamsReadBefore ?? new HashSet<string>(StringComparer.Ordinal),
            ct);
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
        private static readonly string[] DefectClasses = ["gap", "crack"];
        private static readonly double[] Confidences = [0.02, 0.01];

        private readonly Dictionary<NodeId, DateTime[]> _rows = [];
        private readonly Dictionary<NodeId, List<uint>> _pageSizes = [];
        private readonly HashSet<NodeId> _eventNodes = [];
        private int _reads;

        /// <summary>Answers every window full, whatever it is asked for.</summary>
        public bool AlwaysFull { get; init; }

        /// <summary>
        /// Hands back a continuation point whenever rows remain -- the behaviour asyncua shows
        /// only when the client asks for more than the server's own cap.
        /// </summary>
        public bool OffersContinuation { get; init; }

        /// <summary>The session dies mid-window, and the release that follows dies with it.</summary>
        public bool FailAfterFirstPage { get; init; }

        /// <summary>Releasing throws, whatever ended the read.</summary>
        public bool FailRelease { get; init; }

        /// <summary>Cancelled after the first page, so the pager stops on the flag rather than
        /// on anything thrown -- the loop's other way out.</summary>
        public CancellationTokenSource? CancelAfterFirstPage { get; init; }

        public bool ReleaseAttempted { get; private set; }

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
                ReleaseAttempted = true;
                return FailAfterFirstPage || FailRelease
                    ? throw new ServiceResultException(
                        StatusCodes.BadSessionIdInvalid, "the session is gone")
                    : Task.FromResult(new HistoryReadResult());
            }

            if (FailAfterFirstPage && ++_reads > 1)
            {
                throw new OperationCanceledException("the gateway is shutting down");
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

            if (AlwaysFull)
            {
                var full = Enumerable.Range(0, (int)pageSize)
                    .Select(i => from.AddTicks(i)).ToList();
                return Task.FromResult(Page(node, full, null));
            }

            var all = _rows[node].Where(ts => ts >= from && ts < to).Order().ToList();
            var offset = continuationPoint is null ? 0 : BitConverter.ToInt32(continuationPoint);
            var inWindow = all.Skip(offset).Take((int)pageSize).ToList();
            var next = offset + inWindow.Count;

            var page = Page(
                node,
                inWindow,
                OffersContinuation && next < all.Count ? BitConverter.GetBytes(next) : null);

            // After the page is built, so the pager decodes it and then finds the flag set on
            // the loop condition rather than on the read itself.
            CancelAfterFirstPage?.Cancel();
            return Task.FromResult(page);
        }

        private HistoryReadResult Page(
            NodeId node, IReadOnlyList<DateTime> sourceTimestamps, byte[]? continuationPoint) =>
            new()
            {
                StatusCode = StatusCodes.Good,
                ContinuationPoint = continuationPoint,
                HistoryData = _eventNodes.Contains(node)
                    ? EventPage(sourceTimestamps)
                    : DataPage(sourceTimestamps),
            };

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
                // Positional, in the select-clause order the stream asked for -- that order
                // is the wire format, and a page decoded against a different one would be a
                // different test than the one the live subscription passes.
                events.Add(new HistoryEventFieldList
                {
                    EventFields =
                    [
                        new Variant(ts),
                        new Variant(new NodeId("InspectionResultEventType", PlantNamespace)),
                        new Variant($"A-{ts:HHmmss}"),
                        new Variant(1u),
                        new Variant("good"),
                        new Variant(DefectClasses),
                        new Variant(Confidences),
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
