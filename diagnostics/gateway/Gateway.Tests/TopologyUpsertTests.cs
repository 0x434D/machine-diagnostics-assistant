using Gateway.Ingest;
using Gateway.Opc;
using Npgsql;
using Opc.Ua;
using Testcontainers.PostgreSql;

namespace Gateway.Tests;

/// <summary>
/// What discovery writes, against the schema it writes into. §4.1's topology is browsed on
/// every connect, so everything here is about the second run as much as the first.
/// </summary>
public sealed class TopologyUpsertTests : IAsyncLifetime
{
    private const short Capacity = 5;

    // Pinned by digest, not by tag (§10.7). scripts/pin-images.sh re-resolves it.
    private const string PostgresImage =
        "postgres:17-bookworm@sha256:051f7b7b3abdd564d5d1bd1e8c4b9c1b6e77087d1dd22020ede611c096a272e0";

    private readonly PostgreSqlContainer _postgres = new PostgreSqlBuilder(PostgresImage).Build();

    public async Task InitializeAsync()
    {
        await _postgres.StartAsync();
        await PostgresWriter.ApplySchemaAsync(_postgres.GetConnectionString());
    }

    public async Task DisposeAsync() => await _postgres.DisposeAsync();

    [Fact]
    public async Task TheLineIsWrittenInTheOrderItsBuffersImply()
    {
        // The Stations folder browses out in declaration order, which nothing guarantees is
        // line order. position_in_line comes from the buffers or it comes from nowhere.
        await TopologyDiscovery.UpsertAsync(
            _postgres.GetConnectionString(), Topology(Stations("S3", "S1", "S4", "S2")), default);

        Assert.Equal(
            new[] { ("S1", 1), ("S2", 2), ("S3", 3), ("S4", 4) },
            await PositionsAsync());

        Assert.Equal(
            new[] { ("B1_2", "S1", "S2", Capacity), ("B2_3", "S2", "S3", Capacity),
                    ("B3_4", "S3", "S4", Capacity) },
            await BuffersAsync());
    }

    [Fact]
    public async Task APlantThatStopsPublishingBuffersLeavesNoStaleOrderBehind()
    {
        // "Null rather than a guess" is an argument about not asserting an order nothing
        // supports. A position surviving from a discovery that no longer holds asserts exactly
        // that, and worse, looks discovered.
        await TopologyDiscovery.UpsertAsync(
            _postgres.GetConnectionString(), Topology(Stations("S1", "S2", "S3", "S4")), default);
        Assert.NotEmpty(await PositionsAsync());

        await TopologyDiscovery.UpsertAsync(
            _postgres.GetConnectionString(),
            new DiscoveredTopology(Stations("S1", "S2", "S3", "S4"), []),
            default);

        Assert.Empty(await PositionsAsync());
    }

    [Fact]
    public async Task RediscoveryAddsNoRowsAndBurnsNoBufferIds()
    {
        // buffers.id is SMALLSERIAL, and both ON CONFLICT forms evaluate nextval before they
        // detect the conflict. M1 measured 55,956 discoveries exhausting the range on a table
        // holding one row, after which every write failed.
        var connectionString = _postgres.GetConnectionString();
        await TopologyDiscovery.UpsertAsync(
            connectionString, Topology(Stations("S1", "S2", "S3", "S4")), default);
        var sequence = await ScalarAsync("SELECT last_value FROM buffers_id_seq");

        await TopologyDiscovery.UpsertAsync(
            connectionString, Topology(Stations("S1", "S2", "S3", "S4")), default);

        Assert.Equal(3L, await ScalarAsync("SELECT count(*) FROM buffers"));
        Assert.Equal(sequence, await ScalarAsync("SELECT last_value FROM buffers_id_seq"));
    }

    [Fact]
    public async Task ALineWhoseBuffersDoNotChainWritesNothingAtAll()
    {
        // One transaction: a topology refused half way through would leave stations with no
        // buffers between them, which reads exactly like a plant that has none.
        await Assert.ThrowsAsync<InvalidOperationException>(() =>
            TopologyDiscovery.UpsertAsync(
                _postgres.GetConnectionString(),
                new DiscoveredTopology(
                    Stations("S1", "S2", "S3", "S4"),
                    [Buffer("B1_2", "S1", "S2"), Buffer("B1_3", "S1", "S3")]),
                default));

        Assert.Equal(0L, await ScalarAsync("SELECT count(*) FROM stations"));
        Assert.Equal(0L, await ScalarAsync("SELECT count(*) FROM buffers"));
    }

    private static DiscoveredTopology Topology(IReadOnlyList<DiscoveredStation> stations) =>
        new(stations, [Buffer("B1_2", "S1", "S2"), Buffer("B2_3", "S2", "S3"),
                       Buffer("B3_4", "S3", "S4")]);

    private static IReadOnlyList<DiscoveredStation> Stations(params string[] codes) =>
    [
        .. codes.Select(code => new DiscoveredStation(
            code, code + "Function", new NodeId(code, 2), [], EmitsEvents: false, EventTypes: [])),
    ];

    private static DiscoveredBuffer Buffer(string code, string upstream, string downstream) =>
        new(code, new NodeId(code, 2), BuiltInType.UInt32, upstream, downstream, Capacity);

    private async Task<List<(string Code, int Position)>> PositionsAsync()
    {
        await using var connection = new NpgsqlConnection(_postgres.GetConnectionString());
        await connection.OpenAsync();
        await using var command = new NpgsqlCommand(
            """
            SELECT code, position_in_line FROM stations
            WHERE position_in_line IS NOT NULL ORDER BY position_in_line
            """, connection);

        var rows = new List<(string, int)>();
        await using var reader = await command.ExecuteReaderAsync();
        while (await reader.ReadAsync())
        {
            rows.Add((reader.GetString(0), reader.GetInt16(1)));
        }

        return rows;
    }

    private async Task<List<(string Code, string Upstream, string Downstream, short Capacity)>>
        BuffersAsync()
    {
        await using var connection = new NpgsqlConnection(_postgres.GetConnectionString());
        await connection.OpenAsync();
        await using var command = new NpgsqlCommand(
            """
            SELECT b.code, u.code, d.code, b.capacity
            FROM buffers b
            JOIN stations u ON u.id = b.upstream_station_id
            JOIN stations d ON d.id = b.downstream_station_id
            ORDER BY b.code
            """, connection);

        var rows = new List<(string, string, string, short)>();
        await using var reader = await command.ExecuteReaderAsync();
        while (await reader.ReadAsync())
        {
            rows.Add((reader.GetString(0), reader.GetString(1), reader.GetString(2),
                reader.GetInt16(3)));
        }

        return rows;
    }

    private async Task<object?> ScalarAsync(string sql)
    {
        await using var connection = new NpgsqlConnection(_postgres.GetConnectionString());
        await connection.OpenAsync();
        await using var command = new NpgsqlCommand(sql, connection);
        return await command.ExecuteScalarAsync();
    }
}
