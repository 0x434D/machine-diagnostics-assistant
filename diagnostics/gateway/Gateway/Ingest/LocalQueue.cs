using System.Diagnostics.CodeAnalysis;
using System.Globalization;
using Microsoft.Data.Sqlite;

namespace Gateway.Ingest;

/// <summary>
/// The gateway's durable buffer for when Postgres is unreachable (§5.1). A SQLite file on a
/// volume rather than an in-memory channel, so it survives a restart and can be opened
/// mid-outage to prove it is filling.
/// </summary>
[SuppressMessage(
    "Naming", "CA1711:Identifiers should not have incorrect suffix",
    Justification = "A queue in the domain sense, not a System.Collections one. The spec calls "
        + "it the local queue throughout §5.1 and §4.4; renaming the type would cost the "
        + "vocabulary the design document and every operator conversation already use.")]
public sealed class LocalQueue : IAsyncDisposable
{
    // Round-trip format: "O" on a UTC DateTime ends in Z, and RoundtripKind reads it back as
    // Utc rather than as a local time shifted by the host's offset.
    private const string TimestampFormat = "O";

    private readonly SqliteConnection _connection;

    // One SQLite connection, two callers: the subscription's notification callbacks enqueue
    // while /status reads the depth. SqliteConnection does not serialise commands itself.
    private readonly SemaphoreSlim _gate = new(1, 1);

    private LocalQueue(SqliteConnection connection) => _connection = connection;

    public static async Task<LocalQueue> OpenAsync(string path)
    {
        var connection = new SqliteConnection(
            new SqliteConnectionStringBuilder { DataSource = path }.ToString());
        await connection.OpenAsync().ConfigureAwait(false);

        // WAL so a reader can inspect the queue while the gateway is still writing to it.
        await ExecuteAsync(connection, "PRAGMA journal_mode=WAL;").ConfigureAwait(false);
        await ExecuteAsync(connection, """
            CREATE TABLE IF NOT EXISTS queue (
              id           INTEGER PRIMARY KEY AUTOINCREMENT,
              kind         TEXT    NOT NULL,
              node_id      TEXT    NOT NULL,
              source_ts    TEXT    NOT NULL,
              server_ts    TEXT    NOT NULL,
              status_code  INTEGER NOT NULL,
              payload_json TEXT    NOT NULL,
              image_bytes  BLOB
            );
            """).ConfigureAwait(false);

        return new LocalQueue(connection);
    }

    /// <exception cref="ArgumentException">A timestamp is not UTC.</exception>
    public async Task EnqueueAsync(IngestRecord record)
    {
        ArgumentNullException.ThrowIfNull(record);

        // Enforced rather than converted: ToUniversalTime on an Unspecified DateTime assumes
        // local and shifts it silently, which would corrupt every window the analysis reads.
        RequireUtc(record.SourceTs, nameof(record.SourceTs));
        RequireUtc(record.ServerTs, nameof(record.ServerTs));

        await _gate.WaitAsync().ConfigureAwait(false);
        try
        {
            await using var command = _connection.CreateCommand();
            command.CommandText = """
            INSERT INTO queue (kind, node_id, source_ts, server_ts, status_code, payload_json, image_bytes)
            VALUES ($kind, $node_id, $source_ts, $server_ts, $status_code, $payload_json, $image_bytes);
            """;
            command.Parameters.AddWithValue("$kind", record.Kind);
            command.Parameters.AddWithValue("$node_id", record.NodeId);
            command.Parameters.AddWithValue("$source_ts", Format(record.SourceTs));
            command.Parameters.AddWithValue("$server_ts", Format(record.ServerTs));
            command.Parameters.AddWithValue("$status_code", record.StatusCode);
            command.Parameters.AddWithValue("$payload_json", record.PayloadJson);
            command.Parameters.AddWithValue(
                "$image_bytes", (object?)record.ImageBytes ?? DBNull.Value);

            await command.ExecuteNonQueryAsync().ConfigureAwait(false);
        }
        finally
        {
            _gate.Release();
        }
    }

    /// <summary>
    /// Oldest rows first. Insertion order is for draining only — nothing downstream may
    /// assume arrival order (§4.4), which is why SourceTs travels with every row.
    /// </summary>
    public async Task<IReadOnlyList<(long Id, IngestRecord Record)>> DequeueBatchAsync(int max)
    {
        await _gate.WaitAsync().ConfigureAwait(false);
        try
        {
            await using var command = _connection.CreateCommand();
            command.CommandText = "SELECT * FROM queue ORDER BY id LIMIT $max;";
            command.Parameters.AddWithValue("$max", max);

            var batch = new List<(long, IngestRecord)>();
            await using var reader = await command.ExecuteReaderAsync().ConfigureAwait(false);
            while (await reader.ReadAsync().ConfigureAwait(false))
            {
                batch.Add((
                    reader.GetInt64(reader.GetOrdinal("id")),
                    new IngestRecord(
                        Kind: reader.GetString(reader.GetOrdinal("kind")),
                        NodeId: reader.GetString(reader.GetOrdinal("node_id")),
                        SourceTs: Parse(reader.GetString(reader.GetOrdinal("source_ts"))),
                        ServerTs: Parse(reader.GetString(reader.GetOrdinal("server_ts"))),
                        StatusCode: (uint)reader.GetInt64(reader.GetOrdinal("status_code")),
                        PayloadJson: reader.GetString(reader.GetOrdinal("payload_json")),
                        ImageBytes: ReadImage(reader))));
            }

            return batch;
        }
        finally
        {
            _gate.Release();
        }
    }

    public async Task AckAsync(IEnumerable<long> ids)
    {
        ArgumentNullException.ThrowIfNull(ids);
        var list = ids.ToList();
        if (list.Count == 0)
        {
            return;
        }

        await _gate.WaitAsync().ConfigureAwait(false);
        try
        {
            await using var command = _connection.CreateCommand();
            var names = new List<string>(list.Count);
            for (var i = 0; i < list.Count; i++)
            {
                names.Add($"$id{i}");
                command.Parameters.AddWithValue($"$id{i}", list[i]);
            }

            command.CommandText =
                $"DELETE FROM queue WHERE id IN ({string.Join(", ", names)});";
            await command.ExecuteNonQueryAsync().ConfigureAwait(false);
        }
        finally
        {
            _gate.Release();
        }
    }

    public async Task<int> DepthAsync()
    {
        await _gate.WaitAsync().ConfigureAwait(false);
        try
        {
            await using var command = _connection.CreateCommand();
            command.CommandText = "SELECT COUNT(*) FROM queue;";
            return Convert.ToInt32(
                await command.ExecuteScalarAsync().ConfigureAwait(false),
                CultureInfo.InvariantCulture);
        }
        finally
        {
            _gate.Release();
        }
    }

    public async ValueTask DisposeAsync()
    {
        await _connection.DisposeAsync().ConfigureAwait(false);
        _gate.Dispose();
    }

    private static byte[]? ReadImage(SqliteDataReader reader)
    {
        var ordinal = reader.GetOrdinal("image_bytes");
        return reader.IsDBNull(ordinal) ? null : (byte[])reader.GetValue(ordinal);
    }

    private static async Task ExecuteAsync(SqliteConnection connection, string sql)
    {
        await using var command = connection.CreateCommand();
        command.CommandText = sql;
        await command.ExecuteNonQueryAsync().ConfigureAwait(false);
    }

    private static string Format(DateTime value) =>
        value.ToString(TimestampFormat, CultureInfo.InvariantCulture);

    private static DateTime Parse(string value) =>
        DateTime.ParseExact(
            value, TimestampFormat, CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind);

    private static void RequireUtc(DateTime value, string name)
    {
        if (value.Kind != DateTimeKind.Utc)
        {
            throw new ArgumentException($"{name} must be UTC, not {value.Kind}", nameof(value));
        }
    }
}
