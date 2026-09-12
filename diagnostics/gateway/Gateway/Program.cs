using Gateway.Ingest;
using Gateway.Opc;
using Gateway.Status;
using Opc.Ua;

var options = GatewayOptions.FromProcessEnvironment();

if (args.Contains(ConnectTest.Flag, StringComparer.Ordinal))
{
    return await ConnectTest.RunAsync(args, options).ConfigureAwait(false);
}

var builder = WebApplication.CreateBuilder(args);
var app = builder.Build();
var logger = app.Logger;

var queue = await LocalQueue.OpenAsync(options.QueuePath).ConfigureAwait(false);

QueueDrain? drain = null;
HistoryBackfill? backfill = null;
PostgresWriter? writer = null;
if (!string.IsNullOrWhiteSpace(options.PostgresConnectionString))
{
    await PostgresWriter.ApplySchemaAsync(options.PostgresConnectionString).ConfigureAwait(false);
    writer = new PostgresWriter(options.PostgresConnectionString);
    drain = new QueueDrain(
        queue,
        writer,
        options,
        app.Services.GetRequiredService<ILoggerFactory>().CreateLogger<QueueDrain>());
}
var connection = new UaConnection(options, DefaultTelemetry.Create(l => l.AddConsole()));

var state = "disconnected";
DateTime? lastEventSourceTs = null;
Subscriptions? subscriptions = null;
connection.StateChanged += next => state = next;

// Backfill arrives in Task 10. Until then BackfillProgress reports what is true — no
// backfill has run — rather than a plausible number.
StatusEndpoint.Map(app, async () => new GatewayStatus(
    State: state,
    LastEventSourceTs: lastEventSourceTs,
    QueueDepth: await queue.DepthAsync().ConfigureAwait(false),
    BackfillProgress: backfill?.Progress ?? 0.0,
    OverflowCount: subscriptions?.OverflowCount ?? 0,
    RowsWritten: drain?.RowsWritten ?? 0,
    HistoryAvailableFrom: null));

var stopping = app.Lifetime.ApplicationStopping;
var ingest = Task.Run(
    async () =>
    {
        var session = await connection.ConnectAsync(stopping).ConfigureAwait(false);
        var space = await AddressSpace.ResolveAsync(session, stopping).ConfigureAwait(false);

        async Task EnqueueAsync(IngestRecord record)
        {
            if (record.Kind == "event")
            {
                lastEventSourceTs = record.SourceTs;
            }

            await queue.EnqueueAsync(record).ConfigureAwait(false);
        }

        // §4.1: stations are browsed, not configured, before anything writes rows that
        // reference them.
        var topology = await TopologyDiscovery.DiscoverAsync(session, stopping).ConfigureAwait(false);
        if (!string.IsNullOrWhiteSpace(options.PostgresConnectionString))
        {
            await TopologyDiscovery
                .UpsertAsync(options.PostgresConnectionString, topology, stopping)
                .ConfigureAwait(false);
        }

        // Drains while backfill runs, or 18 h of history would sit in the local queue waiting
        // for a subscription that has not started yet.
        var draining = drain is null ? Task.CompletedTask : drain.RunAsync(stopping);

        // §4.3: HistoryRead for the past, subscriptions for live, and backfill first — a
        // subscription opened before the plant finishes catching up delivers its history
        // through the live path at ~100 events/second, which is the thing §4.3 exists to
        // avoid. Backfill closes exactly the gap this gateway has.
        backfill = new HistoryBackfill(session, space, EnqueueAsync, options);
        var to = DateTime.UtcNow;
        var from = (writer is null
            ? null
            : await writer.LastStoredSourceTimestampAsync(stopping).ConfigureAwait(false))
            ?? to - options.HistoryDepth;
        await backfill.RunAsync(from, to, stopping).ConfigureAwait(false);

        subscriptions = new Subscriptions(options, EnqueueAsync);
        await subscriptions.StartAsync(session, space, stopping).ConfigureAwait(false);

        await draining.ConfigureAwait(false);
    },
    stopping);

// An unobserved Task exception is silently discarded, which for the one task that does all
// the work would mean a gateway reporting "disconnected" forever with no reason anywhere.
_ = ingest.ContinueWith(
    faulted =>
    {
        // CA1848 wants a LoggerMessage delegate, which needs a partial class that top-level
        // statements cannot host. This fires at most once, on the way down.
#pragma warning disable CA1848
        logger.LogCritical(faulted.Exception, "ingest failed; stopping");
#pragma warning restore CA1848
        app.Lifetime.StopApplication();
    },
    CancellationToken.None,
    TaskContinuationOptions.OnlyOnFaulted,
    TaskScheduler.Default);

await app.RunAsync().ConfigureAwait(false);
await connection.DisposeAsync().ConfigureAwait(false);
await queue.DisposeAsync().ConfigureAwait(false);
return 0;
