using Gateway.Opc;
using Opc.Ua;

namespace Gateway.Tests;

public sealed class TopologyTests
{
    private static readonly string[] Line = ["S1", "S2", "S3", "S4"];
    private static readonly string[] OneStation = ["S1"];
    private static readonly NodeId CapacityNode = new("B2_3.Capacity", 2);
    private static readonly NodeId LotNode = new("S1.Lane1_Lot", 2);
    private static readonly NodeId TaktNode = new("S1.TaktTime", 2);

    [Theory]
    [InlineData("S3_Inspection", "S3", "Inspection")]
    [InlineData("S1_Feeding", "S1", "Feeding")]
    [InlineData("S2_Joining", "S2", "Joining")]
    [InlineData("S4_Outfeed", "S4", "Outfeed")]
    public void BrowseNameSplitsIntoCodeAndName(string browseName, string code, string name)
    {
        // §5.2's stations table keeps the code and the name apart, and §3.1 names the stations
        // S1..S4 with the function beside them. It also has to agree with what the ingest path
        // writes: two codes for one station means two rows for one station.
        Assert.Equal((code, name), TopologyDiscovery.SplitBrowseName(browseName));
    }

    [Fact]
    public void AStationCodeWithNoFunctionIsItsOwnName()
    {
        // A real plant will not always follow this convention, and an underscore-free browse
        // name must not silently produce an empty code.
        Assert.Equal(("Press", "Press"), TopologyDiscovery.SplitBrowseName("Press"));
    }

    [Fact]
    public void TheLineIsOrderedByItsBuffers()
    {
        // §5.2's position_in_line, left null in M1 because it is derivable from the buffers
        // and the buffers arrive now. The station no buffer feeds is first, and each buffer's
        // downstream is one past its upstream -- so the order survives a Stations folder that
        // browses out in any order at all.
        Assert.Equal(Line, TopologyDiscovery.OrderStations(["S3", "S1", "S4", "S2"], Buffers()));
    }

    [Fact]
    public void ALineThatBranchesCannotBeOrderedAndSaysSo()
    {
        // Two buffers leaving one station is a tree, not a line. Picking either branch would
        // write a position_in_line that every propagation query downstream then trusts, with
        // nothing to distinguish it from a real one.
        var exception = Assert.Throws<InvalidOperationException>(() =>
            TopologyDiscovery.OrderStations(
                Line, [Buffer("B1_2", "S1", "S2"), Buffer("B1_3", "S1", "S3")]));
        Assert.Contains("branches", exception.Message, StringComparison.Ordinal);
    }

    [Fact]
    public void ALineInTwoPiecesCannotBeOrderedAndSaysSo()
    {
        // S1->S2 and S3->S4 are two lines, not one. Both halves are internally consistent,
        // which is exactly why this has to be refused rather than resolved by browse order.
        var exception = Assert.Throws<InvalidOperationException>(() =>
            TopologyDiscovery.OrderStations(
                Line, [Buffer("B1_2", "S1", "S2"), Buffer("B3_4", "S3", "S4")]));
        Assert.Contains("first station", exception.Message, StringComparison.Ordinal);
    }

    [Fact]
    public void ABufferNamingAStationTheLineDoesNotHaveIsRefused()
    {
        // Discovery browsed the stations and read the buffer's endpoints from the same
        // server, so this means the two disagree -- and buffers.upstream_station_id is a
        // foreign key that would fail later and less clearly.
        var exception = Assert.Throws<InvalidOperationException>(() =>
            TopologyDiscovery.OrderStations(["S1", "S2"], [Buffer("B2_9", "S2", "S9")]));
        Assert.Contains("S9", exception.Message, StringComparison.Ordinal);
    }

    [Fact]
    public void ARingIsRefusedRatherThanWalkedForever()
    {
        // Every station has an upstream, so there is no first station and nothing to start
        // from. Worth its own test because the natural implementation walks the chain.
        Assert.Throws<InvalidOperationException>(() =>
            TopologyDiscovery.OrderStations(
                ["S1", "S2"], [Buffer("B1_2", "S1", "S2"), Buffer("B2_1", "S2", "S1")]));
    }

    [Fact]
    public void OneStationWithNoBuffersIsStillAOneStationLine()
    {
        Assert.Equal(OneStation, TopologyDiscovery.OrderStations(OneStation, []));
    }

    [Fact]
    public void ACapacityThatDidNotReadIsRefusedRatherThanStoredAsZero()
    {
        // Convert.ToInt32(null) is 0 and buffers.capacity is SMALLINT NOT NULL, so a Bad read
        // used to store a buffer that holds nothing -- which is the number §3.3's propagation
        // is measured against. Browsing the node proves it exists, not that the read answered.
        var exception = Assert.Throws<ServiceResultException>(() =>
            TopologyDiscovery.BufferCapacity(
                new DataValue { StatusCode = StatusCodes.BadNodeIdUnknown }, "B2_3", CapacityNode));

        Assert.Contains("B2_3", exception.Message, StringComparison.Ordinal);
        Assert.Contains("Capacity", exception.Message, StringComparison.Ordinal);
    }

    [Fact]
    public void AGoodReadCarryingNoValueIsRefusedToo()
    {
        // The other half of the same hole: status Good, value null, and the conversion still
        // produces a confident zero.
        Assert.Throws<ServiceResultException>(() =>
            TopologyDiscovery.BufferCapacity(new DataValue(Variant.Null), "B2_3", CapacityNode));
    }

    [Fact]
    public void ACapacityTooLargeForItsColumnIsRefused()
    {
        // The node is UInt32 and the column is SMALLINT. The cast that writes it is what would
        // otherwise turn 70,000 into a negative capacity, just as quietly.
        var exception = Assert.Throws<ServiceResultException>(() =>
            TopologyDiscovery.BufferCapacity(
                new DataValue(new Variant(70_000u)), "B2_3", CapacityNode));

        Assert.Contains("70000", exception.Message, StringComparison.Ordinal);
    }

    [Fact]
    public void ACapacityThatReadIsTheCapacity()
    {
        Assert.Equal(
            5, TopologyDiscovery.BufferCapacity(new DataValue(new Variant(5u)), "B2_3", CapacityNode));
    }

    [Fact]
    public void AVariableThePlantDoesNotHistoriseIsNotAStreamToIngest()
    {
        // D12: three of S1's children restate what an event already carries authoritatively,
        // so the plant keeps no history of them. Discovery took every variable child as a
        // signal, which made the gateway subscribe to 28 streams and backfill 28 against a
        // plant that historises 25 -- three streams with no history to read and no row in the
        // plant's ledger to reconcile against. It shows up nowhere in `make check`: the
        // plant's browse-count test asserts the plant's own tree and stays green whatever
        // this gateway does with it.
        Assert.Null(TopologyDiscovery.HistorisedSignal(
            "Lane1_Lot", LotNode, new DataValue(new Variant(DataTypeIds.String)),
            new DataValue(new Variant(false))));
    }

    [Fact]
    public void AVariableThePlantDoesHistoriseKeepsItsBrowsedDataType()
    {
        var signal = TopologyDiscovery.HistorisedSignal(
            "TaktTime", TaktNode, new DataValue(new Variant(DataTypeIds.Double)),
            new DataValue(new Variant(true)));

        Assert.Equal(new DiscoveredSignal("TaktTime", TaktNode, BuiltInType.Double), signal);
    }

    [Fact]
    public void AServerThatDoesNotAnswerTheAttributeKeepsItsStream()
    {
        // Only an explicit false drops a stream. A server that will not answer has said
        // nothing, and dropping a whole stream on silence is the one outcome §5.1 refuses --
        // the same asymmetry the signal policy's fail-open default has, and the reason a real
        // plant that never heard of Historizing is still ingested from.
        Assert.NotNull(TopologyDiscovery.HistorisedSignal(
            "TaktTime", TaktNode, new DataValue(new Variant(DataTypeIds.Double)),
            new DataValue { StatusCode = StatusCodes.BadAttributeIdInvalid }));

        Assert.NotNull(TopologyDiscovery.HistorisedSignal(
            "TaktTime", TaktNode, new DataValue(new Variant(DataTypeIds.Double)),
            new DataValue(Variant.Null)));
    }

    private static IReadOnlyList<DiscoveredBuffer> Buffers() =>
    [
        Buffer("B1_2", "S1", "S2"),
        Buffer("B2_3", "S2", "S3"),
        Buffer("B3_4", "S3", "S4"),
    ];

    private static DiscoveredBuffer Buffer(string code, string upstream, string downstream) =>
        new(code, new NodeId(code, 2), BuiltInType.UInt32, upstream, downstream, Capacity: 5);
}
