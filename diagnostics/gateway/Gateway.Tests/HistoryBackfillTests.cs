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

    /// <summary>
    /// What a station's GeneratesEvent references browse out to. The fake plant below emits
    /// §3.4's verdict, which is the type whose images force the small page.
    /// </summary>
    private static readonly IReadOnlyList<DiscoveredEventType> Inspection =
    [
        new("InspectionResultEventType", new NodeId("InspectionResultEventType", PlantNamespace)),
    ];

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
                    EmitsEvents: true, EventTypes: Inspection),
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
        Assert.Equal(5, RowsFor(report, "S3.Events"));
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
                EmitsEvents: code == "S3", EventTypes: code == "S3" ? Inspection : []));
        }

        var written = new List<IngestRecord>();
        var report = await RunAsync(plant, written, new AddressSpace(
            stations,
            [new DiscoveredBuffer("B1_2", plant.Variable("B1_2.Level", Minutes(1)),
                BuiltInType.UInt32, "S1", "S2", Capacity: 5)],
            PhaseNodeId: null));

        Assert.Equal(
            ["B1_2.Level", "S1.TaktTime", "S2.TaktTime", "S3.Events", "S3.TaktTime"],
            report.Windows.Select(window => window.Stream).Distinct().Order(StringComparer.Ordinal));
    }

    [Fact]
    public async Task AnEventStreamCarryingTwoTypesIsReadAsBoth()
    {
        // S1 emits two types and asyncua historises events per emitting node, so both share
        // one table and one read. The filter has to cover the union, and the decoder has to
        // put each row back under the type it actually is -- routed on which columns are
        // non-null, an AssemblyCreatedEvent and a ComponentReadEvent would be told apart by
        // resemblance, and the genealogy is what pays for a wrong guess.
        var plant = new FakeHistorian();
        var feeding = plant.Events(
            "S1", Minutes(0, 0, 6, 6),
            "ComponentReadEventType", "AssemblyCreatedEventType");

        var written = new List<IngestRecord>();
        await RunAsync(
            plant, written,
            new AddressSpace(
                [
                    new DiscoveredStation(
                        "S1", "Feeding", feeding, [],
                        EmitsEvents: true,
                        EventTypes:
                        [
                            new DiscoveredEventType(
                                "ComponentReadEventType",
                                new NodeId("ComponentReadEventType", PlantNamespace)),
                            new DiscoveredEventType(
                                "AssemblyCreatedEventType",
                                new NodeId("AssemblyCreatedEventType", PlantNamespace)),
                        ]),
                ],
                [],
                PhaseNodeId: null));

        Assert.Equal(
            2,
            written.Count(record => record.PayloadJson.Contains(
                "\"EventType\":\"ComponentReadEventType\"", StringComparison.Ordinal)));
        Assert.Equal(
            2,
            written.Count(record => record.PayloadJson.Contains(
                "\"EventType\":\"AssemblyCreatedEventType\"", StringComparison.Ordinal)));

        // Both types' own fields came back, which is what a union filter is for: a filter
        // covering only the first type would decode the second into a row of nulls.
        Assert.Contains(
            written,
            record => record.PayloadJson.Contains("\"LotCode\"", StringComparison.Ordinal));
        Assert.Contains(
            written,
            record => record.PayloadJson.Contains("\"ComponentSerials\"", StringComparison.Ordinal));
    }

    [Fact]
    public async Task AnEventStreamThatFillsItsPageWithNoContinuationPointIsSubdividedToo()
    {
        // M1 measured this on the inspection stream and M2a measured it again on the buffer
        // levels -- every one-hour window came back exactly page-size long, with no
        // continuation point and no bad status, and 96% of the history was lost on a run that
        // reported success. The four types M2b adds are read through the same pager at their
        // own page sizes, and "the guard is shared" is a claim about code rather than about
        // behaviour until a new type's stream is driven through it.
        var plant = new FakeHistorian();
        var feeding = plant.Events(
            "S1", Minutes(0, 6, 12, 18, 24, 30, 36, 42),
            "ComponentReadEventType", "AssemblyCreatedEventType");

        var written = new List<IngestRecord>();
        var report = await RunAsync(
            plant, written,
            new AddressSpace(
                [
                    new DiscoveredStation(
                        "S1", "Feeding", feeding, [],
                        EmitsEvents: true,
                        EventTypes:
                        [
                            new DiscoveredEventType(
                                "ComponentReadEventType",
                                new NodeId("ComponentReadEventType", PlantNamespace)),
                            new DiscoveredEventType(
                                "AssemblyCreatedEventType",
                                new NodeId("AssemblyCreatedEventType", PlantNamespace)),
                        ]),
                ],
                [],
                PhaseNodeId: null),
            policy: """
                {
                  "events": {
                    "ComponentReadEventType": { "page_size": 3 },
                    "AssemblyCreatedEventType": { "page_size": 3 }
                  }
                }
                """);

        // Nothing was lost to a window the server answered full and said nothing about.
        Assert.Equal(plant.SourceTimestamps(feeding), DeliveredFor(written, feeding));
        Assert.Equal(8, RowsFor(report, "S1.Events"));

        // And the ledger records the halved windows rather than the one that could not be
        // believed: the duplicate model is one page boundary per page beyond the first, per
        // window, and a row aggregating several real windows accounts for boundaries that are
        // not there.
        Assert.True(
            report.Windows.Count(window => window.Stream == "S1.Events") > 1,
            "the window that came back full was not subdivided");
    }

    [Fact]
    public async Task AStreamIsPagedAtTheSmallestPageAnyTypeOnItIsGiven()
    {
        // One read covers a station's whole event stream, so a station carrying an imaged
        // type beside an unimaged one is bounded by the imaged one's bytes -- R4 measured a
        // reject image at p99 110,419 B, and 25 of them is already 2.77 MB of the 4 MiB
        // response limit. Taking the larger number, or the first type's, is how a read comes
        // back BadEncodingLimitsExceeded on the one stream that carries §3.4's evidence.
        var plant = new FakeHistorian();
        var events = plant.Events(
            "S3", Minutes(1), "InspectionResultEventType", "ComponentReadEventType");

        await RunAsync(
            plant, [],
            new AddressSpace(
                [
                    new DiscoveredStation(
                        "S3", "Inspection", events, [],
                        EmitsEvents: true,
                        EventTypes:
                        [
                            new DiscoveredEventType(
                                "ComponentReadEventType",
                                new NodeId("ComponentReadEventType", PlantNamespace)),
                            new DiscoveredEventType(
                                "InspectionResultEventType",
                                new NodeId("InspectionResultEventType", PlantNamespace)),
                        ]),
                ],
                [],
                PhaseNodeId: null),
            policy: """
                {
                  "events": {
                    "InspectionResultEventType": { "page_size": 25 },
                    "ComponentReadEventType": { "page_size": 4000 }
                  }
                }
                """);

        Assert.Equal([25u], plant.PageSizesRequestedFor(events));
    }

    [Fact]
    public async Task AVariableStreamIsPagedAtItsPolicySizeAndAnEventStreamAtItsTypes()
    {
        // One mechanism for every page size -- the mounted policy -- keyed by signal name for
        // a variable and by event type browse name for a stream of events. A page size
        // compiled into the gateway as well would be a second mechanism for one number, free
        // to disagree with the file an engineer edits.
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
                        EmitsEvents: true, EventTypes: Inspection),
                ],
                [],
                PhaseNodeId: null),
            policy: """
                {
                  "defaults": { "page_size": 40 },
                  "signals": { "TaktTime": { "page_size": 7 } },
                  "events": { "InspectionResultEventType": { "page_size": 25 } }
                }
                """);

        Assert.Equal([7u], plant.PageSizesRequestedFor(takt));
        Assert.Equal([40u], plant.PageSizesRequestedFor(force));
        Assert.Equal([25u], plant.PageSizesRequestedFor(events));
    }

    [Fact]
    public async Task AnEventTypeThePolicyDoesNotNameIsPagedAtAStatedDefault()
    {
        // The event half of §5.1's fail-open rule. A plant that gains a type this file has
        // never heard of is read at a number stated in one place, not at whatever the last
        // stream happened to use -- and not skipped, which is the one outcome §5.1 refuses.
        var plant = new FakeHistorian();
        var events = plant.Events("S3", Minutes(1));

        await RunAsync(
            plant, [],
            new AddressSpace(
                [
                    new DiscoveredStation(
                        "S3", "Inspection", events, [],
                        EmitsEvents: true, EventTypes: Inspection),
                ],
                [],
                PhaseNodeId: null),
            policy: "{}");

        Assert.Equal(
            [(uint)SignalPolicy.DefaultEventPageSize], plant.PageSizesRequestedFor(events));
    }

    [Fact]
    public async Task AStationGeneratingATypeThisGatewayCannotDecodeStopsTheRun()
    {
        // The stream would be read through a filter that matches none of its fields, decode
        // into rows of nulls, and still write a ledger row claiming every event was pulled --
        // a green run over data that never arrived, which is the shape every other guard in
        // this file exists to refuse.
        var plant = new FakeHistorian();

        var exception = await Assert.ThrowsAsync<InvalidOperationException>(() => RunAsync(
            plant, [],
            new AddressSpace(
                [
                    new DiscoveredStation(
                        "S5", "Packing", plant.Events("S5", Minutes(1)), [],
                        EmitsEvents: true,
                        EventTypes:
                        [
                            new DiscoveredEventType(
                                "ToolChangeEventType",
                                new NodeId("ToolChangeEventType", PlantNamespace)),
                        ]),
                ],
                [],
                PhaseNodeId: null)));

        Assert.Contains("ToolChangeEventType", exception.Message, StringComparison.Ordinal);
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
                        EmitsEvents: true, EventTypes: Inspection),
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
                        EmitsEvents: false, EventTypes: []),
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
                    EmitsEvents: true, EventTypes: Inspection),
            ],
            [],
            PhaseNodeId: null);

        var exception = await Assert.ThrowsAsync<InvalidOperationException>(() => RunAsync(
            plant, [], space,
            streamsReadBefore: new HashSet<string>(StringComparer.Ordinal)
            {
                "S3.TaktTime", "S3.Events", "S3.PartCount",
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
                        EmitsEvents: true, EventTypes: Inspection),
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
                        EmitsEvents: true, EventTypes: Inspection),
                ],
                [new DiscoveredBuffer("B2_3", level, BuiltInType.UInt32, "S2", "S3", Capacity: 5)],
                PhaseNodeId: null),
            policy: """{ "signals": { "Level": { "subscribe": false } } }""",
            streamsReadBefore: new HashSet<string>(StringComparer.Ordinal)
            {
                "S3.TaktTime", "S3.Events", "B2_3.Level",
            });

        Assert.DoesNotContain(report.Windows, window => window.Stream == "B2_3.Level");
    }

    [Fact]
    public async Task AVanishedStreamNamesWhatTheSameStationPublishesNow()
    {
        // The ledger below is what a pre-M2b gateway wrote: Subscriptions.EventStream spelled
        // "InspectionResult" then and spells "Events" now, so S3's event stream vanishes on
        // every boot against that database and the guard fires for ever. It is right to fire
        // -- it cannot tell a rename from a plant that went quiet -- but an operator reading
        // "no longer published" goes to look at a plant that is publishing fine. The message
        // has to carry the name that appeared where the old one went, which is the whole
        // difference between the two. S3.TaktTime, published before and published still,
        // distinguishes nothing and is left out — the test above holds it out.
        var plant = new FakeHistorian();
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
                        EmitsEvents: true, EventTypes: Inspection),
                ],
                [],
                PhaseNodeId: null),
            streamsReadBefore: new HashSet<string>(StringComparer.Ordinal)
            {
                "S3.TaktTime", "S3.InspectionResult",
            }));

        Assert.Contains("S3.InspectionResult", exception.Message, StringComparison.Ordinal);
        Assert.Contains("S3.Events", exception.Message, StringComparison.Ordinal);
        Assert.Contains("rename", exception.Message, StringComparison.Ordinal);
        Assert.Contains("backfill_windows.stream", exception.Message, StringComparison.Ordinal);
        Assert.DoesNotContain("S3.TaktTime", exception.Message, StringComparison.Ordinal);
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
                    EmitsEvents: true, EventTypes: Inspection),
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
                        EmitsEvents: true, EventTypes: Inspection),
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
                        EmitsEvents: true, EventTypes: Inspection),
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
                        EmitsEvents: true, EventTypes: Inspection),
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
                        EmitsEvents: true, EventTypes: Inspection),
                ],
                [],
                PhaseNodeId: null),
            ct: cancellation.Token));

        Assert.True(plant.ReleaseAttempted, "the continuation point was never released");
    }

    /// <summary>
    /// The default policy every test here runs under, unless it passes its own. Small on
    /// purpose: the point of these tests is that a full page with no continuation point is
    /// not believed, and a page nothing fills proves nothing.
    /// </summary>
    private const string SmallPages =
        """
        {
          "defaults": { "page_size": 4 },
          "events": { "InspectionResultEventType": { "page_size": 3 } }
        }
        """;

    private static DateTime[] Minutes(params int[] offsets) =>
        [.. offsets.Select(offset => Ts.AddMinutes(offset))];

    /// <summary>`count` timestamps spread evenly inside the one-hour window under test.</summary>
    private static DateTime[] Dense(int count) =>
        [.. Enumerable.Range(0, count).Select(i => Ts.AddMilliseconds(i * (3_600_000 / count)))];

    private static Task<BackfillReport> RunAsync(
        FakeHistorian plant, List<IngestRecord> written, AddressSpace space,
        string policy = SmallPages,
        IReadOnlySet<string>? streamsReadBefore = null,
        CancellationToken ct = default)
    {
        var options = GatewayOptions.Default() with
        {
            BackfillWindow = TimeSpan.FromHours(1),
            MinimumBackfillWindow = TimeSpan.FromMinutes(1),
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

        private static readonly string[] ComponentSerials = ["C-1-000001", "C-2-000001"];

        private readonly Dictionary<NodeId, DateTime[]> _rows = [];
        private readonly Dictionary<NodeId, List<uint>> _pageSizes = [];
        private readonly Dictionary<NodeId, string[]> _eventNodes = [];
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

        /// <param name="typeNames">
        /// Which types this station's stream carries, cycled row by row. A station emitting
        /// two is how the plant really behaves — asyncua historises events per emitting node,
        /// so both types share one table and one read.
        /// </param>
        public NodeId Events(
            string station, DateTime[] sourceTimestamps, params string[] typeNames)
        {
            var node = new NodeId(station, PlantNamespace);
            _rows[node] = sourceTimestamps;
            _eventNodes[node] = typeNames.Length > 0
                ? typeNames
                : ["InspectionResultEventType"];
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

            // The select clauses come off the request, the way a server answers them: asyncua
            // re-aligns a stored event to whatever the client asked for and puts a null
            // Variant where it has nothing. A fake with its own idea of the field order would
            // be testing the fake's order rather than the reader's.
            var (from, to, pageSize, selected) = ExtensionObject.ToEncodeable(details) switch
            {
                ReadRawModifiedDetails raw =>
                    (raw.StartTime, raw.EndTime, raw.NumValuesPerNode, (IReadOnlyList<string>)[]),
                ReadEventDetails e => (
                    e.StartTime, e.EndTime, e.NumValuesPerNode,
                    (IReadOnlyList<string>)
                    [.. e.Filter.SelectClauses.Select(clause => clause.BrowsePath[0].Name)]),
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
                return Task.FromResult(Page(node, full, null, selected));
            }

            var all = _rows[node].Where(ts => ts >= from && ts < to).Order().ToList();
            var offset = continuationPoint is null ? 0 : BitConverter.ToInt32(continuationPoint);
            var inWindow = all.Skip(offset).Take((int)pageSize).ToList();
            var next = offset + inWindow.Count;

            var page = Page(
                node,
                inWindow,
                OffersContinuation && next < all.Count ? BitConverter.GetBytes(next) : null,
                selected);

            // After the page is built, so the pager decodes it and then finds the flag set on
            // the loop condition rather than on the read itself.
            CancelAfterFirstPage?.Cancel();
            return Task.FromResult(page);
        }

        private HistoryReadResult Page(
            NodeId node, IReadOnlyList<DateTime> sourceTimestamps, byte[]? continuationPoint,
            IReadOnlyList<string> selected) =>
            new()
            {
                StatusCode = StatusCodes.Good,
                ContinuationPoint = continuationPoint,
                HistoryData = _eventNodes.TryGetValue(node, out var types)
                    ? EventPage(sourceTimestamps, selected, types)
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

        /// <summary>
        /// One page of events, answered clause by clause in the order the client asked for
        /// them -- which is what the server does, and therefore what the reader has to be
        /// right about. Rows cycle through the station's types, so a two-type stream really
        /// does interleave them the way one shared event table does.
        /// </summary>
        private static ExtensionObject EventPage(
            IReadOnlyList<DateTime> sourceTimestamps, IReadOnlyList<string> selected,
            string[] types)
        {
            var events = new HistoryEventFieldListCollection();
            for (var i = 0; i < sourceTimestamps.Count; i++)
            {
                var typeName = types[i % types.Length];
                var ts = sourceTimestamps[i];
                events.Add(new HistoryEventFieldList
                {
                    EventFields = [.. selected.Select(field => Field(field, typeName, ts))],
                });
            }

            return new ExtensionObject(new HistoryEvent { Events = events });
        }

        /// <summary>
        /// What one event type puts in one field, and a null Variant where it has nothing --
        /// which is exactly what a server returns for a select clause the row's own type does
        /// not have, rather than an error.
        /// </summary>
        private static Variant Field(string field, string typeName, DateTime ts) =>
            (field, typeName) switch
            {
                ("Time", _) => new Variant(ts),
                ("EventType", _) => new Variant(new NodeId(typeName, PlantNamespace)),

                ("ComponentSerial", "ComponentReadEventType") => new Variant($"C-1-{ts:HHmmss}"),
                ("Lane", "ComponentReadEventType") => new Variant(1u),
                ("LotCode", "ComponentReadEventType") => new Variant("L-2305"),
                ("Supplier", "ComponentReadEventType") => new Variant("SUP-01"),

                ("AssemblySerial", "AssemblyCreatedEventType") => new Variant($"A-{ts:HHmmss}"),
                ("ComponentSerials", "AssemblyCreatedEventType") => new Variant(ComponentSerials),
                ("CarrierId", "AssemblyCreatedEventType") => new Variant(7u),

                ("AssemblySerial", "InspectionResultEventType") => new Variant($"A-{ts:HHmmss}"),
                ("CarrierId", "InspectionResultEventType") => new Variant(1u),
                ("Disposition", "InspectionResultEventType") => new Variant("good"),
                ("DefectClasses", "InspectionResultEventType") => new Variant(DefectClasses),
                ("Confidences", "InspectionResultEventType") => new Variant(Confidences),
                ("Confidence", "InspectionResultEventType") => new Variant(0.99),
                ("ModelVersion", "InspectionResultEventType") => new Variant("m-1"),
                ("Image", "InspectionResultEventType") => new Variant(Array.Empty<byte>()),

                _ => Variant.Null,
            };
    }
}
