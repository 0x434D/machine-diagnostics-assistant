using System.Collections.Concurrent;
using System.Reflection;
using System.Text.Json;
using Gateway.Opc;
using Npgsql;
using NpgsqlTypes;

namespace Gateway.Ingest;

/// <summary>
/// The single write path (§5.1): every record lands verbatim in raw_events and its derived
/// rows are written in the same transaction. Both or neither — a raw row whose derivation
/// failed would be a silent divergence between what arrived and what the analysis can see.
/// </summary>
public sealed class PostgresWriter
{
    /// <summary>
    /// The signal names that are not numbers. §5.2 gives each of them its own table because
    /// signals.value is DOUBLE PRECISION and a buffer level belongs to a buffer.
    /// </summary>
    internal const string StateSignal = "State";
    internal const string StateReasonSignal = "StateReason";

    // Ordered, and applied in this order: 002 references stations, which 001 creates. Every
    // statement in both is IF NOT EXISTS, so applying them to an existing database is a no-op.
    private static readonly string[] MigrationResources =
    [
        "Gateway.Migrations.001_m1.sql",
        "Gateway.Migrations.002_m2a.sql",
    ];

    private readonly string _connectionString;

    // The three caches below hold only what a committed transaction put there. QueueDrain
    // retries a failed batch with nothing acked, so anything an attempt remembered is read
    // again by the retry — a cache that advanced on the attempt makes the retry write
    // something the plant never sent. Each batch therefore fills its own dictionary and
    // promotes it after CommitAsync.

    // One station in M1 and four in M2, resolved once each. Without this the lookup runs per
    // record, which is both a round trip per row and — with the ON CONFLICT DO UPDATE this
    // replaced — a sequence value burned per row.
    private readonly ConcurrentDictionary<string, short> _stationIds = new(StringComparer.Ordinal);

    // Hits only, and safe to fill mid-batch: a buffer row is written by topology discovery in
    // its own transaction, so anything this reads is already committed. A code that resolved
    // to nothing is re-queried, because a suspend reason can name a buffer discovery has not
    // written yet and a cached miss would keep it unresolvable for the life of the process.
    private readonly ConcurrentDictionary<string, short> _bufferIds = new(StringComparer.Ordinal);

    // A data change carries only the new value, so from_state can only come from memory.
    // Empty after a connect, which is why the first transition seen writes a null from_state.
    //
    // The SourceTimestamp is remembered with it, and that is not bookkeeping. A backfill
    // window whose page comes back full and silent is halved and re-read from its start, so
    // this memory is routinely asked about a row that precedes what it holds -- and a memory
    // standing at row 1,000 answering for row 1 invents a transition the plant never made.
    private readonly ConcurrentDictionary<string, SeenState> _lastStates =
        new(StringComparer.Ordinal);

    public PostgresWriter(string connectionString) => _connectionString = connectionString;

    public static async Task ApplySchemaAsync(
        string connectionString, CancellationToken ct = default)
    {
        await using var connection = new NpgsqlConnection(connectionString);
        await connection.OpenAsync(ct).ConfigureAwait(false);
        foreach (var resource in MigrationResources)
        {
            await using var command = new NpgsqlCommand(ReadMigration(resource), connection);
            await command.ExecuteNonQueryAsync(ct).ConfigureAwait(false);
        }
    }

    /// <summary>
    /// Where this gateway's own storage ends, or null if it holds nothing. §4.3: backfill
    /// closes exactly the gap it has — after first boot, after a crash, after an outage.
    /// One mechanism, three situations.
    /// </summary>
    public async Task<DateTime?> LastStoredSourceTimestampAsync(CancellationToken ct = default)
    {
        await using var connection = new NpgsqlConnection(_connectionString);
        await connection.OpenAsync(ct).ConfigureAwait(false);
        await using var command = new NpgsqlCommand(
            "SELECT max(source_ts) FROM raw_events", connection);
        var value = await command.ExecuteScalarAsync(ct).ConfigureAwait(false);
        return value is DateTime stored ? stored : null;
    }

    /// <summary>
    /// R1's reconciliation ledger: what the gateway believes it pulled, per window. Recorded
    /// from the backfill's own report rather than recomputed, so the two cannot disagree.
    /// </summary>
    public async Task RecordBackfillWindowAsync(
        DateTime from, DateTime to, string stream, int rowsReturned, int pages, int durationMs,
        CancellationToken ct = default)
    {
        await using var connection = new NpgsqlConnection(_connectionString);
        await connection.OpenAsync(ct).ConfigureAwait(false);
        await using var command = new NpgsqlCommand(
            """
            INSERT INTO backfill_windows
              (from_ts, to_ts, stream, rows_returned, rows_written, pages, duration_ms)
            VALUES ($1, $2, $3, $4, 0, $5, $6)
            ON CONFLICT (from_ts, to_ts, stream) DO UPDATE
              SET rows_returned = EXCLUDED.rows_returned,
                  pages = EXCLUDED.pages,
                  duration_ms = EXCLUDED.duration_ms
            """, connection);
        command.Parameters.AddWithValue(from);
        command.Parameters.AddWithValue(to);
        command.Parameters.AddWithValue(stream);
        command.Parameters.AddWithValue(rowsReturned);
        command.Parameters.AddWithValue(pages);
        command.Parameters.AddWithValue(durationMs);
        await command.ExecuteNonQueryAsync(ct).ConfigureAwait(false);
    }

    /// <summary>
    /// Every stream this gateway has ever recorded a backfill window for.
    ///
    /// <para>The only record of what this plant published before, and therefore the only thing
    /// a fresh discovery can be held against. Discovery cannot tell a 25-stream plant from a
    /// 26-stream plant that lost one, and the shorter run is green all the way to
    /// <c>/reconcile</c>.</para>
    /// </summary>
    public async Task<IReadOnlySet<string>> KnownBackfillStreamsAsync(
        CancellationToken ct = default)
    {
        await using var connection = new NpgsqlConnection(_connectionString);
        await connection.OpenAsync(ct).ConfigureAwait(false);
        await using var command = new NpgsqlCommand(
            "SELECT DISTINCT stream FROM backfill_windows", connection);

        var streams = new HashSet<string>(StringComparer.Ordinal);
        await using var reader = await command.ExecuteReaderAsync(ct).ConfigureAwait(false);
        while (await reader.ReadAsync(ct).ConfigureAwait(false))
        {
            streams.Add(reader.GetString(0));
        }

        return streams;
    }

    /// <returns>Rows affected across every table this batch touched.</returns>
    public async Task<int> WriteBatchAsync(
        IReadOnlyList<IngestRecord> batch, CancellationToken ct = default)
    {
        ArgumentNullException.ThrowIfNull(batch);

        await using var connection = new NpgsqlConnection(_connectionString);
        await connection.OpenAsync(ct).ConfigureAwait(false);
        await using var transaction = await connection.BeginTransactionAsync(ct).ConfigureAwait(false);

        // What this batch learns, kept out of the shared caches until the batch has earned it.
        // Read through, so two records for one station inside a batch still see each other.
        var batchStationIds = new Dictionary<string, short>(StringComparer.Ordinal);
        var batchStates = new Dictionary<string, SeenState>(StringComparer.Ordinal);

        var rows = 0;
        foreach (var record in batch)
        {
            rows += await InsertRawAsync(connection, record, ct).ConfigureAwait(false);

            using var document = JsonDocument.Parse(record.PayloadJson);
            var payload = document.RootElement;

            // Before the station lookup, because ingest_gaps is not station-scoped: §4.4's
            // claim is about the window, and a gap that had to name a station would be a
            // gap the schema cannot express when the whole connection is what was lost.
            if (record.Kind == "gap")
            {
                rows += await InsertGapAsync(connection, record, payload, ct).ConfigureAwait(false);
                continue;
            }

            var signal = record.Kind == "datachange"
                ? Required(payload, "Signal").GetString()
                : null;

            // Also before the station lookup, and for the same shape of reason: a buffer sits
            // between two stations and belongs to neither, so a level record names no station
            // and asking it for one would force it to pick a side.
            if (signal == Subscriptions.BufferLevelSignal)
            {
                rows += await UpsertBufferLevelAsync(connection, record, payload, ct)
                    .ConfigureAwait(false);
                continue;
            }

            var stationId = await EnsureStationAsync(
                connection, Required(payload, "Station").GetString()!, batchStationIds, ct)
                .ConfigureAwait(false);

            rows += record.Kind switch
            {
                "datachange" => signal switch
                {
                    // State and StateReason are strings; signals.value is DOUBLE PRECISION.
                    // They are not a special case of a numeric signal, they are a different
                    // stream.
                    StateSignal or StateReasonSignal => await UpsertStateChangeAsync(
                        connection, record, payload, stationId, batchStates, ct)
                        .ConfigureAwait(false),
                    _ => await UpsertSignalAsync(connection, record, payload, stationId, ct)
                        .ConfigureAwait(false),
                },
                "event" => await UpsertInspectionAsync(connection, record, payload, stationId, ct)
                    .ConfigureAwait(false),
                _ => 0,
            };
        }

        await transaction.CommitAsync(ct).ConfigureAwait(false);

        // Only now. Before the commit these are claims about rows that may never exist.
        foreach (var (code, id) in batchStationIds)
        {
            _stationIds[code] = id;
        }

        foreach (var (code, seen) in batchStates)
        {
            _lastStates[code] = seen;
        }

        return rows;
    }

    /// <summary>
    /// §4.4: without gap markers, missing data is indistinguishable from a quiet machine.
    /// This is the row that makes the difference, and it travels through the local queue
    /// like every other record — a gap written straight to Postgres would be lost during a
    /// Postgres outage, which is one of the two times a gap is worth having.
    /// </summary>
    private static async Task<int> InsertGapAsync(
        NpgsqlConnection connection, IngestRecord record, JsonElement payload,
        CancellationToken ct)
    {
        await using var command = new NpgsqlCommand(
            "INSERT INTO ingest_gaps (from_ts, to_ts, reason) VALUES ($1, $2, $3)", connection);
        command.Parameters.AddWithValue(record.SourceTs);
        command.Parameters.AddWithValue(Required(payload, "To").GetDateTime());
        command.Parameters.AddWithValue(Required(payload, "Reason").GetString()!);
        return await command.ExecuteNonQueryAsync(ct).ConfigureAwait(false);
    }

    /// <summary>
    /// A gap record, ready for the queue. The node id names what was lost so that a reader
    /// of raw_events can tell an overflow on one signal from a whole connection going away.
    /// </summary>
    public static IngestRecord GapRecord(DateTime from, DateTime to, string reason, string nodeId)
    {
        var payload = JsonSerializer.Serialize(new
        {
            To = to,
            Reason = reason,
        });
        return new IngestRecord("gap", nodeId, from, DateTime.UtcNow, 0, payload, null);
    }

    private static async Task<int> InsertRawAsync(
        NpgsqlConnection connection, IngestRecord record, CancellationToken ct)
    {
        await using var command = new NpgsqlCommand(
            """
            INSERT INTO raw_events (source_ts, server_ts, kind, node_id, payload, status_code)
            VALUES ($1, $2, $3, $4, $5, $6)
            """, connection);
        command.Parameters.AddWithValue(record.SourceTs);
        command.Parameters.AddWithValue(record.ServerTs);
        command.Parameters.AddWithValue(record.Kind);
        command.Parameters.AddWithValue(record.NodeId);
        command.Parameters.Add(new NpgsqlParameter
        {
            Value = record.PayloadJson,
            NpgsqlDbType = NpgsqlDbType.Jsonb,
        });
        command.Parameters.AddWithValue((long)record.StatusCode);
        return await command.ExecuteNonQueryAsync(ct).ConfigureAwait(false);
    }

    /// <summary>
    /// Stations are discovered, not configured (§4.1). Task 11 fills name, function and
    /// position by browsing; until then a station is known by the code its signals carry.
    /// </summary>
    private async Task<short> EnsureStationAsync(
        NpgsqlConnection connection, string code, Dictionary<string, short> batchStationIds,
        CancellationToken ct)
    {
        if (batchStationIds.TryGetValue(code, out var pending))
        {
            return pending;
        }

        if (_stationIds.TryGetValue(code, out var cached))
        {
            return cached;
        }

        // Selected before inserting, and ON CONFLICT DO NOTHING rather than DO UPDATE.
        // DO UPDATE evaluates nextval even when the row already exists, so the id sequence
        // advances once per record — measured: 55,956 records exhausted SMALLSERIAL's 32,767
        // range on a table holding one row, and every write then failed.
        var id = await SelectStationIdAsync(connection, code, ct).ConfigureAwait(false);
        if (id is null)
        {
            await using var insert = new NpgsqlCommand(
                "INSERT INTO stations (code, name) VALUES ($1, $1) ON CONFLICT (code) DO NOTHING",
                connection);
            insert.Parameters.AddWithValue(code);
            await insert.ExecuteNonQueryAsync(ct).ConfigureAwait(false);

            id = await SelectStationIdAsync(connection, code, ct).ConfigureAwait(false)
                ?? throw new InvalidOperationException($"station {code} vanished after insert");
        }

        // The batch's dictionary, not the shared one: this id came from an INSERT inside the
        // open transaction, and a rollback would leave a cached id for a row that does not
        // exist and FK-fail every later write for the station.
        batchStationIds[code] = id.Value;
        return id.Value;
    }

    private static async Task<short?> SelectStationIdAsync(
        NpgsqlConnection connection, string code, CancellationToken ct)
    {
        await using var command = new NpgsqlCommand(
            "SELECT id FROM stations WHERE code = $1", connection);
        command.Parameters.AddWithValue(code);
        return await command.ExecuteScalarAsync(ct).ConfigureAwait(false) is short id ? id : null;
    }

    private static async Task<int> UpsertSignalAsync(
        NpgsqlConnection connection, IngestRecord record, JsonElement payload,
        short stationId, CancellationToken ct)
    {
        await using var command = new NpgsqlCommand(
            """
            INSERT INTO signals (station_id, signal, source_ts, value)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (station_id, signal, source_ts) DO NOTHING
            """, connection);
        command.Parameters.AddWithValue(stationId);
        command.Parameters.AddWithValue(Required(payload, "Signal").GetString()!);
        command.Parameters.AddWithValue(record.SourceTs);
        command.Parameters.AddWithValue(Required(payload, "Value").GetDouble());
        return await command.ExecuteNonQueryAsync(ct).ConfigureAwait(false);
    }

    /// <summary>
    /// One PackML transition, from the two data changes that describe it. The plant writes
    /// State and StateReason at the same SourceTimestamp, so this upserts on
    /// (station_id, source_ts) and each arrival sets only the column its own signal carries —
    /// one row, in whichever order the two arrive, and with no buffering here.
    /// </summary>
    /// <remarks>
    /// A transition with no reason is the normal case, not a missing half: StateReason stays
    /// "" through a station's whole bring-up and an unchanged value is never published, so six
    /// state changes per station arrive with no reason data change at all. Nothing here waits
    /// for a pair.
    /// </remarks>
    private async Task<int> UpsertStateChangeAsync(
        NpgsqlConnection connection, IngestRecord record, JsonElement payload,
        short stationId, Dictionary<string, SeenState> batchStates, CancellationToken ct)
    {
        var value = Required(payload, "Value").GetString()!;
        var isState = Required(payload, "Signal").GetString() == StateSignal;

        string? fromState = null;
        string? toState = null;
        string? reason = null;
        short? reasonBufferId = null;

        if (isState)
        {
            toState = value;
            fromState = PreviousStateOf(
                Required(payload, "Station").GetString()!, value, record.SourceTs, batchStates);
        }
        else
        {
            // An empty string is not a reason. It is what the node reads when a station is not
            // suspended, and stored as text it would make every unsuspend look like a
            // condition with a nameless cause.
            if (value.Length == 0)
            {
                // Nothing to write, and writing anyway is worse than writing nothing: every
                // column this arrival could fill is null, so on its own it would key a row
                // that carries no state, no reason and no transition. The paired State
                // normally fills it in the same batch, but a batch boundary with a lost State
                // half would leave that empty row in the table for good.
                return 0;
            }

            reason = value;
            reasonBufferId = await ResolveReasonBufferAsync(connection, value, ct)
                .ConfigureAwait(false);
        }

        await using var command = new NpgsqlCommand(
            """
            INSERT INTO state_changes
              (station_id, source_ts, from_state, to_state, reason, reason_buffer_id)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (station_id, source_ts) DO UPDATE SET
              -- The existing from_state wins: a page-boundary duplicate re-delivers a State
              -- the gateway has already consumed, and by then its own memory says the station
              -- was already there.
              from_state       = COALESCE(state_changes.from_state, EXCLUDED.from_state),
              to_state         = COALESCE(EXCLUDED.to_state, state_changes.to_state),
              reason           = COALESCE(EXCLUDED.reason, state_changes.reason),
              reason_buffer_id = COALESCE(EXCLUDED.reason_buffer_id,
                                          state_changes.reason_buffer_id)
            """, connection);
        command.Parameters.AddWithValue(stationId);
        command.Parameters.AddWithValue(record.SourceTs);
        command.Parameters.AddWithValue(fromState ?? (object)DBNull.Value);
        command.Parameters.AddWithValue(toState ?? (object)DBNull.Value);
        command.Parameters.AddWithValue(reason ?? (object)DBNull.Value);
        command.Parameters.AddWithValue(reasonBufferId ?? (object)DBNull.Value);
        return await command.ExecuteNonQueryAsync(ct).ConfigureAwait(false);
    }

    /// <returns>
    /// The state this station was last seen in, or null when there is no transition to name —
    /// the first state after a connect, a repeat of the state already recorded, and a row that
    /// arrives out of order.
    /// </returns>
    /// <remarks>
    /// Out of order is not hypothetical. A truncated backfill window hands its rows over
    /// before it is halved, and both halves then re-read the range from the start; the memory
    /// standing at the last row of the truncated read would otherwise name it as what the
    /// first row transitioned from. The upsert's COALESCE cannot catch that one, because the
    /// very first State this gateway sees is stored with a null from_state and anything wins
    /// against null. So a row at or before what is remembered establishes nothing about what
    /// preceded it, and resets the memory to itself so the rows after it are right again.
    /// </remarks>
    private string? PreviousStateOf(
        string stationCode, string toState, DateTime sourceTs,
        Dictionary<string, SeenState> batchStates)
    {
        if (!batchStates.TryGetValue(stationCode, out var last))
        {
            _lastStates.TryGetValue(stationCode, out last);
        }

        // A repeat is a page-boundary duplicate rather than a transition: the server drops a
        // data change whose value equals the previous one, so the plant never sends a station
        // into the state it is already in.
        var previous = last is not null && last.SourceTs < sourceTs && last.State != toState
            ? last.State
            : null;

        // The batch's dictionary, promoted only on commit. Advancing the shared one here would
        // survive a rollback, and the retry would then read a memory that has already moved
        // past the record it is re-writing.
        batchStates[stationCode] = new SeenState(toState, sourceTs);
        return previous;
    }

    /// <summary>The last State seen for one station, and when the plant stamped it.</summary>
    private sealed record SeenState(string State, DateTime SourceTs);

    /// <summary>
    /// §3.3 renders a suspend reason as "direction:buffer_id". Resolving it here is what turns
    /// propagation from inferred into verifiable; keeping the text as well is what lets
    /// "starved:carrier-return" — a real condition with no buffer behind it — still store.
    /// </summary>
    private async Task<short?> ResolveReasonBufferAsync(
        NpgsqlConnection connection, string reason, CancellationToken ct)
    {
        var separator = reason.IndexOf(':', StringComparison.Ordinal);
        return separator < 0
            ? null
            : await SelectBufferIdAsync(connection, reason[(separator + 1)..], ct)
                .ConfigureAwait(false);
    }

    /// <summary>
    /// A buffer level, keyed to the buffer it belongs to. The payload names the buffer by the
    /// code topology discovery wrote, because the address space gives a level no station.
    /// </summary>
    /// <exception cref="InvalidOperationException">
    /// The buffer is not in the topology. Unlike a station, a buffer cannot be conjured from
    /// what a level record carries — its two stations and its capacity are read by browsing —
    /// so this is discovery having not run, and it fails loudly rather than dropping the row.
    /// </exception>
    private async Task<int> UpsertBufferLevelAsync(
        NpgsqlConnection connection, IngestRecord record, JsonElement payload,
        CancellationToken ct)
    {
        var code = Required(payload, "Buffer").GetString()!;
        var bufferId = await SelectBufferIdAsync(connection, code, ct).ConfigureAwait(false)
            ?? throw new InvalidOperationException(
                $"buffer {code} is not in the topology; discovery must run before its levels");

        await using var command = new NpgsqlCommand(
            """
            INSERT INTO buffer_levels (buffer_id, source_ts, level)
            VALUES ($1, $2, $3)
            ON CONFLICT (buffer_id, source_ts) DO NOTHING
            """, connection);
        command.Parameters.AddWithValue(bufferId);
        command.Parameters.AddWithValue(record.SourceTs);
        command.Parameters.AddWithValue(CarrierCount(payload));
        return await command.ExecuteNonQueryAsync(ct).ConfigureAwait(false);
    }

    /// <summary>
    /// A level arrives through the same numeric payload as every other data change, and is a
    /// count of carriers. Checked and whole: a plain narrowing cast turns an out-of-range or
    /// fractional value into a SMALLINT that is quietly wrong, which is the one kind of answer
    /// this system exists not to give.
    /// </summary>
    private static short CarrierCount(JsonElement payload)
    {
        var value = Required(payload, "Value").GetDouble();
        return double.IsInteger(value)
            ? checked((short)value)
            : throw new InvalidOperationException($"buffer level {value} is not whole carriers");
    }

    private async Task<short?> SelectBufferIdAsync(
        NpgsqlConnection connection, string code, CancellationToken ct)
    {
        if (_bufferIds.TryGetValue(code, out var cached))
        {
            return cached;
        }

        await using var command = new NpgsqlCommand(
            "SELECT id FROM buffers WHERE code = $1", connection);
        command.Parameters.AddWithValue(code);
        if (await command.ExecuteScalarAsync(ct).ConfigureAwait(false) is not short id)
        {
            return null;
        }

        _bufferIds[code] = id;
        return id;
    }

    private static async Task<int> UpsertInspectionAsync(
        NpgsqlConnection connection, IngestRecord record, JsonElement payload,
        short stationId, CancellationToken ct)
    {
        var serial = Required(payload, "AssemblySerial").GetString()!;
        var hasImage = record.ImageBytes is { Length: > 0 };

        await using var command = new NpgsqlCommand(
            """
            INSERT INTO inspection_results
              (assembly_serial, source_ts, station_id, result, defect_class, confidence,
               model_version, image_ref)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (assembly_serial) DO NOTHING
            """, connection);
        command.Parameters.AddWithValue(serial);
        command.Parameters.AddWithValue(record.SourceTs);
        command.Parameters.AddWithValue(stationId);
        command.Parameters.AddWithValue(Required(payload, "Disposition").GetString()!);
        command.Parameters.AddWithValue(Optional(payload, "DefectClass") ?? (object)DBNull.Value);
        command.Parameters.AddWithValue(
            payload.TryGetProperty("Confidence", out var c) && c.ValueKind == JsonValueKind.Number
                ? c.GetDouble() : (object)DBNull.Value);
        command.Parameters.AddWithValue(Required(payload, "ModelVersion").GetString()!);
        command.Parameters.AddWithValue(hasImage ? serial : (object)DBNull.Value);

        var rows = await command.ExecuteNonQueryAsync(ct).ConfigureAwait(false);
        if (!hasImage)
        {
            // Good parts get a result without an image (§3.4), and that is not a data gap.
            return rows;
        }

        await using var image = new NpgsqlCommand(
            """
            INSERT INTO inspection_images (assembly_serial, bytes) VALUES ($1, $2)
            ON CONFLICT (assembly_serial) DO NOTHING
            """, connection);
        image.Parameters.AddWithValue(serial);
        image.Parameters.AddWithValue(record.ImageBytes!);
        return rows + await image.ExecuteNonQueryAsync(ct).ConfigureAwait(false);
    }

    private static JsonElement Required(JsonElement payload, string name) =>
        payload.TryGetProperty(name, out var value) && value.ValueKind != JsonValueKind.Null
            ? value
            : throw new InvalidOperationException($"record payload has no {name}");

    private static string? Optional(JsonElement payload, string name) =>
        payload.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.String
            ? value.GetString()
            : null;

    private static string ReadMigration(string resource)
    {
        using var stream = Assembly.GetExecutingAssembly()
            .GetManifestResourceStream(resource)
            ?? throw new InvalidOperationException($"{resource} is not embedded");
        using var reader = new StreamReader(stream);
        return reader.ReadToEnd();
    }
}
