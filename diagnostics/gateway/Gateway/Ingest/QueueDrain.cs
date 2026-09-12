using Gateway.Opc;
using Microsoft.Extensions.Logging;
using Npgsql;

namespace Gateway.Ingest;

/// <summary>
/// Moves records from the local queue into Postgres, acking only what committed.
/// </summary>
public sealed partial class QueueDrain
{
    private readonly LocalQueue _queue;
    private readonly PostgresWriter _writer;
    private readonly GatewayOptions _options;
    private readonly ILogger<QueueDrain> _logger;

    public long RowsWritten { get; private set; }

    public QueueDrain(
        LocalQueue queue, PostgresWriter writer, GatewayOptions options, ILogger<QueueDrain> logger)
    {
        _queue = queue;
        _writer = writer;
        _options = options;
        _logger = logger;
    }

    public async Task RunAsync(CancellationToken ct)
    {
        while (!ct.IsCancellationRequested)
        {
            var batch = await _queue.DequeueBatchAsync(_options.DrainBatchSize).ConfigureAwait(false);
            if (batch.Count == 0)
            {
                await Task.Delay(_options.DrainIdleMs, ct).ConfigureAwait(false);
                continue;
            }

            try
            {
                RowsWritten += await _writer
                    .WriteBatchAsync([.. batch.Select(entry => entry.Record)], ct)
                    .ConfigureAwait(false);
                await _queue.AckAsync(batch.Select(entry => entry.Id)).ConfigureAwait(false);
            }
            catch (NpgsqlException e)
            {
                // One of the three places in this system with a real recovery: the local queue
                // exists precisely so that Postgres being unreachable is survivable. Nothing is
                // acked, so the batch is retried; the queue grows and /status shows it growing.
                LogWriteFailed(e);
                await Task.Delay(_options.DrainRetryMs, ct).ConfigureAwait(false);
            }
        }
    }

    [LoggerMessage(
        Level = LogLevel.Warning,
        Message = "postgres write failed; batch retained in the local queue")]
    private partial void LogWriteFailed(Exception exception);
}
