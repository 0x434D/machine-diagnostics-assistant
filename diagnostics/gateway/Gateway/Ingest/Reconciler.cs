using Npgsql;

namespace Gateway.Ingest;

public sealed record StreamReconciliation(
    string Stream, int RowsReturned, int RowsStored, int Pages)
{
    /// <summary>
    /// What the historian handed over beyond what was stored. F2: the continuation point is
    /// the SourceTimestamp of the first row of the next page and the next query re-includes
    /// it, so roughly one duplicate arrives per page boundary. The upsert absorbs them.
    /// </summary>
    public int DuplicatesAbsorbed => RowsReturned - RowsStored;

    /// <summary>
    /// Duplicates expected from F2 alone: one per page boundary, and a window's final page has
    /// no boundary after it. Reported rather than asserted — judging the margin is the
    /// measurement runner's job, and a comparison buried here would be a threshold nobody sees.
    /// </summary>
    public int ExpectedFromPageBoundaries(int windows) => Math.Max(0, Pages - windows);
}

public sealed record ReconciliationResult(
    DateTime From, DateTime To, IReadOnlyList<StreamReconciliation> Streams)
{
    public bool Reconciled => Streams.All(stream => stream.DuplicatesAbsorbed >= 0);
}

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

        var streams = new List<StreamReconciliation>
        {
            await ForSignalAsync(connection, "TaktTime", from, to, ct).ConfigureAwait(false),
            await ForSignalAsync(connection, "PartCount", from, to, ct).ConfigureAwait(false),
            await ForEventsAsync(connection, from, to, ct).ConfigureAwait(false),
        };

        return new ReconciliationResult(from, to, streams);
    }

    private static async Task<StreamReconciliation> ForSignalAsync(
        NpgsqlConnection connection, string signal, DateTime from, DateTime to,
        CancellationToken ct)
    {
        var (returned, pages) = await LedgerAsync(connection, signal, from, to, ct)
            .ConfigureAwait(false);

        await using var command = new NpgsqlCommand(
            "SELECT count(*) FROM signals WHERE signal = $1 AND source_ts >= $2 AND source_ts < $3",
            connection);
        command.Parameters.AddWithValue(signal);
        command.Parameters.AddWithValue(from);
        command.Parameters.AddWithValue(to);
        var stored = Convert.ToInt32(await command.ExecuteScalarAsync(ct).ConfigureAwait(false));

        return new StreamReconciliation(signal, returned, stored, pages);
    }

    private static async Task<StreamReconciliation> ForEventsAsync(
        NpgsqlConnection connection, DateTime from, DateTime to, CancellationToken ct)
    {
        var (returned, pages) = await LedgerAsync(connection, "InspectionResult", from, to, ct)
            .ConfigureAwait(false);

        await using var command = new NpgsqlCommand(
            "SELECT count(*) FROM inspection_results WHERE source_ts >= $1 AND source_ts < $2",
            connection);
        command.Parameters.AddWithValue(from);
        command.Parameters.AddWithValue(to);
        var stored = Convert.ToInt32(await command.ExecuteScalarAsync(ct).ConfigureAwait(false));

        return new StreamReconciliation("InspectionResult", returned, stored, pages);
    }

    private static async Task<(int Returned, int Pages)> LedgerAsync(
        NpgsqlConnection connection, string stream, DateTime from, DateTime to,
        CancellationToken ct)
    {
        await using var command = new NpgsqlCommand(
            "SELECT coalesce(sum(rows_returned), 0), coalesce(sum(pages), 0) "
            + "FROM backfill_windows WHERE stream = $1 AND from_ts >= $2 AND to_ts <= $3",
            connection);
        command.Parameters.AddWithValue(stream);
        command.Parameters.AddWithValue(from);
        command.Parameters.AddWithValue(to);

        await using var reader = await command.ExecuteReaderAsync(ct).ConfigureAwait(false);
        await reader.ReadAsync(ct).ConfigureAwait(false);
        return (Convert.ToInt32(reader.GetValue(0)), Convert.ToInt32(reader.GetValue(1)));
    }
}
