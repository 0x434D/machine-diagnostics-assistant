using Gateway.Opc;

namespace Gateway.Tests;

public sealed class TopologyTests
{
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
    public void TheIngestPathAndDiscoveryAgreeOnTheStationCode()
    {
        // The one that would actually bite: PostgresWriter keys stations on the code carried
        // in each record's payload, and discovery writes the stations table. If these drift,
        // the line acquires a second station that does not exist.
        Assert.Equal(
            AddressSpace.StationCode,
            TopologyDiscovery.SplitBrowseName("S3_Inspection").Code);
    }
}
