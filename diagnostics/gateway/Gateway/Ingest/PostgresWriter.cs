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

    private const string MigrationPrefix = "Gateway.Migrations.";

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

    // One id per (lot code, lane) — ~80 over a whole history (19,800 parts draw 39,600
    // components, at 500 to a lot) against 39,600 component reads, so without this the lookup
    // is a round trip per record. Same rule as the station ids above: an id produced by an
    // INSERT inside the open transaction lives in the batch's own dictionary until the batch
    // commits.
    private readonly ConcurrentDictionary<LotKey, short> _lotIds = new();

    public PostgresWriter(string connectionString) => _connectionString = connectionString;

    /// <summary>
    /// Every migration embedded in this assembly, in the order they must run — 002
    /// references stations, which 001 creates, and 003 references carriers, which 002
    /// creates. Ordinal on the three-digit prefix, which is what the naming convention is
    /// for.
    /// </summary>
    /// <remarks>
    /// Read from the assembly rather than listed: a migration file that exists and is not
    /// listed fails at runtime against a live database, which is the one place a schema
    /// change must not first be noticed. There is nothing here to forget to update.
    /// </remarks>
    /// <exception cref="InvalidOperationException">
    /// a migration is named in a way that gives it no place in the order.
    /// </exception>
    public static IReadOnlyList<string> Migrations()
    {
        var resources = Assembly.GetExecutingAssembly().GetManifestResourceNames()
            .Where(name => name.StartsWith(MigrationPrefix, StringComparison.Ordinal))
            .Order(StringComparer.Ordinal)
            .ToList();

        var unnumbered = resources
            .Where(name => !IsNumbered(name[MigrationPrefix.Length..]))
            .ToList();

        return unnumbered.Count == 0
            ? resources
            : throw new InvalidOperationException(
                $"{string.Join(", ", unnumbered)} do not start with a three-digit sequence "
                + "number, so the order migrations run in is not stated by their names");
    }

    public static async Task ApplySchemaAsync(
        string connectionString, CancellationToken ct = default)
    {
        await using var connection = new NpgsqlConnection(connectionString);
        await connection.OpenAsync(ct).ConfigureAwait(false);
        foreach (var resource in Migrations())
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
        var batchLotIds = new Dictionary<LotKey, short>();

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
                "event" => await WriteEventAsync(
                    connection, record, payload, stationId, batchLotIds, ct).ConfigureAwait(false),
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

        foreach (var (lot, id) in batchLotIds)
        {
            _lotIds[lot] = id;
        }

        return rows;
    }

    /// <summary>
    /// One of §4.1's five event types, into the tables §5.2 gives it.
    ///
    /// <para>Routed on the type the event names, never on which fields happen to be
    /// present: a good part's PartCompletedEvent carries no reason and an
    /// InspectionResultEvent carries no image, so "which columns are filled" is a different
    /// question from "which type is this" and answering one with the other would put a press
    /// record in the genealogy.</para>
    /// </summary>
    /// <exception cref="InvalidOperationException">
    /// the plant published a type this gateway has no table for. Loud, because the
    /// alternative is a stream that ingests into nothing while /reconcile counts it read.
    /// </exception>
    private async Task<int> WriteEventAsync(
        NpgsqlConnection connection, IngestRecord record, JsonElement payload, short stationId,
        Dictionary<LotKey, short> batchLotIds, CancellationToken ct)
    {
        var eventType = Required(payload, PlantEvents.EventTypeField).GetString()!;

        if (eventType == PlantEvents.ComponentRead.TypeName)
        {
            return await InsertComponentReadAsync(
                connection, record, payload, batchLotIds, ct).ConfigureAwait(false);
        }

        if (eventType == PlantEvents.AssemblyCreated.TypeName)
        {
            return await InsertAssemblyCreatedAsync(connection, record, payload, ct)
                .ConfigureAwait(false);
        }

        if (eventType == PlantEvents.PartProcessed.TypeName)
        {
            return await InsertPartProcessedAsync(connection, record, payload, stationId, ct)
                .ConfigureAwait(false);
        }

        if (eventType == PlantEvents.InspectionResult.TypeName)
        {
            return await UpsertInspectionAsync(connection, record, payload, stationId, ct)
                .ConfigureAwait(false);
        }

        if (eventType == PlantEvents.PartCompleted.TypeName)
        {
            return await InsertPartDispositionAsync(connection, record, payload, ct)
                .ConfigureAwait(false);
        }

        throw new InvalidOperationException(
            $"event type '{eventType}' has no write path; §5.2 gives this gateway a table for "
            + $"{string.Join(", ", PlantEvents.All.Select(type => type.TypeName))}");
    }

    /// <summary>
    /// §5.2's <c>components</c> row and the lot it belongs to.
    /// </summary>
    private async Task<int> InsertComponentReadAsync(
        NpgsqlConnection connection, IngestRecord record, JsonElement payload,
        Dictionary<LotKey, short> batchLotIds, CancellationToken ct)
    {
        var lane = SmallInt(payload, "Lane");
        var lot = new LotKey(Required(payload, "LotCode").GetString()!, lane);
        var lotId = await EnsureLotAsync(
            connection, lot, Required(payload, "Supplier").GetString()!, record.SourceTs,
            batchLotIds, ct).ConfigureAwait(false);

        // DO UPDATE rather than DO NOTHING, and safe here: components.serial is a TEXT
        // primary key with no sequence behind it, so nothing is burned by an upsert that
        // finds the row. What it fills is a stub row left by an AssemblyCreatedEvent that
        // named this component before its own read arrived.
        await using var command = new NpgsqlCommand(
            """
            INSERT INTO components (serial, lot_id, lane, read_at) VALUES ($1, $2, $3, $4)
            ON CONFLICT (serial) DO UPDATE
              SET lot_id = EXCLUDED.lot_id, lane = EXCLUDED.lane, read_at = EXCLUDED.read_at
            """, connection);
        command.Parameters.AddWithValue(Required(payload, "ComponentSerial").GetString()!);
        command.Parameters.AddWithValue(lotId);
        command.Parameters.AddWithValue(lane);
        command.Parameters.AddWithValue(record.SourceTs);
        return await command.ExecuteNonQueryAsync(ct).ConfigureAwait(false);
    }

    /// <summary>
    /// §5.2's <c>assemblies</c> row, the carrier it rode, and §3.4a's as-built genealogy.
    /// </summary>
    /// <remarks>
    /// The component stubs below are what makes genealogy writable whatever order the two
    /// S1 event types arrive in. A component named here whose own read never arrives keeps a
    /// row with no lot, no lane and no instant, which is the true statement about it — the
    /// alternative is a foreign-key violation on a record the queue can never acknowledge.
    /// </remarks>
    private static async Task<int> InsertAssemblyCreatedAsync(
        NpgsqlConnection connection, IngestRecord record, JsonElement payload,
        CancellationToken ct)
    {
        var serial = Required(payload, "AssemblySerial").GetString()!;
        var carrierId = SmallInt(payload, "CarrierId");
        var rows = await EnsureCarrierAsync(connection, carrierId, ct).ConfigureAwait(false);

        await using (var assembly = new NpgsqlCommand(
            """
            INSERT INTO assemblies (serial, created_at, carrier_id) VALUES ($1, $2, $3)
            ON CONFLICT (serial) DO UPDATE
              SET created_at = EXCLUDED.created_at, carrier_id = EXCLUDED.carrier_id
            """, connection))
        {
            assembly.Parameters.AddWithValue(serial);
            assembly.Parameters.AddWithValue(record.SourceTs);
            assembly.Parameters.AddWithValue(carrierId);
            rows += await assembly.ExecuteNonQueryAsync(ct).ConfigureAwait(false);
        }

        var position = (short)0;
        foreach (var component in Required(payload, "ComponentSerials").EnumerateArray())
        {
            var componentSerial = component.GetString()
                ?? throw new InvalidOperationException(
                    $"assembly {serial} names a component with no serial");

            await using (var stub = new NpgsqlCommand(
                "INSERT INTO components (serial) VALUES ($1) ON CONFLICT (serial) DO NOTHING",
                connection))
            {
                stub.Parameters.AddWithValue(componentSerial);
                rows += await stub.ExecuteNonQueryAsync(ct).ConfigureAwait(false);
            }

            await using var link = new NpgsqlCommand(
                """
                INSERT INTO genealogy (assembly_serial, component_serial, position)
                VALUES ($1, $2, $3)
                ON CONFLICT (assembly_serial, component_serial) DO NOTHING
                """, connection);
            link.Parameters.AddWithValue(serial);
            link.Parameters.AddWithValue(componentSerial);

            // The component's index in the event's array, which is the only thing that
            // orders it (§5.2's genealogy.position). Not the lane: a lane is a feeder and a
            // position is a place in the assembly, and the two agreeing today is the plant's
            // arrangement rather than a rule this gateway may lean on.
            link.Parameters.AddWithValue(position++);
            rows += await link.ExecuteNonQueryAsync(ct).ConfigureAwait(false);
        }

        return rows;
    }

    /// <summary>
    /// §3.4a's press record: the two scalars as <c>part_process_values</c>, and D6's curve
    /// as one array row rather than one row per sample.
    /// </summary>
    private static async Task<int> InsertPartProcessedAsync(
        NpgsqlConnection connection, IngestRecord record, JsonElement payload, short stationId,
        CancellationToken ct)
    {
        var serial = Required(payload, "AssemblySerial").GetString()!;
        var rows = await EnsureAssemblyAsync(connection, serial, ct).ConfigureAwait(false);

        // The event's own field names, not the historised streams' (JoiningForcePeak).
        // §5.2's part_process_values.signal names what the per-part record carries, and
        // translating between the two vocabularies here would be a mapping nothing else in
        // the system states.
        foreach (var signal in new[] { "PeakForce", "JoiningDistance" })
        {
            await using var value = new NpgsqlCommand(
                """
                INSERT INTO part_process_values (assembly_serial, station_id, signal, value)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (assembly_serial, station_id, signal) DO NOTHING
                """, connection);
            value.Parameters.AddWithValue(serial);
            value.Parameters.AddWithValue(stationId);
            value.Parameters.AddWithValue(signal);
            value.Parameters.AddWithValue(Required(payload, signal).GetDouble());
            rows += await value.ExecuteNonQueryAsync(ct).ConfigureAwait(false);
        }

        await using var curve = new NpgsqlCommand(
            """
            INSERT INTO part_process_curves (assembly_serial, station_id, signal, samples)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (assembly_serial, station_id, signal) DO NOTHING
            """, connection);
        curve.Parameters.AddWithValue(serial);
        curve.Parameters.AddWithValue(stationId);
        curve.Parameters.AddWithValue("Curve");
        curve.Parameters.AddWithValue(Doubles(payload, "Curve"));
        return rows + await curve.ExecuteNonQueryAsync(ct).ConfigureAwait(false);
    }

    /// <summary>§5.2's <c>part_dispositions</c>: how the part left the line, and why.</summary>
    private static async Task<int> InsertPartDispositionAsync(
        NpgsqlConnection connection, IngestRecord record, JsonElement payload,
        CancellationToken ct)
    {
        var serial = Required(payload, "AssemblySerial").GetString()!;
        var rows = await EnsureAssemblyAsync(connection, serial, ct).ConfigureAwait(false);

        await using var command = new NpgsqlCommand(
            """
            INSERT INTO part_dispositions (assembly_serial, at, disposition, reason)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (assembly_serial) DO NOTHING
            """, connection);
        command.Parameters.AddWithValue(serial);
        command.Parameters.AddWithValue(record.SourceTs);
        command.Parameters.AddWithValue(Required(payload, "Disposition").GetString()!);

        // A good part carries no reason, and the decoder has already turned the plant's
        // empty string into an absent field rather than a nameless cause.
        command.Parameters.AddWithValue(Optional(payload, "Reason") ?? (object)DBNull.Value);
        return rows + await command.ExecuteNonQueryAsync(ct).ConfigureAwait(false);
    }

    /// <summary>
    /// The row every per-part table keys on, created by whichever event names the serial
    /// first. It asserts only what the event proves — that an assembly by this serial was on
    /// the line — and leaves <c>created_at</c> and <c>carrier_id</c> to the event that
    /// actually carries them.
    /// </summary>
    private static async Task<int> EnsureAssemblyAsync(
        NpgsqlConnection connection, string serial, CancellationToken ct)
    {
        await using var command = new NpgsqlCommand(
            "INSERT INTO assemblies (serial) VALUES ($1) ON CONFLICT (serial) DO NOTHING",
            connection);
        command.Parameters.AddWithValue(serial);
        return await command.ExecuteNonQueryAsync(ct).ConfigureAwait(false);
    }

    /// <summary>
    /// §5.2's <c>carriers</c>, which M2a created empty because the address space exposes no
    /// carrier nodes. The events carry the id, so this is where the table fills.
    /// </summary>
    private static async Task<int> EnsureCarrierAsync(
        NpgsqlConnection connection, short carrierId, CancellationToken ct)
    {
        // INSERT ... SELECT ... WHERE NOT EXISTS, the shape topology discovery established.
        // carriers.id is a plain SMALLINT with no sequence behind it, so nothing would be
        // burned by ON CONFLICT here — but one shape for "create it if the line has not
        // mentioned it before" is what keeps the next table that does have a SMALLSERIAL
        // from being written the other way by habit.
        await using var command = new NpgsqlCommand(
            """
            INSERT INTO carriers (id)
            SELECT $1 WHERE NOT EXISTS (SELECT 1 FROM carriers WHERE id = $1)
            """, connection);
        command.Parameters.AddWithValue(carrierId);
        return await command.ExecuteNonQueryAsync(ct).ConfigureAwait(false);
    }

    /// <summary>
    /// The lot one component was drawn from, created on first sight.
    /// <c>loaded_at</c> is the earliest draw seen, which a backfill reading windows out of
    /// order can only improve on.
    /// </summary>
    private async Task<short> EnsureLotAsync(
        NpgsqlConnection connection, LotKey lot, string supplier, DateTime readAt,
        Dictionary<LotKey, short> batchLotIds, CancellationToken ct)
    {
        if (!batchLotIds.TryGetValue(lot, out var id) && !_lotIds.TryGetValue(lot, out id))
        {
            // Selected before inserting, and never ON CONFLICT: component_lots.id is a
            // SMALLSERIAL, and both DO UPDATE and DO NOTHING evaluate nextval before the
            // conflict is detected. A history holds ~39,600 component reads against ~80 lots,
            // so an upsert per record would exhaust SMALLSERIAL's 32,767 range and fail every
            // write after it — the defect M1 measured at 55,956 records.
            var existing = await SelectLotIdAsync(connection, lot, ct).ConfigureAwait(false);
            if (existing is null)
            {
                await using var insert = new NpgsqlCommand(
                    """
                    INSERT INTO component_lots (lot_code, lane, supplier, loaded_at)
                    SELECT $1, $2, $3, $4
                    WHERE NOT EXISTS (
                      SELECT 1 FROM component_lots WHERE lot_code = $1 AND lane = $2)
                    """, connection);
                insert.Parameters.AddWithValue(lot.LotCode);
                insert.Parameters.AddWithValue(lot.Lane);
                insert.Parameters.AddWithValue(supplier);
                insert.Parameters.AddWithValue(readAt);
                await insert.ExecuteNonQueryAsync(ct).ConfigureAwait(false);

                existing = await SelectLotIdAsync(connection, lot, ct).ConfigureAwait(false)
                    ?? throw new InvalidOperationException(
                        $"lot {lot.LotCode} on lane {lot.Lane} vanished after insert");
            }

            // The batch's dictionary, promoted only on commit: this id may have come from an
            // INSERT inside the open transaction, and SMALLSERIAL does not roll back with it.
            id = existing.Value;
            batchLotIds[lot] = id;
        }

        await using var earliest = new NpgsqlCommand(
            "UPDATE component_lots SET loaded_at = $2 WHERE id = $1 AND loaded_at > $2",
            connection);
        earliest.Parameters.AddWithValue(id);
        earliest.Parameters.AddWithValue(readAt);
        await earliest.ExecuteNonQueryAsync(ct).ConfigureAwait(false);

        return id;
    }

    private static async Task<short?> SelectLotIdAsync(
        NpgsqlConnection connection, LotKey lot, CancellationToken ct)
    {
        await using var command = new NpgsqlCommand(
            "SELECT id FROM component_lots WHERE lot_code = $1 AND lane = $2", connection);
        command.Parameters.AddWithValue(lot.LotCode);
        command.Parameters.AddWithValue(lot.Lane);
        return await command.ExecuteScalarAsync(ct).ConfigureAwait(false) is short id ? id : null;
    }

    /// <summary>One supplier lot on one lane, which is what §5.2 keys component_lots on.</summary>
    private sealed record LotKey(string LotCode, short Lane);

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
    /// Stations are discovered, not configured (§4.1). This path knows a station only by the
    /// code its signals carry, so it inserts the code as the name; the browsed name is
    /// <see cref="TopologyDiscovery.UpsertAsync"/>'s, written in its own transaction and
    /// overwriting this placeholder whichever of the two runs first. <c>function</c> and
    /// <c>position_in_line</c> are still nullable and still unfilled — nothing browses them.
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

    /// <summary>
    /// §3.4's verdict, at D11's widened shape.
    /// </summary>
    /// <remarks>
    /// <c>defect_class</c> is not written and is not in the statement below. The widened
    /// event carries no scalar class — §3.4 needs six independent scores that do not sum to
    /// 1, and picking an argmax here would be this gateway deciding which defect a part has.
    /// <c>positions</c> has no source at all yet. Both columns are left for Task 7, which
    /// moves <c>/inspection/stats</c> off the scalar.
    /// </remarks>
    private static async Task<int> UpsertInspectionAsync(
        NpgsqlConnection connection, IngestRecord record, JsonElement payload,
        short stationId, CancellationToken ct)
    {
        var serial = Required(payload, "AssemblySerial").GetString()!;
        var hasImage = record.ImageBytes is { Length: > 0 };
        var carrierId = SmallInt(payload, "CarrierId");
        var rows = await EnsureCarrierAsync(connection, carrierId, ct).ConfigureAwait(false);

        // inspection_results.assembly_serial is the one per-part key with no foreign key to
        // assemblies, which is exactly why this call is easy to leave out and impossible to
        // notice: a verdict whose part has no row here fails nothing. It leaves a serial that
        // inspection_results knows and assemblies does not, and §14's trace starts from
        // assemblies — so the part is omitted from its own history rather than answered with
        // what is known about it.
        rows += await EnsureAssemblyAsync(connection, serial, ct).ConfigureAwait(false);

        await using var command = new NpgsqlCommand(
            """
            INSERT INTO inspection_results
              (assembly_serial, source_ts, station_id, result, confidence, model_version,
               image_ref, carrier_id, defect_classes, confidences)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            ON CONFLICT (assembly_serial) DO NOTHING
            """, connection);
        command.Parameters.AddWithValue(serial);
        command.Parameters.AddWithValue(record.SourceTs);
        command.Parameters.AddWithValue(stationId);
        command.Parameters.AddWithValue(Required(payload, "Disposition").GetString()!);
        command.Parameters.AddWithValue(
            payload.TryGetProperty("Confidence", out var c) && c.ValueKind == JsonValueKind.Number
                ? c.GetDouble() : (object)DBNull.Value);
        command.Parameters.AddWithValue(Required(payload, "ModelVersion").GetString()!);
        command.Parameters.AddWithValue(hasImage ? serial : (object)DBNull.Value);
        command.Parameters.AddWithValue(carrierId);
        command.Parameters.AddWithValue(Strings(payload, "DefectClasses"));
        command.Parameters.AddWithValue(Doubles(payload, "Confidences"));

        rows += await command.ExecuteNonQueryAsync(ct).ConfigureAwait(false);
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

    /// <summary>
    /// A field that has to fit a SMALLINT column, checked rather than cast. The plant
    /// publishes lanes and carrier ids as UInt32, and a plain narrowing cast turns one out
    /// of range into a SMALLINT that is quietly wrong — the one kind of answer this system
    /// exists not to give.
    /// </summary>
    private static short SmallInt(JsonElement payload, string name)
    {
        var value = Required(payload, name).GetDouble();
        return double.IsInteger(value) && value is >= short.MinValue and <= short.MaxValue
            ? (short)value
            : throw new InvalidOperationException(
                $"{name} read as {value}, which no SMALLINT column holds");
    }

    private static string[] Strings(JsonElement payload, string name) =>
        [.. Required(payload, name).EnumerateArray()
            .Select(item => item.GetString()
                ?? throw new InvalidOperationException($"{name} carries a null entry"))];

    private static double[] Doubles(JsonElement payload, string name) =>
        [.. Required(payload, name).EnumerateArray().Select(item => item.GetDouble())];

    /// <summary>"003_m2b.sql" — the prefix that gives a migration its place in the order.</summary>
    private static bool IsNumbered(string fileName) =>
        fileName.Length > 3 && fileName[..3].All(char.IsAsciiDigit) && fileName[3] == '_';

    private static string ReadMigration(string resource)
    {
        using var stream = Assembly.GetExecutingAssembly()
            .GetManifestResourceStream(resource)
            ?? throw new InvalidOperationException($"{resource} is not embedded");
        using var reader = new StreamReader(stream);
        return reader.ReadToEnd();
    }
}
