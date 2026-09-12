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

    public PostgresWriter(string connectionString) => _connectionString = connectionString;

    public static async Task ApplySchemaAsync(
        string connectionString, CancellationToken ct = default)
    {
        await using var connection = new NpgsqlConnection(connectionString);
        await connection.OpenAsync(ct).ConfigureAwait(false);
        await using var command = new NpgsqlCommand(ReadMigration(), connection);
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
    private static async Task<short> EnsureStationAsync(
        NpgsqlConnection connection, string code, CancellationToken ct)
    {
        await using var command = new NpgsqlCommand(
            """
            INSERT INTO stations (code, name) VALUES ($1, $1)
            ON CONFLICT (code) DO UPDATE SET code = EXCLUDED.code
            RETURNING id
            """, connection);
        command.Parameters.AddWithValue(code);
        return (short)(await command.ExecuteScalarAsync(ct).ConfigureAwait(false))!;
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
