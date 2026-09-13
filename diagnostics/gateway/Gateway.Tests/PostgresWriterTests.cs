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

    [Fact]
    public async Task RepeatedWritesDoNotAdvanceTheStationSequence()
    {
        // ON CONFLICT DO UPDATE evaluates nextval even when the row already exists, so the
        // station id sequence advanced once per record. SMALLSERIAL stops at 32,767 and every
        // write then failed — reached in a single 18 h backfill against a one-row table.
        for (var i = 0; i < 200; i++)
        {
            await _writer.WriteBatchAsync([SampleDataChange("TaktTime", Instant.AddSeconds(i), 6.0)]);
        }

        Assert.Equal(1, await CountAsync("stations"));

        await using var connection = new NpgsqlConnection(_postgres.GetConnectionString());
        await connection.OpenAsync();
        await using var command = new NpgsqlCommand("SELECT last_value FROM stations_id_seq", connection);
        Assert.True(
            Convert.ToInt64(await command.ExecuteScalarAsync()) <= 2,
            "the station id sequence advanced per record");
    }

    [Fact]
    public async Task ReconciliationCountsWhatTheBackfillSaidItPulledAgainstWhatIsStored()
    {
        // R1: the ledger records what HistoryRead handed over; the stored count is what
        // survived the upsert. The difference is F2's page-boundary duplicates, absorbed.
        var window = Instant.AddHours(-1);
        await _writer.WriteBatchAsync([SampleDataChange("TaktTime", window.AddMinutes(1), 6.0)]);
        await _writer.WriteBatchAsync([SampleDataChange("TaktTime", window.AddMinutes(2), 6.1)]);

        // Three returned, two stored: the third was the duplicate a page boundary re-included.
        await _writer.RecordBackfillWindowAsync(
            window, Instant, "TaktTime", rowsReturned: 3, pages: 2, durationMs: 10);

        var result = await new Reconciler(_postgres.GetConnectionString())
            .CheckAsync(window, Instant);
        var takt = result.Streams.Single(s => s.Stream == "TaktTime");

        Assert.Equal(3, takt.RowsReturned);
        Assert.Equal(2, takt.RowsStored);
        Assert.Equal(1, takt.DuplicatesAbsorbed);
        Assert.True(result.Reconciled);
    }

    [Fact]
    public async Task RowsReadAndNotStoredAreNotReconciled()
    {
        // R1's actual question. One page, so page boundaries explain no duplicates at all:
        // eight rows returned and two stored is six read and lost, and the whole point of the
        // ledger is that this cannot pass as success.
        var window = Instant.AddHours(-1);
        await _writer.WriteBatchAsync([SampleDataChange("TaktTime", window.AddMinutes(1), 6.0)]);
        await _writer.WriteBatchAsync([SampleDataChange("TaktTime", window.AddMinutes(2), 6.1)]);
        await _writer.RecordBackfillWindowAsync(
            window, Instant, "TaktTime", rowsReturned: 8, pages: 1, durationMs: 10);

        var result = await new Reconciler(_postgres.GetConnectionString())
            .CheckAsync(window, Instant);

        Assert.False(result.Reconciled);
        Assert.Equal(6, result.Streams.Single(s => s.Stream == "TaktTime").Lost);
    }

    [Fact]
    public async Task StoringMoreThanTheLedgerAccountsForIsStillReconciled()
    {
        // Replaces a test that asserted the opposite, and was wrong to. A surplus is the
        // normal state of a running gateway: the live subscription writes rows no backfill
        // window ever claimed, so over any window containing live ingest — which is every
        // window a running gateway is asked about — a surplus says nothing about whether data
        // is missing. Asserted the other way, /reconcile called a healthy gateway broken on
        // the first real request it served.
        var window = Instant.AddHours(-1);
        await _writer.WriteBatchAsync([SampleDataChange("TaktTime", window.AddMinutes(1), 6.0)]);
        await _writer.WriteBatchAsync([SampleDataChange("TaktTime", window.AddMinutes(2), 6.1)]);
        await _writer.RecordBackfillWindowAsync(
            window, Instant, "TaktTime", rowsReturned: 1, pages: 1, durationMs: 10);

        var result = await new Reconciler(_postgres.GetConnectionString())
            .CheckAsync(window, Instant);

        Assert.True(result.Reconciled);
    }

    [Fact]
    public async Task DuplicatesOnePerPageBoundaryDoNotCountAsLoss()
    {
        // F2: the continuation point is the SourceTimestamp of the first row of the next page
        // and the next query re-includes it, so one duplicate arrives per boundary and the
        // upsert absorbs it. Four pages over one window is three boundaries, so three fewer
        // rows stored than returned is exactly explained and is not loss.
        var window = Instant.AddHours(-1);
        await _writer.WriteBatchAsync([SampleDataChange("TaktTime", window.AddMinutes(1), 6.0)]);
        await _writer.RecordBackfillWindowAsync(
            window, Instant, "TaktTime", rowsReturned: 4, pages: 4, durationMs: 10);

        var result = await new Reconciler(_postgres.GetConnectionString())
            .CheckAsync(window, Instant);

        var takt = result.Streams.Single(s => s.Stream == "TaktTime");
        Assert.Equal(3, takt.ExpectedFromPageBoundaries);
        Assert.Equal(0, takt.Lost);
        Assert.True(result.Reconciled);
    }

    [Fact]
    public async Task AGapMarkerLandsAsARowAndBreaksTheReconciliation()
    {
        // §4.4: without gap markers, missing data is indistinguishable from a quiet machine.
        // The table existed from Task 9 and nothing ever wrote to it, so /inspection/stats
        // reported perfect coverage over an outage — the exact failure the table exists to
        // prevent, wearing the table's own name.
        var window = Instant.AddHours(-1);
        await _writer.WriteBatchAsync([
            PostgresWriter.GapRecord(
                window.AddMinutes(10), window.AddMinutes(12), "subscription_overflow",
                "ns=2;i=7"),
        ]);

        Assert.Equal(1, await CountAsync("ingest_gaps"));

        var result = await new Reconciler(_postgres.GetConnectionString())
            .CheckAsync(window, Instant);

        Assert.False(result.Reconciled, "a window containing a recorded gap reconciled");
        var gap = Assert.Single(result.Gaps);
        Assert.Equal("subscription_overflow", gap.Reason);
    }

    [Fact]
    public async Task AGapOverlappingTheWindowCountsEvenWhenItStartsBeforeIt()
    {
        // Overlap rather than containment. An outage that began before the window and ended
        // inside it is still a hole in the window, and asking for containment is how the one
        // gap that matters goes unreported.
        var window = Instant.AddHours(-1);
        await _writer.WriteBatchAsync([
            PostgresWriter.GapRecord(
                window.AddHours(-2), window.AddMinutes(5), "plant_unreachable", "session"),
        ]);

        var result = await new Reconciler(_postgres.GetConnectionString())
            .CheckAsync(window, Instant);

        Assert.Single(result.Gaps);
    }

    [Fact]
    public async Task AGapRecordAlsoLandsVerbatimInRawEvents()
    {
        // §5.1: every record lands raw before anything interprets it, and a gap is a record.
        // Without this the only trace of what was lost is a derived row with no node id, and
        // "this signal overflowed" and "the whole session went away" stop being tellable
        // apart after the fact.
        await _writer.WriteBatchAsync([
            PostgresWriter.GapRecord(
                Instant.AddMinutes(-2), Instant, "subscription_overflow", "ns=2;i=7"),
        ]);

        await using var connection = new NpgsqlConnection(_postgres.GetConnectionString());
        await connection.OpenAsync();
        await using var command = new NpgsqlCommand(
            "SELECT node_id FROM raw_events WHERE kind = 'gap'", connection);
        Assert.Equal("ns=2;i=7", (string?)await command.ExecuteScalarAsync());
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
