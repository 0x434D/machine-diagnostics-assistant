using System.Globalization;
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

    // This container's analysis role and nowhere else's. 005 creates the role without a
    // password on purpose, so whoever owns the database supplies one -- Compose through the
    // gateway, and this fixture for the container it just started.
    private const string AnalysisPassword = "analysis-under-test";

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
        await Assert.ThrowsAnyAsync<Exception>(
            () => _writer.WriteBatchAsync([MalformedEvent("S3")]));

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
            window, Instant, "S3.TaktTime", rowsReturned: 3, pages: 2, durationMs: 10);

        var result = await new Reconciler(_postgres.GetConnectionString())
            .CheckAsync(window, Instant);
        var takt = result.Streams.Single(s => s.Stream == "S3.TaktTime");

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
            window, Instant, "S3.TaktTime", rowsReturned: 8, pages: 1, durationMs: 10);

        var result = await new Reconciler(_postgres.GetConnectionString())
            .CheckAsync(window, Instant);

        Assert.False(result.Reconciled);
        Assert.Equal(6, result.Streams.Single(s => s.Stream == "S3.TaktTime").Lost);
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
            window, Instant, "S3.TaktTime", rowsReturned: 1, pages: 1, durationMs: 10);

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
            window, Instant, "S3.TaktTime", rowsReturned: 4, pages: 4, durationMs: 10);

        var result = await new Reconciler(_postgres.GetConnectionString())
            .CheckAsync(window, Instant);

        var takt = result.Streams.Single(s => s.Stream == "S3.TaktTime");
        Assert.Equal(3, takt.ExpectedFromPageBoundaries);
        Assert.Equal(0, takt.Lost);
        Assert.True(result.Reconciled);
    }

    [Fact]
    public async Task EveryStreamGetsItsOwnReconciliationRow()
    {
        // R1's ledger is per stream. One aggregate row across 25 streams cannot say which one
        // came back short, which is the only thing the ledger is for.
        foreach (var stream in new[] { "S2.TaktTime", "S2.JoiningForcePeak" })
        {
            await _writer.RecordBackfillWindowAsync(
                Instant, Instant.AddHours(1), stream, rowsReturned: 600, pages: 1, durationMs: 12);
        }

        var rows = await QueryAsync(
            "SELECT stream, rows_returned FROM backfill_windows ORDER BY stream");

        Assert.Equal(2, rows.Count);
        Assert.Equal("S2.JoiningForcePeak", rows[0]["stream"]);
        Assert.Equal("S2.TaktTime", rows[1]["stream"]);
    }

    [Fact]
    public async Task TwoStationsOneSignalAndOneWindowAreTwoLedgerRows()
    {
        // The defect the qualifier exists for: backfill_windows is UNIQUE (from_ts, to_ts,
        // stream) and the write upserts on it, so four stations recording a bare "TaktTime"
        // over the same window leave one row holding the last station's numbers — and the
        // reconciliation then reports that station's count for all four. Station CODE, not
        // browse name: two identifiers for one station means two rows for one station.
        foreach (var station in new[] { "S1", "S2", "S3", "S4" })
        {
            await _writer.RecordBackfillWindowAsync(
                Instant, Instant.AddHours(1), $"{station}.TaktTime",
                rowsReturned: 600, pages: 1, durationMs: 12);
        }

        Assert.Equal(4, await CountAsync("backfill_windows"));
    }

    [Fact]
    public async Task AStreamTheLedgerCannotAttributeToAnOwnerIsRefused()
    {
        // An unqualified row predates the qualifier and could belong to any of four stations.
        // Counted against one of them it would report a loss or a surplus that is an artefact
        // of the guess, so /reconcile fails loudly instead of answering.
        await _writer.RecordBackfillWindowAsync(
            Instant, Instant.AddHours(1), "TaktTime", rowsReturned: 600, pages: 1, durationMs: 12);

        var reconciler = new Reconciler(_postgres.GetConnectionString());
        var exception = await Assert.ThrowsAsync<InvalidOperationException>(
            () => reconciler.CheckAsync(Instant, Instant.AddHours(1)));

        Assert.Contains("TaktTime", exception.Message, StringComparison.Ordinal);
    }

    [Fact]
    public async Task EveryKindOfStreamIsReconciledAgainstTheTableItsRowsWentInto()
    {
        // A signal, a buffer level and an inspection event land in three different tables, and
        // a stream counted against the wrong one reconciles against nothing at all.
        await SeedTopologyAsync();
        var window = Instant.AddHours(-1);

        await _writer.WriteBatchAsync([
            SampleDataChange("TaktTime", window.AddMinutes(1), 6.0),
            SampleBufferLevel("B1_2", window.AddMinutes(1), 3),
            SampleEvent("A-1", reject: false, image: null),
        ]);

        foreach (var stream in new[] { "S3.TaktTime", "B1_2.Level", "S3.Events" })
        {
            await _writer.RecordBackfillWindowAsync(
                window, Instant, stream, rowsReturned: 1, pages: 1, durationMs: 10);
        }

        // Past Instant, because SampleEvent is stamped on it and the window is half-open.
        var result = await new Reconciler(_postgres.GetConnectionString())
            .CheckAsync(window, Instant.AddMinutes(1));

        Assert.Equal(3, result.Streams.Count);
        Assert.All(result.Streams, stream => Assert.Equal(1, stream.RowsStored));
        Assert.True(result.Reconciled);
    }

    [Fact]
    public async Task AnUnsuspendCarriesNoReasonAndIsNotALostStateReasonRow()
    {
        // A StateReason value is not a row of its own: it is a column on the row its paired
        // State keys. The empty string a station publishes when it stops being suspended is
        // deliberately stored as the NULL that already says so, and counting only the rows
        // carrying text would report every unsuspend in the history as read and lost.
        await SeedTopologyAsync();
        var window = Instant.AddHours(-1);
        var suspended = window.AddMinutes(1);
        var running = window.AddMinutes(2);

        await _writer.WriteBatchAsync([
            SampleDataChange("S2", "State", suspended, "Suspended"),
            SampleDataChange("S2", "StateReason", suspended, "starved:B1_2"),
            SampleDataChange("S2", "State", running, "Execute"),
            SampleDataChange("S2", "StateReason", running, ""),
        ]);

        await _writer.RecordBackfillWindowAsync(
            window, Instant, "S2.State", rowsReturned: 2, pages: 1, durationMs: 10);
        await _writer.RecordBackfillWindowAsync(
            window, Instant, "S2.StateReason", rowsReturned: 2, pages: 1, durationMs: 10);

        var result = await new Reconciler(_postgres.GetConnectionString())
            .CheckAsync(window, Instant);

        Assert.Equal(2, result.Streams.Single(s => s.Stream == "S2.State").RowsStored);
        Assert.Equal(2, result.Streams.Single(s => s.Stream == "S2.StateReason").RowsStored);
        Assert.True(result.Reconciled);
    }

    [Fact]
    public async Task AStateReasonStreamThatCameBackShortIsReportedAsLost()
    {
        // The check has to be able to fire. Counted against state_changes it could not: a
        // StateReason shares its (station_id, source_ts) key with the State it is paired with,
        // so the stored count is decided entirely by State and Returned == Stored under every
        // input, including a StateReason read that lost half its rows. raw_events is verbatim
        // and holds the empty reasons the derivation drops, so it is 1:1 with what was read.
        await SeedTopologyAsync();
        var window = Instant.AddHours(-1);

        // The plant's own shape: an unchanged value is never published, so a station runs
        // through six State transitions while StateReason moves twice — once to a reason and
        // once back to the empty string. The two streams are different lengths, which is what
        // makes counting one of them against the other's rows useless.
        var states = new[] { "Idle", "Starting", "Execute", "Suspended", "Execute", "Idle" };
        for (var minute = 1; minute <= states.Length; minute++)
        {
            await _writer.WriteBatchAsync(
                [SampleDataChange("S2", "State", window.AddMinutes(minute), states[minute - 1])]);
        }

        await _writer.WriteBatchAsync([
            SampleDataChange("S2", "StateReason", window.AddMinutes(4), "starved:B1_2"),
        ]);
        await _writer.WriteBatchAsync([
            SampleDataChange("S2", "StateReason", window.AddMinutes(5), ""),
        ]);

        // The reader claims three where two arrived: one row read and lost, on one page, so no
        // page boundary explains it. Counted against state_changes the station's six rows swamp
        // it and the loss reads as a surplus.
        await _writer.RecordBackfillWindowAsync(
            window, Instant, "S2.StateReason", rowsReturned: 3, pages: 1, durationMs: 10);

        var result = await new Reconciler(_postgres.GetConnectionString())
            .CheckAsync(window, Instant);
        var reason = result.Streams.Single(s => s.Stream == "S2.StateReason");

        Assert.Equal(2, reason.RowsStored);
        Assert.Equal(1, reason.Lost);
        Assert.False(result.Reconciled);
    }

    [Fact]
    public async Task ARereadStateRowDoesNotInventTheTransitionItCameFrom()
    {
        // What a subdivided backfill window does: the truncated read hands its rows over
        // before the halves re-read the same range from the start, so this memory is asked
        // about a row that precedes what it holds. Standing at the last row of the truncated
        // read it would name that as what the first row transitioned from -- and the upsert's
        // COALESCE cannot catch it, because the first State the gateway ever sees is stored
        // with a null from_state and anything beats null. d28fc78 fixed one way of inventing a
        // PackML transition; this is the other.
        await SeedTopologyAsync();
        var window = Instant.AddHours(-1);
        var first = window.AddMinutes(1);
        var second = window.AddMinutes(2);

        // The truncated pass.
        await _writer.WriteBatchAsync([SampleDataChange("S2", "State", first, "Execute")]);
        await _writer.WriteBatchAsync([SampleDataChange("S2", "State", second, "Suspended")]);

        // The halves, re-reading the same range from its start.
        await _writer.WriteBatchAsync([SampleDataChange("S2", "State", first, "Execute")]);
        await _writer.WriteBatchAsync([SampleDataChange("S2", "State", second, "Suspended")]);

        var rows = await QueryAsync(
            "SELECT c.source_ts, c.from_state, c.to_state FROM state_changes c "
            + "JOIN stations s ON s.id = c.station_id WHERE s.code = 'S2' ORDER BY c.source_ts");

        Assert.Equal(2, rows.Count);
        Assert.Equal(DBNull.Value, rows[0]["from_state"]);
        Assert.Equal("Execute", rows[0]["to_state"]);
        Assert.Equal("Execute", rows[1]["from_state"]);
        Assert.Equal("Suspended", rows[1]["to_state"]);
    }

    [Fact]
    public async Task TheLedgerNamesEveryStreamThisGatewayHasEverBackfilled()
    {
        // What a discovery that came back short is held against. Without it, a plant that
        // stopped publishing one stream backfills the rest, writes one ledger row fewer, and
        // answers /reconcile with every remaining stream green.
        foreach (var stream in new[] { "S1.TaktTime", "S2.TaktTime", "B1_2.Level" })
        {
            await _writer.RecordBackfillWindowAsync(
                Instant, Instant.AddHours(1), stream, rowsReturned: 1, pages: 1, durationMs: 1);
        }

        var known = await _writer.KnownBackfillStreamsAsync();

        Assert.Equal(
            ["B1_2.Level", "S1.TaktTime", "S2.TaktTime"],
            known.Order(StringComparer.Ordinal));
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
    public async Task AFailedBatchDoesNotMoveTheGatewaysMemoryOfWhereAStationWas()
    {
        // QueueDrain retries a failed batch with nothing acked — that is the local queue's
        // whole purpose — so the retry re-reads records the attempt already consumed. When the
        // attempt's memory survived the rollback, the retry wrote the FIRST row as arriving
        // from the state the LAST row moved to: Execute@T1 came out as
        // "from Suspended to Execute", a transition PackML does not permit and the plant never
        // published, in the one table that exists to record real ones. Nothing logged, and
        // raw_events agreed with it.
        await Assert.ThrowsAnyAsync<Exception>(() => _writer.WriteBatchAsync([
            SampleDataChange("S1", "State", Instant, "Execute"),
            SampleDataChange("S1", "State", Instant.AddSeconds(30), "Suspended"),
            MalformedEvent("S1"),
        ]));

        await _writer.WriteBatchAsync([
            SampleDataChange("S1", "State", Instant, "Execute"),
            SampleDataChange("S1", "State", Instant.AddSeconds(30), "Suspended"),
        ]);

        var rows = await QueryAsync(
            "SELECT from_state, to_state FROM state_changes ORDER BY source_ts");
        Assert.Equal(2, rows.Count);
        Assert.Equal(DBNull.Value, rows[0]["from_state"]);
        Assert.Equal("Execute", rows[1]["from_state"]);
    }

    [Fact]
    public async Task AStationLearnedByAFailedBatchIsNotTakenOnTrustByTheRetry()
    {
        // The same shape one method over: the id is produced by an INSERT inside the
        // transaction, and SMALLSERIAL does not roll back with it. A cached id therefore
        // named a row that no longer existed, and every later write for that station failed
        // its foreign key against a stations table that never had it.
        await Assert.ThrowsAnyAsync<Exception>(() => _writer.WriteBatchAsync([
            SampleDataChange("TaktTime", Instant, 6.0),
            MalformedEvent("S3"),
        ]));

        await _writer.WriteBatchAsync([SampleDataChange("TaktTime", Instant, 6.0)]);

        Assert.Equal(1, await CountAsync("signals"));
    }

    [Fact]
    public async Task AReasonOnItsOwnThatSaysNothingWritesNoRow()
    {
        // "" is what StateReason reads when a station is not suspended, and it is published
        // because it changed. On its own it fills no column, so writing it would key a row
        // carrying no state, no reason and no transition — and a batch boundary that split it
        // from its State half would leave that empty row in the table for good.
        await _writer.WriteBatchAsync([SampleDataChange("S1", "StateReason", Instant, "")]);

        Assert.Empty(await QueryAsync("SELECT 1 FROM state_changes"));
        Assert.Equal(1, await CountAsync("raw_events"));   // raw is append-only and verbatim
    }

    [Fact]
    public async Task OnlySettledTransitionsAreVisibleToTheAnalysisView()
    {
        // to_state is nullable so a reason can land before its state, which leaves a row that
        // is not yet a transition. §5.2 has the analysis service read views, so the filter is
        // the schema's job — a propagation query that forgot it would not fail, it would count
        // half rows as transitions.
        await _writer.WriteBatchAsync([
            SampleDataChange("S1", "StateReason", Instant, "blocked:B1_2"),
            SampleDataChange("S2", "State", Instant, "Execute"),
        ]);

        Assert.Equal(2, (await QueryAsync("SELECT 1 FROM state_changes")).Count);
        var settled = Assert.Single(await QueryAsync(
            "SELECT to_state FROM state_changes_settled"));
        Assert.Equal("Execute", settled["to_state"]);
    }

    [Fact]
    public async Task ApplyingTheSchemaTwiceLeavesWhatIsAlreadyThere()
    {
        // It runs at every boot, against a database that already holds M1's tables, M2a's and
        // now M2b's. The IF NOT EXISTS clauses are the whole of that guarantee, and an
        // unapplied one only shows up against a live database. The ALTERs matter most: a
        // second ADD COLUMN without IF NOT EXISTS is a boot failure on every restart.
        await SeedTopologyAsync();
        await _writer.WriteBatchAsync([
            SampleBufferLevel("B2_3", Instant, 3),
            AssemblyCreated("A-00000080", ["C-1-00000080"], Instant),
            SampleEvent("A-00000080", reject: false, image: null, Instant, carrierId: 7),
        ]);

        await PostgresWriter.ApplySchemaAsync(_postgres.GetConnectionString());

        Assert.Single(await QueryAsync("SELECT 1 FROM buffer_levels"));
        Assert.Equal(4, await CountAsync("stations"));
        Assert.Single(await QueryAsync("SELECT 1 FROM genealogy"));
        Assert.Single(await QueryAsync("SELECT defect_classes FROM inspection_results"));
    }

    [Fact]
    public async Task TheAnalysisRoleReadsThroughViewsAndCannotWrite()
    {
        // §5.2 and CLAUDE.md both claim the database enforces this rather than convention.
        // Asserted from this side as well as from analysis/tests/test_read_layer.py because
        // this is where the migration that grants it lives: a table added here without a
        // view, or a GRANT widened by accident, is a change to what the analysis service may
        // do that nothing in the Python stack would be asked to review.
        await SeedTopologyAsync();

        await using var owner = new NpgsqlConnection(_postgres.GetConnectionString());
        await owner.OpenAsync();
        await using var shipped = new NpgsqlCommand(
            "SELECT rolpassword IS NULL FROM pg_authid WHERE rolname = 'analysis'", owner);
        Assert.True(
            await shipped.ExecuteScalarAsync() as bool?,
            "the migration ships a credential for the analysis role, so every deployment "
            + "built from this binary shares it");

        await PostgresWriter.SetAnalysisRolePasswordAsync(
            _postgres.GetConnectionString(), AnalysisPassword);
        var asAnalysis = new NpgsqlConnectionStringBuilder(_postgres.GetConnectionString())
        {
            Username = "analysis",
            Password = AnalysisPassword,
        }.ConnectionString;

        await using var connection = new NpgsqlConnection(asAnalysis);
        await connection.OpenAsync();

        await using var read = new NpgsqlCommand(
            "SELECT count(*) FROM read.stations", connection);
        Assert.Equal(4, Convert.ToInt32(await read.ExecuteScalarAsync(), CultureInfo.InvariantCulture));

        await using var write = new NpgsqlCommand(
            "INSERT INTO ingest.stations (code, name) VALUES ('S9', 'Forged')", connection);
        var refused = await Assert.ThrowsAsync<PostgresException>(
            () => write.ExecuteNonQueryAsync());
        Assert.Equal(PostgresErrorCodes.InsufficientPrivilege, refused.SqlState);
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
    public async Task ABufferLevelAndAStateChangeInOneBatchEachTakeTheirOwnRoute()
    {
        // The mixed batch. A level record names no station, so the routing `continue`s before
        // the station lookup that every other record needs -- and one batch really does carry
        // both, because the subscription delivers all 25 streams into the same queue. Every
        // other test here feeds the writer one kind at a time, which is exactly the shape that
        // cannot tell a `continue` from a `return`: a routing bug that dropped the rest of the
        // batch after a level, or that sent a level down the station path, passes all of them.
        await SeedTopologyAsync();

        await _writer.WriteBatchAsync([
            SampleBufferLevel("B2_3", Instant, 3),
            SampleDataChange("S2", "State", Instant, "Suspending"),
            SampleDataChange("S2", "StateReason", Instant, "blocked:B2_3"),
            SampleBufferLevel("B1_2", Instant.AddSeconds(1), 4),
            SampleDataChange("TaktTime", Instant, 6.02),
        ]);

        Assert.Equal(2, await CountAsync("buffer_levels"));
        Assert.Single(await QueryAsync("SELECT 1 FROM state_changes"));
        Assert.Equal(1, await CountAsync("signals"));
        Assert.Equal(5, await CountAsync("raw_events"));

        var transition = Assert.Single(await QueryAsync(
            "SELECT to_state, reason FROM state_changes"));
        Assert.Equal("Suspending", transition["to_state"]);
        Assert.Equal("blocked:B2_3", transition["reason"]);
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

    [Fact]
    public async Task AnAssemblyIsBuiltFromTheComponentsItsOwnEventNames()
    {
        // §3.4a's as-built structure, recorded at the instant of production rather than
        // reconstructed from "which components were at S1 around then".
        await _writer.WriteBatchAsync([
            ComponentRead("C-1-00000001", lane: 1, "L-2305", Instant),
            ComponentRead("C-2-00000001", lane: 2, "L-2306", Instant),
            AssemblyCreated("A-00000001", ["C-1-00000001", "C-2-00000001"], Instant),
        ]);

        var rows = await QueryAsync(
            "SELECT component_serial, position FROM genealogy "
            + "WHERE assembly_serial = 'A-00000001' ORDER BY position");

        Assert.Equal(2, rows.Count);
        Assert.Equal("C-1-00000001", rows[0]["component_serial"]);
        Assert.Equal((short)0, rows[0]["position"]);
        Assert.Equal("C-2-00000001", rows[1]["component_serial"]);
        Assert.Equal((short)1, rows[1]["position"]);
    }

    [Fact]
    public async Task TheGenealogyLandsWhicheverOrderTheTwoIdentityEventsArriveIn()
    {
        // THE ordering case. genealogy references assemblies and components, and nothing
        // orders the events that fill them: the backfill reads one node at a time, a 200-record
        // drain batch can split a cycle in half, and the two S1 types share a page boundary
        // like any other rows. A foreign key that could not be satisfied here would raise a
        // PostgresException, which QueueDrain retries with nothing acked — one record
        // retried for ever, ingest stalled, the queue growing. Same failure M2a's nullable
        // to_state was the answer to.
        await _writer.WriteBatchAsync([
            AssemblyCreated("A-00000002", ["C-1-00000002", "C-2-00000002"], Instant),
        ]);
        await _writer.WriteBatchAsync([
            ComponentRead("C-2-00000002", lane: 2, "L-2306", Instant),
            ComponentRead("C-1-00000002", lane: 1, "L-2305", Instant),
        ]);

        // The link is there from the first batch, and the component rows it pointed at fill
        // in from the second rather than being replaced by it.
        Assert.Equal(2, (await QueryAsync(
            "SELECT 1 FROM genealogy WHERE assembly_serial = 'A-00000002'")).Count);

        var component = Assert.Single(await QueryAsync(
            "SELECT c.lane, c.read_at, l.lot_code, l.supplier FROM components c "
            + "JOIN component_lots l ON l.id = c.lot_id WHERE c.serial = 'C-1-00000002'"));
        Assert.Equal((short)1, component["lane"]);
        Assert.Equal("L-2305", component["lot_code"]);
        Assert.Equal("SUP-01", component["supplier"]);
    }

    [Fact]
    public async Task AComponentNamedByAnAssemblyAndNeverReadSaysSoRatherThanBlockingTheBatch()
    {
        // The horizon case for components: a component drawn before this gateway's history
        // starts is named by the assembly that used it and by nothing else. The row records
        // that it exists and claims nothing about where it came from.
        await _writer.WriteBatchAsync([
            AssemblyCreated("A-00000003", ["C-1-00000003"], Instant),
        ]);

        var row = Assert.Single(await QueryAsync(
            "SELECT lot_id, lane, read_at FROM components WHERE serial = 'C-1-00000003'"));
        Assert.Equal(DBNull.Value, row["lot_id"]);
        Assert.Equal(DBNull.Value, row["lane"]);
        Assert.Equal(DBNull.Value, row["read_at"]);
    }

    [Fact]
    public async Task APartCreatedBeforeTheHorizonStillStoresEverythingAfterIt()
    {
        // The case no arrival order can fix, and the reason the parent row is created on
        // demand rather than required. Three buffers of five carriers means up to fifteen
        // assemblies are in flight at any instant, so on every boot the first backfill
        // window holds press records, verdicts and dispositions for parts whose
        // AssemblyCreatedEvent is on the far side of the horizon and will never arrive.
        await SeedTopologyAsync();
        await _writer.WriteBatchAsync([
            PartProcessed("A-00000004", Instant, [10.0, 20.0, 30.0]),
            SampleEvent("A-00000004", reject: false, image: null, Instant, carrierId: 3),
            PartCompleted("A-00000004", "good", reason: null, Instant),
        ]);

        var assembly = Assert.Single(await QueryAsync(
            "SELECT created_at, carrier_id FROM assemblies WHERE serial = 'A-00000004'"));
        Assert.Equal(DBNull.Value, assembly["created_at"]);
        Assert.Equal(DBNull.Value, assembly["carrier_id"]);

        Assert.Equal(2, await CountAsync("part_process_values"));
        Assert.Equal(1, await CountAsync("part_process_curves"));
        Assert.Equal(1, await CountAsync("part_dispositions"));
    }

    [Fact]
    public async Task AVerdictOnItsOwnStillPutsThePartOnTheLine()
    {
        // inspection_results.assembly_serial is the one per-part key with NO foreign key to
        // assemblies, so a verdict whose part has no row there fails nothing and nothing says
        // so. It happens whenever a run ends between S3 and S4 -- the backfill's `to` boundary
        // is a wall-clock instant and the part is mid-line. §14's trace starts from
        // assemblies, so the part would be omitted from its own history rather than answered
        // with the verdict that is sitting right there.
        await SeedTopologyAsync();
        await _writer.WriteBatchAsync([
            SampleEvent("A-00000090", reject: true, new byte[8], Instant, carrierId: 9),
        ]);

        var assembly = Assert.Single(await QueryAsync(
            "SELECT created_at FROM assemblies WHERE serial = 'A-00000090'"));
        Assert.Equal(DBNull.Value, assembly["created_at"]);
    }

    [Fact]
    public async Task ACreationEventFillsInTheStubAnEarlierStationLeft()
    {
        // The other half: the horizon row is a row still being filled in, not a dead end.
        // A backfill that reaches further back on a later run — or a window read out of
        // order — completes it in place rather than leaving two truths about one part.
        await SeedTopologyAsync();
        await _writer.WriteBatchAsync([PartProcessed("A-00000005", Instant, [1.0, 2.0])]);
        await _writer.WriteBatchAsync([
            AssemblyCreated("A-00000005", ["C-1-00000005"], Instant.AddSeconds(-12), carrierId: 4),
        ]);

        var row = Assert.Single(await QueryAsync(
            "SELECT created_at, carrier_id FROM assemblies WHERE serial = 'A-00000005'"));
        Assert.Equal(Instant.AddSeconds(-12), row["created_at"]);
        Assert.Equal((short)4, row["carrier_id"]);
    }

    [Fact]
    public async Task TheCarrierTableFillsFromTheEventsThatNameOne()
    {
        // M2a created carriers empty because §4.1 exposes no carrier node. The assembly and
        // the verdict both carry the id, and both reference the table, so both have to be
        // able to create the row — M2c's scenario 4 groups defects on exactly this column.
        await SeedTopologyAsync();
        await _writer.WriteBatchAsync([
            AssemblyCreated("A-00000006", ["C-1-00000006"], Instant, carrierId: 11),
        ]);
        await _writer.WriteBatchAsync([
            SampleEvent("A-00000007", reject: true, new byte[8], Instant, carrierId: 12),
        ]);

        Assert.Equal(
            [(short)11, (short)12],
            (await QueryAsync("SELECT id FROM carriers ORDER BY id")).Select(r => r["id"]));
    }

    [Fact]
    public async Task ALotIsLoadedAtTheEarliestDrawSeenWhicheverOrderTheDrawsArriveIn()
    {
        // §5.2's component_lots.loaded_at is the instant of the lot's first draw, which is
        // MIN(Time) over its component reads. A backfill subdivides a truncated window and
        // re-reads it from the start, and windows are walked forwards while a reconnect
        // re-reads older ones, so "first seen" is not "earliest".
        await _writer.WriteBatchAsync([
            ComponentRead("C-1-00000010", lane: 1, "L-2400", Instant.AddMinutes(5)),
        ]);
        await _writer.WriteBatchAsync([
            ComponentRead("C-1-00000011", lane: 1, "L-2400", Instant),
        ]);
        await _writer.WriteBatchAsync([
            ComponentRead("C-1-00000012", lane: 1, "L-2400", Instant.AddMinutes(9)),
        ]);

        var row = Assert.Single(await QueryAsync(
            "SELECT loaded_at, depleted_at FROM component_lots WHERE lot_code = 'L-2400'"));
        Assert.Equal(Instant, row["loaded_at"]);
        Assert.Equal(DBNull.Value, row["depleted_at"]);
    }

    [Fact]
    public async Task TwoLanesOnTheSameLotCodeAreTwoLots()
    {
        // §5.2 keys component_lots on (lot_code, lane). The plant issues lot codes from one
        // counter shared by both lanes so the pair never collides today, but a plant that
        // reused a code on the other lane would otherwise put two lanes' components on one
        // lot row and make lane-level containment unanswerable.
        await _writer.WriteBatchAsync([
            ComponentRead("C-1-00000020", lane: 1, "L-2500", Instant),
            ComponentRead("C-2-00000020", lane: 2, "L-2500", Instant),
        ]);

        Assert.Equal(2, await CountAsync("component_lots"));
    }

    [Fact]
    public async Task RepeatedComponentReadsDoNotAdvanceTheLotSequence()
    {
        // component_lots.id is a SMALLSERIAL and a history holds ~39,600 component reads
        // against ~80 lots. ON CONFLICT evaluates nextval before it detects the
        // conflict, so an upsert per record would burn the 32,767 range and fail every write
        // after it — the same defect M1 measured on stations at 55,956 records.
        for (var i = 0; i < 200; i++)
        {
            await _writer.WriteBatchAsync([
                ComponentRead($"C-1-{i:D8}", lane: 1, "L-2600", Instant.AddSeconds(i)),
            ]);
        }

        Assert.Equal(1, await CountAsync("component_lots"));

        await using var connection = new NpgsqlConnection(_postgres.GetConnectionString());
        await connection.OpenAsync();
        await using var command = new NpgsqlCommand(
            "SELECT last_value FROM component_lots_id_seq", connection);
        Assert.True(
            Convert.ToInt64(await command.ExecuteScalarAsync()) <= 2,
            "the lot id sequence advanced per component read");
    }

    [Fact]
    public async Task ALotLearnedByAFailedBatchIsNotTakenOnTrustByTheRetry()
    {
        // The cache rule, one table further on. The id comes from an INSERT inside the open
        // transaction and SMALLSERIAL does not roll back with it, so a cached id would name a
        // row that no longer exists and every later component read would fail its foreign key
        // against a component_lots table that never had it.
        await Assert.ThrowsAnyAsync<Exception>(() => _writer.WriteBatchAsync([
            ComponentRead("C-1-00000030", lane: 1, "L-2700", Instant),
            MalformedEvent("S3"),
        ]));

        await _writer.WriteBatchAsync([ComponentRead("C-1-00000030", lane: 1, "L-2700", Instant)]);

        Assert.Equal(1, await CountAsync("component_lots"));
        Assert.Equal(1, await CountAsync("components"));
    }

    [Fact]
    public async Task ThePressCurveIsOneRowOfSamplesRatherThanOneRowPerSample()
    {
        // D6: the curve, not the two scalars, is what separates a press problem from a
        // material problem — and the plant samples it 51 times per part, which as rows would
        // be 51 per part against one time series row.
        await SeedTopologyAsync();
        var curve = Enumerable.Range(0, 51).Select(i => i * 82.3).ToArray();

        await _writer.WriteBatchAsync([PartProcessed("A-00000040", Instant, curve)]);

        var row = Assert.Single(await QueryAsync(
            "SELECT samples FROM part_process_curves WHERE assembly_serial = 'A-00000040'"));
        Assert.Equal(curve, (double[])row["samples"]);

        var values = await QueryAsync(
            "SELECT signal, value FROM part_process_values "
            + "WHERE assembly_serial = 'A-00000040' ORDER BY signal");
        Assert.Equal(["JoiningDistance", "PeakForce"], values.Select(v => v["signal"]));
    }

    [Fact]
    public async Task TheVerdictStoresEveryClassScoreIncludingOnAGoodPart()
    {
        // §3.4 needs six independent scores that do not sum to 1, on every part. A good part
        // is six low scores rather than an absent vector, and scenario 6's "confidence decayed
        // across all classes" is only a question the history can answer if the low ones are
        // recorded too.
        await SeedTopologyAsync();
        await _writer.WriteBatchAsync([
            SampleEvent("A-00000050", reject: false, image: null, Instant, carrierId: 5),
        ]);

        var row = Assert.Single(await QueryAsync(
            "SELECT defect_classes, confidences, confidence, carrier_id, defect_class "
            + "FROM inspection_results WHERE assembly_serial = 'A-00000050'"));

        Assert.Equal(6, ((string[])row["defect_classes"]).Length);
        Assert.Equal(6, ((double[])row["confidences"]).Length);
        Assert.Equal(0.87, row["confidence"]);
        Assert.Equal((short)5, row["carrier_id"]);

        // M1's scalar column, deliberately no longer written: the widened event carries no
        // single class, and an argmax taken here would be this gateway deciding which defect
        // a part has. Task 7 moves /inspection/stats off it.
        Assert.Equal(DBNull.Value, row["defect_class"]);
    }

    [Fact]
    public async Task OnlyARejectLeavesTheLineWithAReason()
    {
        // The plant sends an empty reason for a good part because the field is not optional
        // on the wire. Stored as text it would make every good part look like a condition
        // with a nameless cause — the same claim the empty StateReason makes one table over.
        await SeedTopologyAsync();
        await _writer.WriteBatchAsync([
            PartCompleted("A-00000060", "good", reason: null, Instant),
            PartCompleted("A-00000061", "reject", "misalignment", Instant),
        ]);

        var rows = await QueryAsync(
            "SELECT assembly_serial, disposition, reason FROM part_dispositions "
            + "ORDER BY assembly_serial");
        Assert.Equal(DBNull.Value, rows[0]["reason"]);
        Assert.Equal("misalignment", rows[1]["reason"]);
    }

    [Fact]
    public async Task AnEventTypeWithNoWritePathFailsRatherThanIngestingIntoNothing()
    {
        // A plant that gains a sixth event type must not be ingested into raw_events alone
        // while /reconcile counts it read and every derived table stays empty. Loud is the
        // only honest answer, and it is the answer the whole ingest path gives.
        var unknown = new IngestRecord(
            Kind: "event", NodeId: "ns=2;i=9", SourceTs: Instant, ServerTs: Instant,
            StatusCode: 0,
            PayloadJson: """{"Station":"S3","EventType":"ToolChangeEventType"}""",
            ImageBytes: null);

        var exception = await Assert.ThrowsAsync<InvalidOperationException>(
            () => _writer.WriteBatchAsync([unknown]));

        Assert.Contains("ToolChangeEventType", exception.Message, StringComparison.Ordinal);
        Assert.Equal(0, await CountAsync("raw_events"));
    }

    [Fact]
    public async Task ACarrierIdTooLargeForItsColumnIsRefusedRatherThanWrapped()
    {
        // The node is UInt32 and the column is SMALLINT. A plain narrowing cast turns 70,000
        // into a negative carrier id just as quietly as it turned a large buffer capacity
        // into a negative one, and M2c's scenario 4 groups defects on exactly this column.
        var record = new IngestRecord(
            Kind: "event", NodeId: "ns=2;i=5", SourceTs: Instant, ServerTs: Instant,
            StatusCode: 0,
            PayloadJson: """
                {"Station":"S1","EventType":"AssemblyCreatedEventType",
                 "AssemblySerial":"A-00000070","ComponentSerials":[],"CarrierId":70000}
                """,
            ImageBytes: null);

        var exception = await Assert.ThrowsAsync<InvalidOperationException>(
            () => _writer.WriteBatchAsync([record]));

        Assert.Contains("70000", exception.Message, StringComparison.Ordinal);
    }

    [Fact]
    public async Task TheThreeAlarmEventsBecomeOneRowWithItsThreeInstants()
    {
        // §4.2's lifecycle, as §5.2's row. Which transition an event is comes from the two
        // flags rather than from which columns are filled, so all three carry the same
        // fields and differ only in what those flags say.
        await SeedTopologyAsync();
        var raised = Instant;
        var acked = Instant.AddSeconds(150);
        var cleared = Instant.AddSeconds(153);

        await _writer.WriteBatchAsync(
        [
            AlarmEvent("S2", raised, raised, active: true, acknowledged: false),
            AlarmEvent("S2", raised, acked, active: true, acknowledged: true),
            AlarmEvent("S2", raised, cleared, active: false, acknowledged: true),
        ]);

        var rows = await QueryAsync(
            """
            SELECT s.code, a.code AS alarm, a.text, a.severity, a.raised_at, a.acked_at,
                   a.cleared_at
            FROM alarms a JOIN stations s ON s.id = a.station_id
            """);
        var row = Assert.Single(rows);
        Assert.Equal("S2", row["code"]);
        Assert.Equal("A-207", row["alarm"]);
        Assert.Equal("joining force out of tolerance", row["text"]);
        Assert.Equal((short)700, row["severity"]);
        Assert.Equal(raised, row["raised_at"]);
        Assert.Equal(acked, row["acked_at"]);
        Assert.Equal(cleared, row["cleared_at"]);
    }

    [Fact]
    public async Task AnAcknowledgementThatArrivesBeforeItsOwnRaiseStillLands()
    {
        // Not hypothetical: the history horizon cuts an alarm in half on any boot, and a
        // truncated window is halved and re-read from its start. Every event carries
        // AlarmRaisedAt so whichever arrives first creates the row -- 003_m2b.sql's rule for
        // genealogy, applied to a lifecycle. An UPDATE against a row that is not there yet
        // would drop the acknowledgement with nothing raised.
        await SeedTopologyAsync();
        var raised = Instant;

        await _writer.WriteBatchAsync(
            [AlarmEvent("S2", raised, Instant.AddSeconds(150), active: true, acknowledged: true)]);

        var row = Assert.Single(await QueryAsync(
            "SELECT raised_at, acked_at, cleared_at FROM alarms"));
        Assert.Equal(raised, row["raised_at"]);
        Assert.Equal(Instant.AddSeconds(150), row["acked_at"]);
        Assert.Equal(DBNull.Value, row["cleared_at"]);
    }

    [Fact]
    public async Task AClearDoesNotBackdateAnAcknowledgementItOnlyReports()
    {
        // The clear reports the alarm as acknowledged, because that is the state it is in --
        // but its own Time is when the operator finished, not when they arrived. Taking
        // acked_at from it would stamp the acknowledgement minutes late; leaving it null is
        // the true statement that this gateway never saw it, exactly as a null created_at is
        // for an assembly made before the horizon.
        await SeedTopologyAsync();

        await _writer.WriteBatchAsync(
            [AlarmEvent("S2", Instant, Instant.AddSeconds(153), active: false, acknowledged: true)]);

        var row = Assert.Single(await QueryAsync("SELECT acked_at, cleared_at FROM alarms"));
        Assert.Equal(DBNull.Value, row["acked_at"]);
        Assert.Equal(Instant.AddSeconds(153), row["cleared_at"]);
    }

    [Fact]
    public async Task ARepeatedAlarmOnOneStationIsTwoRowsAndADuplicateIsOne()
    {
        // Both halves of the key. A press nothing repaired raises again after every restart,
        // and those are separate alarms with separate instants -- keyed on (station, code)
        // alone, the second would silently overwrite the first's lifecycle. A page boundary
        // re-delivering the same raise is the other direction and must write nothing.
        await SeedTopologyAsync();
        var first = AlarmEvent("S2", Instant, Instant, active: true, acknowledged: false);

        await _writer.WriteBatchAsync([first]);
        await _writer.WriteBatchAsync([first]);
        await _writer.WriteBatchAsync(
        [
            AlarmEvent("S2", Instant.AddSeconds(600), Instant.AddSeconds(600),
                active: true, acknowledged: false),
        ]);

        Assert.Equal(2, await CountAsync("alarms"));
        Assert.Equal(3, await CountAsync("raw_events"));   // raw is append-only and verbatim
    }

    [Fact]
    public async Task ASeverityOutsideTheOpcUaBandIsRefusedRatherThanStored()
    {
        // SMALLINT would take 40,000 as -25,536 and the row would look like an alarm.
        // §5.1: the write path fails loudly rather than storing a plausible wrong number.
        await SeedTopologyAsync();

        await Assert.ThrowsAnyAsync<Exception>(() => _writer.WriteBatchAsync(
        [
            AlarmEvent("S2", Instant, Instant, active: true, acknowledged: false,
                severity: 40_000),
        ]));

        Assert.Equal(0, await CountAsync("alarms"));
        Assert.Equal(0, await CountAsync("raw_events"));
    }

    private static IngestRecord SampleDataChange(string signal, DateTime ts, double value) => new(
        Kind: "datachange", NodeId: "ns=2;i=7", SourceTs: ts, ServerTs: ts, StatusCode: 0,
        PayloadJson: $$"""{"Station":"S3","Signal":"{{signal}}","Value":{{value}}}""",
        ImageBytes: null);

    private static IngestRecord SampleEvent(string serial, bool reject, byte[]? image) =>
        SampleEvent(serial, reject, image, Instant, carrierId: 7);

    /// <summary>
    /// §3.4's verdict at D11's widened shape: six parallel class scores on every event,
    /// good ones included, and the scalar verdict confidence beside them.
    /// </summary>
    private static IngestRecord SampleEvent(
        string serial, bool reject, byte[]? image, DateTime at, int carrierId) => new(
        Kind: "event", NodeId: "ns=2;i=9", SourceTs: at, ServerTs: at, StatusCode: 0,
        PayloadJson: $$"""
            {"Station":"S3","EventType":"InspectionResultEventType",
             "AssemblySerial":"{{serial}}","CarrierId":{{carrierId}},
             "Disposition":"{{(reject ? "reject" : "good")}}",
             "DefectClasses":["gap","crack","misalignment","missing_part","scratch","contamination"],
             "Confidences":{{(reject
                 ? "[0.91,0.04,0.07,0.02,0.05,0.03]"
                 : "[0.04,0.02,0.03,0.01,0.05,0.02]")}},
             "Confidence":0.87,"ModelVersion":"sim-1"}
            """,
        ImageBytes: image);

    /// <summary>
    /// An inspection event with no assembly serial — the one column inspection_results keys
    /// on. A genuinely malformed record rather than a test-only failure switch, so a test
    /// using it cannot pass while the real failure path is broken.
    /// </summary>
    private static IngestRecord MalformedEvent(string station) => new(
        Kind: "event", NodeId: "ns=2;i=9", SourceTs: Instant, ServerTs: Instant, StatusCode: 0,
        PayloadJson:
            $$"""{"Station":"{{station}}","EventType":"InspectionResultEventType","ModelVersion":"sim-1"}""",
        ImageBytes: null);

    /// <summary>One component off one lane, and the lot it came from.</summary>
    private static IngestRecord ComponentRead(
        string serial, int lane, string lotCode, DateTime at, string supplier = "SUP-01") => new(
        Kind: "event", NodeId: "ns=2;i=5", SourceTs: at, ServerTs: at, StatusCode: 0,
        PayloadJson: $$"""
            {"Station":"S1","EventType":"ComponentReadEventType","ComponentSerial":"{{serial}}",
             "Lane":{{lane}},"LotCode":"{{lotCode}}","Supplier":"{{supplier}}"}
            """,
        ImageBytes: null);

    /// <summary>The assembly S1 creates, and the components it was built from, as built.</summary>
    private static IngestRecord AssemblyCreated(
        string serial, IReadOnlyList<string> components, DateTime at, int carrierId = 7) => new(
        Kind: "event", NodeId: "ns=2;i=5", SourceTs: at, ServerTs: at, StatusCode: 0,
        PayloadJson: $$"""
            {"Station":"S1","EventType":"AssemblyCreatedEventType","AssemblySerial":"{{serial}}",
             "ComponentSerials":[{{string.Join(",", components.Select(c => $"\"{c}\""))}}],
             "CarrierId":{{carrierId}}}
            """,
        ImageBytes: null);

    /// <summary>§3.4a's press record, against the serial.</summary>
    private static IngestRecord PartProcessed(
        string serial, DateTime at, IReadOnlyList<double> curve) => new(
        Kind: "event", NodeId: "ns=2;i=6", SourceTs: at, ServerTs: at, StatusCode: 0,
        PayloadJson: $$"""
            {"Station":"S2","EventType":"PartProcessedEventType","AssemblySerial":"{{serial}}",
             "Curve":[{{string.Join(",", curve.Select(v => v.ToString("R", CultureInfo.InvariantCulture)))}}],
             "PeakForce":4187.4,"JoiningDistance":8.012}
            """,
        ImageBytes: null);

    /// <summary>How the part left the line, and why.</summary>
    private static IngestRecord PartCompleted(
        string serial, string disposition, string? reason, DateTime at) => new(
        Kind: "event", NodeId: "ns=2;i=8", SourceTs: at, ServerTs: at, StatusCode: 0,
        PayloadJson: $$"""
            {"Station":"S4","EventType":"PartCompletedEventType","AssemblySerial":"{{serial}}",
             "Disposition":"{{disposition}}"{{(reason is null ? "" : $",\"Reason\":\"{reason}\"")}}}
            """,
        ImageBytes: null);

    /// <summary>
    /// One of §4.2's three alarm lifecycle events. `at` is the instant of this transition;
    /// `raisedAt` is the alarm's identity and rides all three.
    /// </summary>
    private static IngestRecord AlarmEvent(
        string station, DateTime raisedAt, DateTime at, bool active, bool acknowledged,
        string code = "A-207", int severity = 700) => new(
        Kind: "event", NodeId: $"ns=2;s={station}", SourceTs: at, ServerTs: at, StatusCode: 0,
        PayloadJson: $$"""
            {"Station":"{{station}}","EventType":"AlarmEventType","AlarmCode":"{{code}}",
             "AlarmText":"joining force out of tolerance","AlarmSeverity":{{severity}},
             "AlarmRaisedAt":"{{raisedAt:O}}","AlarmActive":{{(active ? "true" : "false")}},
             "AlarmAcknowledged":{{(acknowledged ? "true" : "false")}}}
            """,
        ImageBytes: null);

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
