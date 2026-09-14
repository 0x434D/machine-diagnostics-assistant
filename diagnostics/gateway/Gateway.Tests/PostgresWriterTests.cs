using Gateway.Ingest;
using Npgsql;
using Testcontainers.PostgreSql;

namespace Gateway.Tests;

public sealed class PostgresWriterTests : IAsyncLifetime
{
    private static readonly DateTime Instant = new(2026, 9, 12, 2, 14, 0, DateTimeKind.Utc);

    // Fixture topology only. The plant's capacity is configuration (§3.1); nothing here
    // asserts on the number, it just has to satisfy the NOT NULL column.
    private const short BufferCapacity = 5;

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

    [Fact]
    public async Task StateAndItsReasonBecomeOneRow()
    {
        // They arrive as two data changes sharing a SourceTimestamp, because that is how
        // the station writes them. One transition must not become two rows.
        await _writer.WriteBatchAsync([
            SampleDataChange("S2", "State", Instant, "Suspended"),
            SampleDataChange("S2", "StateReason", Instant, "starved:B1_2"),
        ]);

        var rows = await QueryAsync("SELECT to_state, reason FROM state_changes");
        var row = Assert.Single(rows);
        Assert.Equal("Suspended", row["to_state"]);
        Assert.Equal("starved:B1_2", row["reason"]);
    }

    [Fact]
    public async Task TheReasonFillsTheRowWhicheverOrderTheTwoArriveIn()
    {
        // A backfill reads history one node at a time, so the whole StateReason series for a
        // station can land before its State series. Depending on the order would turn that
        // into either a lost reason or a failed batch.
        await _writer.WriteBatchAsync([
            SampleDataChange("S2", "StateReason", Instant, "blocked:B2_3"),
        ]);
        await _writer.WriteBatchAsync([
            SampleDataChange("S2", "State", Instant, "Suspended"),
        ]);

        var rows = await QueryAsync("SELECT to_state, reason FROM state_changes");
        var row = Assert.Single(rows);
        Assert.Equal("Suspended", row["to_state"]);
        Assert.Equal("blocked:B2_3", row["reason"]);
    }

    [Fact]
    public async Task TheSuspendReasonResolvesToTheBufferItNames()
    {
        // §3.3 makes this field non-optional because it is what turns propagation from
        // inferred into verifiable. A reason stored only as text would leave every
        // propagation query doing string surgery.
        await SeedTopologyAsync();
        await _writer.WriteBatchAsync([
            SampleDataChange("S3", "State", Instant, "Suspended"),
            SampleDataChange("S3", "StateReason", Instant, "starved:B2_3"),
        ]);

        var rows = await QueryAsync(
            "SELECT b.code FROM state_changes s JOIN buffers b ON b.id = s.reason_buffer_id");
        Assert.Equal("B2_3", Assert.Single(rows)["code"]);
    }

    [Fact]
    public async Task AReasonNamingSomethingThatIsNotABufferStillStores()
    {
        // "starved:carrier-return" is a real condition with no buffer behind it — the carrier
        // pool, not a buffer, is what binds when the line backs up. The row must survive with
        // a null reason_buffer_id rather than being dropped.
        await SeedTopologyAsync();
        await _writer.WriteBatchAsync([
            SampleDataChange("S1", "State", Instant, "Suspended"),
            SampleDataChange("S1", "StateReason", Instant, "starved:carrier-return"),
        ]);

        var row = Assert.Single(await QueryAsync(
            "SELECT reason, reason_buffer_id FROM state_changes"));
        Assert.Equal("starved:carrier-return", row["reason"]);
        Assert.Equal(DBNull.Value, row["reason_buffer_id"]);
    }

    [Fact]
    public async Task AStateWhoseReasonNeverArrivesIsStillATransition()
    {
        // The bring-up case, and it is the common one: each station publishes six State
        // transitions while StateReason stays "", and an unchanged value is never sent. Six
        // rows per station therefore arrive with no reason data change at all.
        await _writer.WriteBatchAsync([SampleDataChange("S1", "State", Instant, "Execute")]);

        var row = Assert.Single(await QueryAsync("SELECT to_state, reason FROM state_changes"));
        Assert.Equal("Execute", row["to_state"]);
        Assert.Equal(DBNull.Value, row["reason"]);
    }

    [Fact]
    public async Task TheFirstStateSeenAfterAConnectHasNoPredecessor()
    {
        await _writer.WriteBatchAsync([SampleDataChange("S1", "State", Instant, "Execute")]);

        var row = Assert.Single(await QueryAsync("SELECT from_state FROM state_changes"));
        Assert.Equal(DBNull.Value, row["from_state"]);
    }

    [Fact]
    public async Task ATransitionNamesTheStateItCameFrom()
    {
        // A data change carries only the new value, so the only source for from_state is what
        // the gateway last saw. Without it every row says a station arrived from nowhere.
        await _writer.WriteBatchAsync([SampleDataChange("S1", "State", Instant, "Execute")]);
        await _writer.WriteBatchAsync([
            SampleDataChange("S1", "State", Instant.AddSeconds(30), "Suspended"),
        ]);

        var rows = await QueryAsync(
            "SELECT from_state, to_state FROM state_changes ORDER BY source_ts");
        Assert.Equal(2, rows.Count);
        Assert.Equal("Execute", rows[1]["from_state"]);
        Assert.Equal("Suspended", rows[1]["to_state"]);
    }

    [Fact]
    public async Task BufferLevelsDoNotLandInTheSignalsTable()
    {
        // signals is keyed on a station. A buffer level keyed to a station would have to
        // pick one of the two it sits between, and either choice is wrong.
        await SeedTopologyAsync();
        await _writer.WriteBatchAsync([SampleBufferLevel("B2_3", Instant, 3)]);

        Assert.Empty(await QueryAsync("SELECT 1 FROM signals"));
        Assert.Single(await QueryAsync("SELECT 1 FROM buffer_levels"));
    }

    [Fact]
    public async Task ABufferLevelForAnUndiscoveredBufferFailsRatherThanDisappears()
    {
        // A station is created from the code its signals carry; a buffer cannot be, because
        // its two stations and its capacity are read by browsing. Levels arriving before
        // discovery are discovery not having run, and §5.1 has no quiet half-write.
        await Assert.ThrowsAnyAsync<Exception>(
            () => _writer.WriteBatchAsync([SampleBufferLevel("B2_3", Instant, 3)]));

        Assert.Equal(0, await CountAsync("raw_events"));
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

    /// <summary>A string-valued data change: State and StateReason, which signals cannot hold.</summary>
    private static IngestRecord SampleDataChange(
        string station, string signal, DateTime ts, string value) => new(
        Kind: "datachange", NodeId: $"ns=2;s={station}.{signal}", SourceTs: ts, ServerTs: ts,
        StatusCode: 0,
        PayloadJson: $$"""{"Station":"{{station}}","Signal":"{{signal}}","Value":"{{value}}"}""",
        ImageBytes: null);

    /// <summary>Named by its buffer, because the address space gives a level no station.</summary>
    private static IngestRecord SampleBufferLevel(string buffer, DateTime ts, int level) => new(
        Kind: "datachange", NodeId: $"ns=2;s={buffer}.Level", SourceTs: ts, ServerTs: ts,
        StatusCode: 0,
        PayloadJson: $$"""{"Buffer":"{{buffer}}","Signal":"Level","Value":{{level}}}""",
        ImageBytes: null);

    /// <summary>
    /// What Task 9's browse will write: §3.1's four stations and the three buffers between
    /// them, under the codes <c>TopologyDiscovery.SplitBrowseName</c> produces.
    /// </summary>
    private async Task SeedTopologyAsync()
    {
        await using var connection = new NpgsqlConnection(_postgres.GetConnectionString());
        await connection.OpenAsync();
        // Two commands, not one batch: Npgsql prepares a parameterised statement, and a
        // prepared statement holds exactly one command.
        await using var stations = new NpgsqlCommand(
            """
            INSERT INTO stations (code, name) VALUES
              ('S1', 'Feeding'), ('S2', 'Joining'), ('S3', 'Inspection'), ('S4', 'Outfeed')
            """, connection);
        await stations.ExecuteNonQueryAsync();

        await using var buffers = new NpgsqlCommand(
            """
            INSERT INTO buffers (code, upstream_station_id, downstream_station_id, capacity)
            SELECT b.code, u.id, d.id, $1
            FROM (VALUES ('B1_2', 'S1', 'S2'), ('B2_3', 'S2', 'S3'), ('B3_4', 'S3', 'S4'))
                 AS b (code, upstream, downstream)
            JOIN stations u ON u.code = b.upstream
            JOIN stations d ON d.code = b.downstream
            """, connection);
        buffers.Parameters.AddWithValue(BufferCapacity);
        await buffers.ExecuteNonQueryAsync();
    }

    private async Task<IReadOnlyList<IReadOnlyDictionary<string, object>>> QueryAsync(string sql)
    {
        await using var connection = new NpgsqlConnection(_postgres.GetConnectionString());
        await connection.OpenAsync();
        await using var command = new NpgsqlCommand(sql, connection);
        await using var reader = await command.ExecuteReaderAsync();

        var rows = new List<IReadOnlyDictionary<string, object>>();
        while (await reader.ReadAsync())
        {
            var row = new Dictionary<string, object>(StringComparer.Ordinal);
            for (var i = 0; i < reader.FieldCount; i++)
            {
                row[reader.GetName(i)] = reader.GetValue(i);
            }

            rows.Add(row);
        }

        return rows;
    }

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
