using Gateway.Ingest;
using Npgsql;
using Testcontainers.PostgreSql;

namespace Gateway.Tests;

public sealed class PostgresWriterTests : IAsyncLifetime
{
    private static readonly DateTime Instant = new(2026, 9, 12, 2, 14, 0, DateTimeKind.Utc);

    // Pinned by digest, not by tag (§10.7). scripts/pin-images.sh re-resolves it.
    private const string PostgresImage =
        "postgres:17-bookworm@sha256:051f7b7b3abdd564d5d1bd1e8c4b9c1b6e77087d1dd22020ede611c096a272e0";

    private readonly PostgreSqlContainer _postgres = new PostgreSqlBuilder(PostgresImage).Build();

    private PostgresWriter _writer = null!;

    public async Task InitializeAsync()
    {
        await _postgres.StartAsync();
        await PostgresWriter.ApplySchemaAsync(_postgres.GetConnectionString());
        _writer = new PostgresWriter(_postgres.GetConnectionString());
    }

    public async Task DisposeAsync() => await _postgres.DisposeAsync();

    [Fact]
    public async Task RawAndNormalisedAreWrittenInOneTransaction()
    {
        // §5.1: one write path, derivation in one place — both or neither. Triggered by a
        // genuinely malformed record (an event with no assembly serial, which violates the
        // inspection_results primary key) rather than by a test-only failure switch, so the
        // test cannot pass while the real failure path is broken.
        var malformed = new IngestRecord(
            Kind: "event", NodeId: "ns=2;i=9", SourceTs: Instant, ServerTs: Instant,
            StatusCode: 0, PayloadJson: """{"Station":"S3","ModelVersion":"sim-1"}""",
            ImageBytes: null);

        await Assert.ThrowsAnyAsync<Exception>(() => _writer.WriteBatchAsync([malformed]));

        Assert.Equal(0, await CountAsync("raw_events"));
        Assert.Equal(0, await CountAsync("inspection_results"));
    }

    [Fact]
    public async Task DuplicateSignalValuesCollapseToOneRow()
    {
        // Not an edge case: this fires on every page boundary of every backfill (§4.4).
        var sample = SampleDataChange("TaktTime", Instant, 6.02);

        await _writer.WriteBatchAsync([sample]);
        await _writer.WriteBatchAsync([sample]);

        Assert.Equal(1, await CountAsync("signals"));
        Assert.Equal(2, await CountAsync("raw_events"));   // raw is append-only and verbatim
    }

    [Fact]
    public async Task OnlyRejectsCarryAnImage()
    {
        await _writer.WriteBatchAsync([SampleEvent("A-1", reject: true, image: new byte[1024])]);
        await _writer.WriteBatchAsync([SampleEvent("A-2", reject: false, image: null)]);

        Assert.Equal(2, await CountAsync("inspection_results"));
        Assert.Equal(1, await CountAsync("inspection_images"));
    }

    [Fact]
    public async Task OutOfOrderArrivalsAreOrderedBySourceTimestampOnRead()
    {
        // §4.4: ordering by SourceTimestamp on read, never by arrival.
        await _writer.WriteBatchAsync([SampleDataChange("TaktTime", Instant.AddSeconds(12), 6.1)]);
        await _writer.WriteBatchAsync([SampleDataChange("TaktTime", Instant, 6.0)]);

        var ordered = await ReadSignalsOrderedAsync("TaktTime");

        Assert.Equal([6.0, 6.1], ordered);
    }

    [Fact]
    public async Task ImageBytesSurviveThePostgresRoundTrip()
    {
        // R4 measured img_p99 at 110,419 B, so this is the real size, not a token blob.
        var image = new byte[110_419];
        Random.Shared.NextBytes(image);

        await _writer.WriteBatchAsync([SampleEvent("A-3", reject: true, image: image)]);

        await using var connection = new NpgsqlConnection(_postgres.GetConnectionString());
        await connection.OpenAsync();
        await using var command = new NpgsqlCommand(
            "SELECT bytes FROM inspection_images WHERE assembly_serial = 'A-3'", connection);
        Assert.Equal(image, (byte[]?)await command.ExecuteScalarAsync());
    }

    private static IngestRecord SampleDataChange(string signal, DateTime ts, double value) => new(
        Kind: "datachange", NodeId: "ns=2;i=7", SourceTs: ts, ServerTs: ts, StatusCode: 0,
        PayloadJson: $$"""{"Station":"S3","Signal":"{{signal}}","Value":{{value}}}""",
        ImageBytes: null);

    private static IngestRecord SampleEvent(string serial, bool reject, byte[]? image) => new(
        Kind: "event", NodeId: "ns=2;i=9", SourceTs: Instant, ServerTs: Instant, StatusCode: 0,
        PayloadJson: $$"""
            {"Station":"S3","AssemblySerial":"{{serial}}","Disposition":"{{(reject ? "reject" : "good")}}",
             "DefectClass":{{(reject ? "\"gap\"" : "null")}},"Confidence":0.87,"ModelVersion":"sim-1"}
            """,
        ImageBytes: image);

    private async Task<int> CountAsync(string table)
    {
        await using var connection = new NpgsqlConnection(_postgres.GetConnectionString());
        await connection.OpenAsync();
        await using var command = new NpgsqlCommand($"SELECT COUNT(*) FROM {table}", connection);
        return Convert.ToInt32(await command.ExecuteScalarAsync());
    }

    private async Task<double[]> ReadSignalsOrderedAsync(string signal)
    {
        await using var connection = new NpgsqlConnection(_postgres.GetConnectionString());
        await connection.OpenAsync();
        await using var command = new NpgsqlCommand(
            "SELECT value FROM signals WHERE signal = $1 ORDER BY source_ts", connection);
        command.Parameters.AddWithValue(signal);

        var values = new List<double>();
        await using var reader = await command.ExecuteReaderAsync();
        while (await reader.ReadAsync())
        {
            values.Add(reader.GetDouble(0));
        }

        return [.. values];
    }
}
