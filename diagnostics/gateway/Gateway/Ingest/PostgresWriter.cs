using System.Collections.Concurrent;
using System.Reflection;
using System.Text.Json;
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
    private const string MigrationResource = "Gateway.Migrations.001_m1.sql";

    private readonly string _connectionString;

    // One station in M1 and four in M2, resolved once each. Without this the lookup runs per
    // record, which is both a round trip per row and — with the ON CONFLICT DO UPDATE this
    // replaced — a sequence value burned per row.
    private readonly ConcurrentDictionary<string, short> _stationIds = new(StringComparer.Ordinal);

    public PostgresWriter(string connectionString) => _connectionString = connectionString;

    public static async Task ApplySchemaAsync(
        string connectionString, CancellationToken ct = default)
    {
        await using var connection = new NpgsqlConnection(connectionString);
        await connection.OpenAsync(ct).ConfigureAwait(false);
        await using var command = new NpgsqlCommand(ReadMigration(), connection);
        await command.ExecuteNonQueryAsync(ct).ConfigureAwait(false);
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

    /// <returns>Rows affected across every table this batch touched.</returns>
    public async Task<int> WriteBatchAsync(
        IReadOnlyList<IngestRecord> batch, CancellationToken ct = default)
    {
        ArgumentNullException.ThrowIfNull(batch);

        await using var connection = new NpgsqlConnection(_connectionString);
        await connection.OpenAsync(ct).ConfigureAwait(false);
        await using var transaction = await connection.BeginTransactionAsync(ct).ConfigureAwait(false);

        var rows = 0;
        foreach (var record in batch)
        {
            rows += await InsertRawAsync(connection, record, ct).ConfigureAwait(false);

            using var document = JsonDocument.Parse(record.PayloadJson);
            var payload = document.RootElement;
            var stationId = await EnsureStationAsync(
                connection, Required(payload, "Station").GetString()!, ct).ConfigureAwait(false);

            rows += record.Kind switch
            {
                "datachange" => await UpsertSignalAsync(connection, record, payload, stationId, ct)
                    .ConfigureAwait(false),
                "event" => await UpsertInspectionAsync(connection, record, payload, stationId, ct)
                    .ConfigureAwait(false),
                _ => 0,
            };
        }

        await transaction.CommitAsync(ct).ConfigureAwait(false);
        return rows;
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
        NpgsqlConnection connection, string code, CancellationToken ct)
    {
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

        _stationIds[code] = id.Value;
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

    private static string ReadMigration()
    {
        using var stream = Assembly.GetExecutingAssembly()
            .GetManifestResourceStream(MigrationResource)
            ?? throw new InvalidOperationException($"{MigrationResource} is not embedded");
        using var reader = new StreamReader(stream);
        return reader.ReadToEnd();
    }
}
