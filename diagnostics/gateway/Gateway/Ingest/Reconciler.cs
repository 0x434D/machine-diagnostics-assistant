using Gateway.Opc;
using Npgsql;

namespace Gateway.Ingest;

public sealed record StreamReconciliation(
    string Stream, int RowsReturned, int RowsStored, int Pages, int Windows)
{
    /// <summary>
    /// What the historian handed over beyond what was stored. F2: the continuation point is
    /// the SourceTimestamp of the first row of the next page and the next query re-includes
    /// it, so roughly one duplicate arrives per page boundary. The upsert absorbs them.
    /// </summary>
    public int DuplicatesAbsorbed => RowsReturned - RowsStored;

    /// <summary>
    /// Duplicates F2 alone explains: one per page boundary, and a window's final page has no
    /// boundary after it.
    /// </summary>
    public int ExpectedFromPageBoundaries => Math.Max(0, Pages - Windows);

    /// <summary>
    /// Rows the backfill reported reading that are not stored and that page boundaries do not
    /// explain. This is the number R1 is about: read and lost, as opposed to read twice.
    /// </summary>
    public int Lost => Math.Max(0, DuplicatesAbsorbed - ExpectedFromPageBoundaries);
}

public sealed record ReconciliationResult(
    DateTime From, DateTime To, IReadOnlyList<StreamReconciliation> Streams,
    IReadOnlyList<IngestGap> Gaps)
{
    /// <summary>
    /// Nothing was read and lost, and no window is recorded as missing.
    ///
    /// Only the loss direction is judged. A stream storing MORE than the backfill ledger
    /// accounts for is the normal state of a running gateway — the live subscription writes
    /// rows that no backfill window ever claimed — so over any window that includes live
    /// ingest, and that is every window a running gateway is asked about, a surplus carries
    /// no information about whether data is missing. Asserting on it reported a healthy
    /// gateway as unreconciled on the first real request this endpoint served.
    /// </summary>
    public bool Reconciled => Gaps.Count == 0 && Streams.All(stream => stream.Lost == 0);
}

/// <summary>A window the gateway knows it does not have (§4.4).</summary>
public sealed record IngestGap(DateTime From, DateTime To, string Reason);

/// <summary>
/// Compares what the backfill reported pulling against what is actually stored.
///
/// Deliberately not compared here: the plant's own generator ledger. R1's wording names the
/// count "in the plant historian per stream, from the simulator's own generator ledger",
/// which is two different quantities — the ledger counts writes attempted and the historian
/// holds what survived, and asyncua's internal subscription queue caps at 10,000 and discards
/// the oldest. Two Task 4 tests passed through a 9,800-row loss by asserting only the ledger.
/// Reading the plant's status would also mean a second protocol between the stacks, which
/// §4.5 forbids; that three-way comparison belongs to the measurement runner, outside both.
/// </summary>
public sealed class Reconciler
{
    private readonly string _connectionString;

    public Reconciler(string connectionString) => _connectionString = connectionString;

    public async Task<ReconciliationResult> CheckAsync(
        DateTime from, DateTime to, CancellationToken ct = default)
    {
        await using var connection = new NpgsqlConnection(_connectionString);
        await connection.OpenAsync(ct).ConfigureAwait(false);

        // Every stream the ledger names, rather than a list stated here. A list would be a
        // second enumeration of a discovered topology, and the way it fails is the worst one
        // available: a stream nobody reconciles is a stream that reports "reconciled" while
        // it holds nothing, which is exactly what naming M1's three streams did the moment
        // the ledger started qualifying them by station.
        var streams = new List<StreamReconciliation>();
        foreach (var ledger in await LedgerAsync(connection, from, to, ct).ConfigureAwait(false))
        {
            streams.Add(new StreamReconciliation(
                ledger.Stream,
                ledger.Returned,
                await StoredAsync(connection, ledger.Stream, from, to, ct).ConfigureAwait(false),
                ledger.Pages,
                ledger.Windows));
        }

        return new ReconciliationResult(
            from, to, streams,
            await GapsAsync(connection, from, to, ct).ConfigureAwait(false));
    }

    private static async Task<IReadOnlyList<IngestGap>> GapsAsync(
        NpgsqlConnection connection, DateTime from, DateTime to, CancellationToken ct)
    {
        // Overlap rather than containment: a gap that starts before the window and ends
        // inside it is still a hole in the window, and asking for containment is how one
        // goes unreported.
        await using var command = new NpgsqlCommand(
            "SELECT from_ts, to_ts, reason FROM ingest_gaps "
            + "WHERE from_ts < $2 AND to_ts > $1 ORDER BY from_ts",
            connection);
        command.Parameters.AddWithValue(from);
        command.Parameters.AddWithValue(to);

        var gaps = new List<IngestGap>();
        await using var reader = await command.ExecuteReaderAsync(ct).ConfigureAwait(false);
        while (await reader.ReadAsync(ct).ConfigureAwait(false))
        {
            gaps.Add(new IngestGap(
                reader.GetDateTime(0), reader.GetDateTime(1), reader.GetString(2)));
        }

        return gaps;
    }

    /// <summary>
    /// How many rows one stream's values are stored as, in the table
    /// <see cref="PostgresWriter"/> derives that stream into. The routing below mirrors the
    /// writer's, because a count taken from a different table than the one the rows went into
    /// is not a reconciliation of anything.
    /// </summary>
    /// <exception cref="InvalidOperationException">
    /// the ledger row names a stream with no owner. Every row this gateway writes is
    /// <c>code.signal</c>; an unqualified one predates that and cannot be attributed to a
    /// station, so it is refused rather than counted against an arbitrary one.
    /// </exception>
    private static async Task<int> StoredAsync(
        NpgsqlConnection connection, string stream, DateTime from, DateTime to,
        CancellationToken ct)
    {
        var separator = stream.IndexOf('.', StringComparison.Ordinal);
        if (separator <= 0 || separator == stream.Length - 1)
        {
            throw new InvalidOperationException(
                $"backfill_windows names a stream '{stream}' with no owner; it predates the "
                + "station qualifier and cannot be reconciled against one station's rows");
        }

        var code = stream[..separator];
        var signal = stream[(separator + 1)..];

        var query = signal switch
        {
            // raw_events, and this is the second stream that has to be counted there. One
            // event is no longer one derived row: S1's three events per part become two
            // component rows, one assembly row and two genealogy rows across three tables,
            // and S2's one event becomes two values and a curve. No derived table is 1:1
            // with an event stream any more, so counting one of them would report a loss or
            // a surplus that is an artefact of which table was picked.
            //
            // raw_events is verbatim and InsertRawAsync runs before any derivation, so every
            // event the read handed over is there. DISTINCT on the payload rather than on
            // the timestamp because S1 emits three events at one instant and a page boundary
            // re-delivers one of them byte for byte.
            Subscriptions.EventStream => new StoredQuery(
                """
                SELECT count(DISTINCT payload) FROM raw_events
                WHERE kind = 'event' AND payload->>'Station' = $1
                  AND source_ts >= $2 AND source_ts < $3
                """,
                new object[] { code, from, to }),

            Subscriptions.BufferLevelSignal => new StoredQuery(
                """
                SELECT count(*) FROM buffer_levels l
                JOIN buffers b ON b.id = l.buffer_id
                WHERE b.code = $1 AND l.source_ts >= $2 AND l.source_ts < $3
                """,
                new object[] { code, from, to }),

            // One State value is one settled transition, so this is exact. The view is what
            // "settled" means, stated once (§5.2), rather than a `to_state IS NOT NULL`
            // repeated in every query that has to remember it.
            PostgresWriter.StateSignal => new StoredQuery(
                """
                SELECT count(*) FROM state_changes_settled c
                JOIN stations s ON s.id = c.station_id
                WHERE s.code = $1 AND c.source_ts >= $2 AND c.source_ts < $3
                """,
                new object[] { code, from, to }),

            // raw_events, not state_changes, and this is the one stream that has to be counted
            // there. A StateReason value is not a row of its own: it is a column on the row its
            // paired State keys, and the empty one a station publishes when it stops being
            // suspended is deliberately stored as the NULL that already says so. Counting rows
            // carrying text reports every unsuspend as lost; counting the station's rows counts
            // what State put there, so Returned == Stored identically and the check cannot fire
            // under any input at all -- a row on /reconcile that can only ever say "fine".
            //
            // raw_events is verbatim and InsertRawAsync runs before the empty-reason return, so
            // every value the read handed over is there. DISTINCT on the timestamp because a
            // page boundary re-delivers one and raw_events is append-only.
            PostgresWriter.StateReasonSignal => new StoredQuery(
                """
                SELECT count(DISTINCT source_ts) FROM raw_events
                WHERE kind = 'datachange'
                  AND payload->>'Station' = $1 AND payload->>'Signal' = $2
                  AND source_ts >= $3 AND source_ts < $4
                """,
                new object[] { code, signal, from, to }),

            _ => new StoredQuery(
                """
                SELECT count(*) FROM signals g
                JOIN stations s ON s.id = g.station_id
                WHERE s.code = $1 AND g.signal = $2 AND g.source_ts >= $3 AND g.source_ts < $4
                """,
                new object[] { code, signal, from, to }),
        };

        await using var command = new NpgsqlCommand(query.Sql, connection);
        foreach (var parameter in query.Parameters)
        {
            command.Parameters.AddWithValue(parameter);
        }

        return Convert.ToInt32(await command.ExecuteScalarAsync(ct).ConfigureAwait(false));
    }

    private static async Task<IReadOnlyList<LedgerEntry>> LedgerAsync(
        NpgsqlConnection connection, DateTime from, DateTime to, CancellationToken ct)
    {
        await using var command = new NpgsqlCommand(
            "SELECT stream, coalesce(sum(rows_returned), 0), coalesce(sum(pages), 0), count(*) "
            + "FROM backfill_windows WHERE from_ts >= $1 AND to_ts <= $2 "
            + "GROUP BY stream ORDER BY stream",
            connection);
        command.Parameters.AddWithValue(from);
        command.Parameters.AddWithValue(to);

        var entries = new List<LedgerEntry>();
        await using var reader = await command.ExecuteReaderAsync(ct).ConfigureAwait(false);
        while (await reader.ReadAsync(ct).ConfigureAwait(false))
        {
            entries.Add(new LedgerEntry(
                reader.GetString(0),
                Convert.ToInt32(reader.GetValue(1)),
                Convert.ToInt32(reader.GetValue(2)),
                Convert.ToInt32(reader.GetValue(3))));
        }

        return entries;
    }

    private sealed record LedgerEntry(string Stream, int Returned, int Pages, int Windows);

    private sealed record StoredQuery(string Sql, object[] Parameters);
}
